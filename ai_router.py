"""
ai_router.py — HTTP endpoints for AI Settings (used for AI CV parsing).

Mounted in app.py via app.include_router(ai_router)
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import ai_config_store
from ai_providers import get_provider

logger = logging.getLogger("volt_cv.ai_router")

router = APIRouter(prefix="/api/ai", tags=["ai-settings"])


class ConfigBody(BaseModel):
    provider: str = Field(..., pattern="^(anthropic|openai|gemini|groq|huggingface)$")
    model: str
    temperature: float = Field(..., ge=0.0, le=1.0)
    api_key: str | None = None


class FeatureBody(BaseModel):
    enabled: bool


class ValidateBody(BaseModel):
    provider: str = Field(..., pattern="^(anthropic|openai|gemini|groq|huggingface)$")
    model: str
    api_key: str
    temperature: float = Field(0.2, ge=0.0, le=1.0)


def _require_ai_feature_enabled():
    if not ai_config_store.is_feature_enabled():
        raise HTTPException(
            status_code=403,
            detail="AI-based features are disabled. Enable AI in Admin Settings first.",
        )


@router.get("/feature")
async def get_feature():
    return {"success": True, "enabled": ai_config_store.is_feature_enabled()}


@router.post("/feature")
async def set_feature(body: FeatureBody):
    cfg = ai_config_store.set_feature_enabled(body.enabled)
    return {"success": True, "enabled": cfg["ai_feature_enabled"]}


@router.get("/models")
async def get_models():
    return {"success": True, "providers": ai_config_store.MODELS}


@router.get("/config")
async def get_config():
    return {"success": True, "config": ai_config_store.public_config()}


@router.post("/config")
async def save_config(body: ConfigBody):
    if body.api_key != "":
        _require_ai_feature_enabled()
    try:
        clear = body.api_key == ""
        new_key = body.api_key if body.api_key and body.api_key.strip() else None
        cfg = ai_config_store.save_config(
            provider=body.provider,
            model=body.model,
            temperature=body.temperature,
            api_key=new_key,
            clear_key=clear,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "config": cfg}


@router.post("/test")
async def test_connection():
    _require_ai_feature_enabled()
    provider = get_provider(ai_config_store.get_runtime_config())
    result = provider.validate()
    return {"success": result.ok, "provider": provider.name, **result.to_dict()}


@router.post("/validate")
async def validate_key(body: ValidateBody):
    _require_ai_feature_enabled()
    from ai_providers import (ClaudeProvider, OpenAIProvider, GeminiProvider,
                              GroqProvider, HuggingFaceProvider)
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required to validate.")
    _classes = {
        "anthropic": ClaudeProvider, "openai": OpenAIProvider,
        "gemini": GeminiProvider, "groq": GroqProvider,
        "huggingface": HuggingFaceProvider,
    }
    cls = _classes.get(body.provider)
    if not cls:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{body.provider}'")
    provider = cls(body.model, body.api_key.strip(), body.temperature)
    result = provider.validate()
    return {"success": result.ok, "provider": provider.name, **result.to_dict()}
