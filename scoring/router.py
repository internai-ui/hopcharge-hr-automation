"""
scoring/router.py — AI provider connection endpoints (shared by CV parsing).

Mounted via:  app.include_router(scoring_router)

    GET   /api/ai/feature           whether AI-based parsing is enabled
    POST  /api/ai/feature           enable/disable it (persisted, off by default)
    GET   /api/ai/config            current config (NO key)
    POST  /api/ai/config            save provider/model/temp/(key) — 403 while disabled
    POST  /api/ai/test              test connection — 403 while disabled
    POST  /api/ai/validate          test a key/model before saving
    GET   /api/ai/models            available providers + models

Candidate/form-response scoring has been removed from this app entirely —
this module now exists solely to configure the AI provider ai_resume_parser.py
uses for CV parsing. The name "scoring" is legacy; kept to avoid an unrelated
rename churning imports elsewhere.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from scoring import config_store
from scoring.providers import get_provider

logger = logging.getLogger("volt_cv.scoring")

router = APIRouter(prefix="/api/ai", tags=["ai-provider"])


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
            detail="AI-based parsing is disabled. Enable it in Admin Settings first.",
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
# Provider configuration
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
    if provider is None:
        raise HTTPException(status_code=400, detail="No provider configured yet. Save a provider and API key first.")
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
