"""
scoring/config_store.py — Secure persistence for AI provider configuration.

Responsibilities
  • Persist provider/model/temperature to output/ai_config.json
  • Encrypt the API key at rest with Fernet (symmetric AES-128-CBC + HMAC)
  • NEVER return the decrypted key through any API — only an internal getter
    used by the provider layer reads it back.

Key management
  A machine-local encryption key lives in output/.ai_secret.key (chmod 600).
  It is generated once on first use. This protects the API key from casual
  disk inspection / accidental commit of ai_config.json. (It is not a defence
  against an attacker who already has full filesystem access — for that you'd
  use a real secrets manager; the seam is isolated here for a future swap.)
"""

from __future__ import annotations

import json
import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.scoring")

CONFIG_FILE: Path = OUTPUT_DIR / "ai_config.json"
SECRET_KEY_FILE: Path = OUTPUT_DIR / ".ai_secret.key"

# Allowed values — kept here so the API layer can validate without importing providers.
PROVIDERS = {"anthropic", "openai", "gemini", "groq", "huggingface"}
MODELS = {
    "huggingface": {
        "hf-default":        "Llama 3.1 8B Instruct (default, free tier)",
        "llama-3.1-8b":      "Llama 3.1 8B Instruct",
        "qwen2.5-coder-32b": "Qwen 2.5 Coder 32B Instruct",
        "deepseek-r1":       "DeepSeek R1 (reasoning, slower)",
    },
    "anthropic": {
        "claude-sonnet": "Claude Sonnet",
        "claude-opus":   "Claude Opus",
    },
    "openai": {
        "gpt-5-mini": "GPT-5 Mini",
        "gpt-5":      "GPT-5",
    },
    "gemini": {
        "gemini-flash":      "Gemini 2.0 Flash",
        "gemini-2.5-flash":  "Gemini 2.5 Flash",
        "gemini-pro":        "Gemini 2.5 Pro",
    },
    "groq": {
        "llama-3.3-70b": "Llama 3.3 70B (versatile)",
        "llama-3.1-8b":  "Llama 3.1 8B (instant)",
        "gemma2-9b":     "Gemma2 9B",
        "deepseek-r1":   "DeepSeek R1 Distill 70B",
    },
}


# ──────────────────────────────────────────────
# Encryption key handling
# ──────────────────────────────────────────────

def _get_fernet() -> Fernet:
    """Load or create the machine-local encryption key."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_KEY_FILE.exists():
        key = Fernet.generate_key()
        SECRET_KEY_FILE.write_bytes(key)
        try:
            os.chmod(SECRET_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600
        except OSError:
            pass  # best-effort on platforms that don't support chmod
        logger.info("Generated new AI secret key")
    return Fernet(SECRET_KEY_FILE.read_bytes())


def _encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


# ──────────────────────────────────────────────
# Config persistence
# ──────────────────────────────────────────────

def _read() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read ai_config: %s", exc)
        return {}


def _write(cfg: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    tmp.replace(CONFIG_FILE)


def save_config(
    provider: str,
    model: str,
    temperature: float,
    api_key: Optional[str] = None,
    clear_key: bool = False,
) -> dict:
    """
    Persist configuration. If api_key is provided it is encrypted and stored;
    if omitted/empty, the previously stored key is preserved (lets the user
    update model/temperature without re-entering the key).
    If clear_key=True, any existing key is removed (for switching to offline mode).
    """
    provider = provider.lower().strip()
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Use one of {sorted(PROVIDERS)}.")
    # Model is free-text: the user may enter any exact model id their key supports.
    # We only require it to be non-empty; the provider's validate()/scoring call
    # will report "model_not_found" if the id is wrong.
    if not (model or "").strip():
        raise ValueError("Model id is required.")
    if not (0.0 <= temperature <= 1.0):
        raise ValueError("Temperature must be between 0.0 and 1.0.")

    cfg = _read()
    cfg["provider"] = provider
    cfg["model"] = model
    cfg["temperature"] = round(float(temperature), 2)
    cfg["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if clear_key:
        # Explicitly remove the key
        cfg.pop("api_key_encrypted", None)
        cfg["key_set"] = False
        logger.info("Cleared API key — reverting to offline mode")
    elif api_key:
        # New key provided
        cfg["api_key_encrypted"] = _encrypt(api_key.strip())
        cfg["key_set"] = True
    elif "api_key_encrypted" not in cfg:
        # No key provided and none exists
        cfg["key_set"] = False

    _write(cfg)
    logger.info("Saved AI config: provider=%s model=%s temp=%s key_set=%s",
                provider, model, cfg["temperature"], cfg.get("key_set"))
    return public_config()


def public_config() -> dict:
    """
    Frontend-safe view of the config. NEVER includes the API key.
    `key_set` tells the UI whether a key exists (so it can show "saved").
    """
    cfg = _read()
    if not cfg:
        return {
            "provider": "huggingface",
            "model": "hf-default",
            "temperature": 0.2,
            "key_set": False,
            "configured": False,
            "ai_feature_enabled": False,
        }
    return {
        "provider": cfg.get("provider", "huggingface"),
        "model": cfg.get("model", "hf-default"),
        "temperature": cfg.get("temperature", 0.2),
        "key_set": bool(cfg.get("api_key_encrypted")),
        "configured": bool(cfg.get("api_key_encrypted")),
        "updated_at": cfg.get("updated_at"),
        "ai_feature_enabled": bool(cfg.get("ai_feature_enabled", False)),
    }


# ──────────────────────────────────────────────
# AI-assisted CV parsing feature toggle
#
# Master switch, OFF by default. While off, get_provider() (providers.py)
# ignores any stored provider/key and returns None — so no external LLM call
# can happen even if a key was saved before the feature was disabled, and
# ai_resume_parser.py falls back to the offline regex+spaCy parser. Persisted
# in the same file as the rest of the AI config, so it survives app restarts
# and is shared by every browser/session hitting this server (not a
# per-browser localStorage flag).
# ──────────────────────────────────────────────

def is_feature_enabled() -> bool:
    return bool(_read().get("ai_feature_enabled", False))


def set_feature_enabled(enabled: bool) -> dict:
    cfg = _read()
    cfg["ai_feature_enabled"] = bool(enabled)
    cfg["feature_updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(cfg)
    logger.info("AI-based scoring feature toggled: enabled=%s", cfg["ai_feature_enabled"])
    return public_config()


def get_decrypted_key() -> Optional[str]:
    """
    INTERNAL ONLY — used by the provider layer. Returns the plaintext key or
    None. This function must never be wired to an API response.
    """
    cfg = _read()
    token = cfg.get("api_key_encrypted")
    if not token:
        return None
    try:
        return _decrypt(token)
    except InvalidToken:
        logger.error("Stored API key could not be decrypted (secret key changed?).")
        return None


def get_runtime_config() -> dict:
    """Internal: full config incl. decrypted key, for the provider factory."""
    cfg = _read()
    return {
        "provider": cfg.get("provider", "huggingface"),
        "model": cfg.get("model", "hf-default"),
        "temperature": cfg.get("temperature", 0.2),
        "api_key": get_decrypted_key(),
    }
