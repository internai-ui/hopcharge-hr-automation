"""
scoring/router.py — All HTTP endpoints for the AI Scoring feature.

Mounted via:  app.include_router(scoring_router)

  AI Settings (Feature 1)
    GET   /api/ai/feature           whether AI-based (external LLM) scoring is enabled
    POST  /api/ai/feature           enable/disable it (persisted, off by default)
    GET   /api/ai/config            current config (NO key)
    POST  /api/ai/config            save provider/model/temp/(key) — 403 while disabled
    POST  /api/ai/test              test connection — 403 while disabled
    GET   /api/ai/models            available providers + models

  Rubrics (Feature 5)
    GET   /api/ai/rubrics           list roles
    GET   /api/ai/rubrics/{key}     full rubric
    PUT   /api/ai/rubrics/{key}     upsert rubric

  Scoring (Features 3,4,7)
    POST  /api/ai/score/{response_id}   score one candidate
    POST  /api/ai/score-all             bulk score pending

  Analytics + audit (Features 8,9)
    GET   /api/ai/analytics
    GET   /api/ai/audit
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from scoring import config_store, rubrics, engine, analytics, audit
from scoring.providers import get_provider

logger = logging.getLogger("volt_cv.scoring")

router = APIRouter(prefix="/api/ai", tags=["ai-scoring"])


# ──────────────────────────────────────────────
# Pydantic models
# ──────────────────────────────────────────────

class ConfigBody(BaseModel):
    provider: str = Field(..., pattern="^(anthropic|openai|gemini|groq|huggingface)$")
    model: str
    temperature: float = Field(..., ge=0.0, le=1.0)
    api_key: str | None = None   # optional on update


class FeatureBody(BaseModel):
    enabled: bool


def _require_ai_feature_enabled():
    if not config_store.is_feature_enabled():
        raise HTTPException(
            status_code=403,
            detail="AI-based scoring is disabled. Enable it in Scoring Mode settings first.",
        )


# ──────────────────────────────────────────────
# Feature toggle — master on/off switch for external LLM providers
# ──────────────────────────────────────────────

@router.get("/feature")
async def get_feature():
    return {"success": True, "enabled": config_store.is_feature_enabled()}


@router.post("/feature")
async def set_feature(body: FeatureBody):
    cfg = config_store.set_feature_enabled(body.enabled)
    return {"success": True, "enabled": cfg["ai_feature_enabled"]}


# ──────────────────────────────────────────────
# Feature 1 — AI Settings
# ──────────────────────────────────────────────

@router.get("/models")
async def get_models():
    return {"success": True, "providers": config_store.MODELS}


@router.get("/config")
async def get_config():
    """Returns the frontend-safe config — never the API key."""
    return {"success": True, "config": config_store.public_config()}


@router.post("/config")
async def save_config(body: ConfigBody):
    # Switching to offline (api_key == "") is always allowed even while the
    # feature is off, so a previously-off toggle can never get "stuck" with a
    # dangling key. Only configuring/keeping a *real* provider requires the
    # feature to be enabled first.
    if body.api_key != "":
        _require_ai_feature_enabled()
    try:
        # If api_key is an empty string "", user is explicitly removing it (switching offline)
        clear = body.api_key == ""
        new_key = body.api_key if body.api_key and body.api_key.strip() else None
        cfg = config_store.save_config(
            provider=body.provider, model=body.model,
            temperature=body.temperature, api_key=new_key,
            clear_key=clear,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "config": cfg}


@router.post("/test")
async def test_connection():
    """
    Validate the *currently saved* configuration. Returns a structured result:
    status is one of ok | invalid_key | no_credits | rate_limited |
    model_not_found | network_error | bad_response | unknown.
    """
    _require_ai_feature_enabled()
    provider = get_provider(config_store.get_runtime_config())
    result = provider.validate()
    return {"success": result.ok, "provider": provider.name, **result.to_dict()}


class ValidateBody(BaseModel):
    provider: str = Field(..., pattern="^(anthropic|openai|gemini|groq|huggingface)$")
    model: str
    api_key: str
    temperature: float = Field(0.2, ge=0.0, le=1.0)


@router.post("/validate")
async def validate_key(body: ValidateBody):
    """
    Test a key/model BEFORE saving it. Lets the UI check credits/validity without
    persisting anything. Builds a provider directly from the posted values.
    """
    _require_ai_feature_enabled()
    from scoring.providers import (ClaudeProvider, OpenAIProvider, GeminiProvider,
                                   GroqProvider, HuggingFaceProvider)
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required to validate.")
    _classes = {"anthropic": ClaudeProvider, "openai": OpenAIProvider,
                "gemini": GeminiProvider, "groq": GroqProvider,
                "huggingface": HuggingFaceProvider}
    cls = _classes[body.provider]
    provider = cls(body.model, body.temperature, body.api_key.strip())
    result = provider.validate()
    return {"success": result.ok, "provider": provider.name, **result.to_dict()}


# ──────────────────────────────────────────────
# Feature 5 — Rubrics
# ──────────────────────────────────────────────

@router.get("/rubrics")
async def list_rubrics():
    return {"success": True, "roles": rubrics.list_roles()}


@router.get("/rubrics/{role_key}")
async def get_rubric(role_key: str):
    r = rubrics.get_rubric(role_key)
    if r is None:
        raise HTTPException(status_code=404, detail=f"Rubric '{role_key}' not found.")
    return {"success": True, "rubric": r}


@router.put("/rubrics/{role_key}")
async def put_rubric(role_key: str, rubric: dict):
    saved = rubrics.upsert_rubric(role_key, rubric)
    return {"success": True, "rubric": saved}


# ──────────────────────────────────────────────
# Features 3, 4, 7 — Scoring
# ──────────────────────────────────────────────

@router.post("/score/{response_id}")
async def score_one(response_id: str):
    if not audit.scoring_limiter.acquire():
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again shortly.")
    try:
        result = engine.score_response(response_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Scoring failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Scoring failed: {exc}")
    # If this candidate now qualifies, auto-move them into HR Round.
    try:
        from accepted_store import auto_move_qualified
        auto_move_qualified()
    except Exception as exc:
        logger.warning("Auto-move after scoring failed: %s", exc)
    return {"success": True, "result": result}


@router.post("/score-all")
async def score_all():
    if not audit.scoring_limiter.acquire():
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again shortly.")
    summary = engine.score_all_pending()
    # After scoring, auto-move candidates above the score threshold into HR Round.
    try:
        from accepted_store import auto_move_qualified
        summary["auto_moved"] = auto_move_qualified()
    except Exception as exc:
        logger.warning("Auto-move after scoring failed: %s", exc)
    return {"success": True, **summary}


# ──────────────────────────────────────────────
# Features 8, 9 — Analytics + audit
# ──────────────────────────────────────────────

@router.get("/analytics")
async def get_analytics():
    return {"success": True, **analytics.analytics()}


@router.get("/audit")
async def get_audit(limit: int = 200):
    return {"success": True, "entries": audit.read_audit(limit)}
