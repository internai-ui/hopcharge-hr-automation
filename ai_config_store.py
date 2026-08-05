"""
ai_config_store.py — Persistence & provider settings for AI-assisted CV Parsing.

Stores AI provider settings (OpenAI, Anthropic Claude, Google Gemini, Groq, HuggingFace)
in output/ai_config.json. Sensitive API keys are encrypted at rest using a machine-local key.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.ai_config")

CONFIG_FILE: Path = OUTPUT_DIR / "ai_config.json"
SECRET_KEY_FILE: Path = OUTPUT_DIR / ".ai_config_secret.key"

_lock = threading.Lock()

DEFAULT_CONFIG = {
    "ai_feature_enabled": False,  # Master toggle for AI features
    "provider": "huggingface",
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "temperature": 0.2,
    "api_key_encrypted": None,
}

MODELS = {
    "huggingface": [
        "meta-llama/Llama-3.1-8B-Instruct",
        "Qwen/Qwen2.5-Coder-32B-Instruct",
        "deepseek-ai/DeepSeek-R1",
    ],
    "anthropic": [
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "claude-3-opus-latest",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-1.5-flash",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
    ],
}


def _get_fernet() -> Fernet:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_KEY_FILE.exists():
        key = Fernet.generate_key()
        SECRET_KEY_FILE.write_bytes(key)
        try:
            SECRET_KEY_FILE.chmod(0o600)
        except Exception:
            pass
    else:
        key = SECRET_KEY_FILE.read_bytes()
    return Fernet(key)


def _load_raw() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except Exception as e:
        logger.warning("Could not read ai_config.json, falling back to defaults: %s", e)
        return dict(DEFAULT_CONFIG)


def _save_raw(data: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def is_feature_enabled() -> bool:
    with _lock:
        return bool(_load_raw().get("ai_feature_enabled", False))


def set_feature_enabled(enabled: bool) -> dict:
    with _lock:
        raw = _load_raw()
        raw["ai_feature_enabled"] = bool(enabled)
        _save_raw(raw)
        return raw


def get_runtime_config() -> dict:
    """Returns decrypted config dict for internal provider calls."""
    with _lock:
        raw = _load_raw()
        key = None
        enc = raw.get("api_key_encrypted")
        if enc:
            try:
                key = _get_fernet().decrypt(enc.encode("utf-8")).decode("utf-8")
            except Exception as e:
                logger.error("Failed to decrypt stored API key: %s", e)
        return {
            "ai_feature_enabled": raw.get("ai_feature_enabled", False),
            "provider": raw.get("provider", "huggingface"),
            "model": raw.get("model", MODELS["huggingface"][0]),
            "temperature": float(raw.get("temperature", 0.2)),
            "api_key": key,
        }


def public_config() -> dict:
    """Returns frontend-safe config (never includes decrypted API key)."""
    with _lock:
        raw = _load_raw()
        return {
            "ai_feature_enabled": raw.get("ai_feature_enabled", False),
            "provider": raw.get("provider", "huggingface"),
            "model": raw.get("model", MODELS["huggingface"][0]),
            "temperature": float(raw.get("temperature", 0.2)),
            "key_set": bool(raw.get("api_key_encrypted")),
        }


def save_config(
    provider: str,
    model: str,
    temperature: float,
    api_key: str | None = None,
    clear_key: bool = False,
) -> dict:
    if provider not in MODELS:
        raise ValueError(f"Unknown provider '{provider}'")
    temp = max(0.0, min(1.0, float(temperature)))

    with _lock:
        raw = _load_raw()
        raw["provider"] = provider
        raw["model"] = model
        raw["temperature"] = temp

        if clear_key:
            raw["api_key_encrypted"] = None
        elif api_key and api_key.strip():
            enc = _get_fernet().encrypt(api_key.strip().encode("utf-8")).decode("utf-8")
            raw["api_key_encrypted"] = enc

        _save_raw(raw)
        return public_config()
