"""
scoring/providers.py — provider abstraction layer for AI-assisted CV parsing.

    AIProvider (ABC)
    ├── ClaudeProvider       real Anthropic Messages API
    ├── OpenAIProvider       real OpenAI Chat Completions API
    ├── GeminiProvider       real Google Gemini API
    ├── GroqProvider         real Groq API
    └── HuggingFaceProvider  real Hugging Face Inference Providers API

ai_resume_parser.py calls provider.complete_json(system, user) and gets a
parsed dict back. It never knows which concrete provider answered.
get_provider() returns None (rather than any object) when no real provider
is configured — see its docstring below.

Key validation
  Every real provider implements validate() -> ValidationResult, which makes a
  tiny live call and classifies the outcome into a precise status:
      ok | invalid_key | no_credits | rate_limited | model_not_found |
      network_error | bad_response | unknown
  so the UI can tell the user exactly what is wrong (bad key vs no credits vs
  wrong model) instead of a raw error blob.

Model strings
  The exact API model identifier can change over time. Rather than hardcode a
  guess, each provider has DEFAULTS but also accepts a full override: if `model`
  is not one of our short keys, it is sent verbatim to the API. So a wrong
  default never traps you — type the exact model id and validate() reports
  immediately whether the API accepts it.
"""

from __future__ import annotations

import abc
import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger("volt_cv.scoring")


# Default short-id -> API model string. Best-effort; overridable (see module doc).
_CLAUDE_DEFAULTS = {
    "claude-sonnet": "claude-sonnet-4-20250514",
    "claude-opus":   "claude-opus-4-20250514",
}
_OPENAI_DEFAULTS = {
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5":      "gpt-5",
}
_GEMINI_DEFAULTS = {
    "gemini-flash": "gemini-2.0-flash",
    "gemini-pro":   "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
}
_GROQ_DEFAULTS = {
    # friendly short-name → actual Groq model id
    "llama-3.3-70b":    "llama-3.3-70b-versatile",
    "llama-3.1-8b":     "llama-3.1-8b-instant",
    "llama3-70b":       "llama-3.3-70b-versatile",
    "gemma2-9b":        "gemma2-9b-it",
    "deepseek-r1":      "deepseek-r1-distill-llama-70b",
    "groq-default":     "llama-3.3-70b-versatile",
}
_HUGGINGFACE_DEFAULTS = {
    # friendly short-name → HF model id, routed via the Inference Providers
    # router (https://router.huggingface.co/v1), which auto-picks whichever
    # backing provider currently serves that model. IMPORTANT: which models
    # actually work is account-dependent (it's whatever "Inference Providers"
    # the user has enabled at huggingface.co/settings/inference-providers) —
    # "recommended" models in HF's own docs are NOT a guarantee. Verified
    # working with a live token at the time these defaults were set:
    # meta-llama/Llama-3.1-8B-Instruct and Qwen/Qwen2.5-Coder-32B-Instruct.
    # google/gemma-2-2b-it and Qwen/Qwen2.5-7B-Instruct-1M were NOT enabled
    # for that token despite being HF's own top doc recommendation, so don't
    # assume any specific id works without testing — the model field is
    # free-text precisely so a user can type whatever their account serves.
    "llama-3.1-8b":     "meta-llama/Llama-3.1-8B-Instruct",
    "qwen2.5-coder-32b": "Qwen/Qwen2.5-Coder-32B-Instruct",
    "deepseek-r1":      "deepseek-ai/DeepSeek-R1",
    "hf-default":       "meta-llama/Llama-3.1-8B-Instruct",
}


class ProviderError(RuntimeError):
    """Raised on auth / network / response errors, carrying a status token."""
    def __init__(self, message: str, status: str = "unknown"):
        super().__init__(message)
        self.status = status


@dataclass
class ValidationResult:
    ok: bool
    status: str          # ok|invalid_key|no_credits|rate_limited|model_not_found|network_error|bad_response|unknown
    message: str
    detail: str = ""
    model_echo: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_model(model: str, defaults: dict) -> str:
    return defaults.get(model, model)


# ──────────────────────────────────────────────
# Base
# ──────────────────────────────────────────────

class AIProvider(abc.ABC):
    name: str = "base"

    def __init__(self, model: str, temperature: float = 0.2, api_key: Optional[str] = None):
        self.model = model
        self.temperature = temperature
        self.api_key = api_key

    @abc.abstractmethod
    def complete_json(self, system: str, user: str) -> dict: ...

    @abc.abstractmethod
    def validate(self) -> ValidationResult: ...

    def test_connection(self) -> tuple[bool, str]:
        r = self.validate()
        return r.ok, r.message

    @staticmethod
    def _parse_json_blob(text: str) -> dict:
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            raise ProviderError("Model did not return valid JSON.", status="bad_response")

    @staticmethod
    def _http_post(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
        # Cloudflare (in front of Groq and others) blocks the default
        # "Python-urllib/x.y" agent with error 1010. Send a normal UA + Accept
        # so the request is allowed through to the actual API.
        headers = {
            "user-agent": "VOLT-CV/1.0 (+https://hopcharge.com)",
            "accept": "application/json",
            **headers,
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            status, msg = _classify_http_error(e.code, raw)
            raise ProviderError(msg, status=status) from e
        except urllib.error.URLError as e:
            raise ProviderError(f"Network error: {e.reason}", status="network_error") from e
        except (TimeoutError, OSError) as e:
            raise ProviderError(f"Connection failed: {e}", status="network_error") from e


# ──────────────────────────────────────────────
# Error classification (shared)
# ──────────────────────────────────────────────

def _extract_provider_message(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:300]
    err = data.get("error", data)
    if isinstance(err, dict):
        return err.get("message") or err.get("type") or json.dumps(err)[:300]
    return str(err)[:300]


def _classify_http_error(code: int, raw: str) -> tuple[str, str]:
    msg = _extract_provider_message(raw)
    low = (raw or "").lower()

    # Edge/CDN blocks (Cloudflare etc.) arrive as 403 with an HTML challenge
    # page or an "error code: 1010/1020" — NOT a real auth failure. These mean
    # the request never reached the provider's API (often a default User-Agent
    # being blocked, a VPN/proxy, or network egress filtering).
    cf_markers = ("error code: 1010", "error code: 1020", "error code: 1015",
                  "cloudflare", "access denied", "attention required",
                  "<!doctype html", "<html", "cf-ray", "just a moment")
    if any(m in low for m in cf_markers):
        return ("network_error",
                "Request was blocked by the provider's edge/firewall before "
                "reaching the API (Cloudflare). This is NOT an API-key problem — "
                "it usually means the client User-Agent was blocked, or a "
                "VPN/proxy/network filter is interfering. Try again on a normal "
                f"network. ({msg[:120]})")

    if code in (401, 403):
        return "invalid_key", f"Authentication failed — key invalid or lacks access. ({msg})"
    if code == 404 or ("model" in low and "not" in low and "found" in low):
        return "model_not_found", f"Model not found — model id may be wrong/unavailable. ({msg})"
    if code == 429:
        if any(t in low for t in ("credit", "quota", "billing", "insufficient", "exceeded your")):
            return "no_credits", f"Out of credits / quota exceeded. ({msg})"
        return "rate_limited", f"Rate limited — too many requests. ({msg})"
    if code == 400:
        # Gemini reports a bad key as 400 API_KEY_INVALID rather than 401.
        if any(t in low for t in ("api_key_invalid", "api key not valid", "invalid api key",
                                  "api key expired")):
            return "invalid_key", f"The API key is invalid or not valid for this API. ({msg})"
        if any(t in low for t in ("credit", "billing", "insufficient_quota", "quota")):
            return "no_credits", f"Billing/credit problem on the account. ({msg})"
        return "bad_response", f"Request rejected ({code}): {msg}"
    if 500 <= code < 600:
        return "network_error", f"Provider server error ({code}). ({msg})"
    return "unknown", f"Unexpected error ({code}): {msg}"


# ──────────────────────────────────────────────
# Claude
# ──────────────────────────────────────────────

class ClaudeProvider(AIProvider):
    name = "anthropic"
    ENDPOINT = "https://api.anthropic.com/v1/messages"

    def _api_model(self) -> str:
        return _resolve_model(self.model, _CLAUDE_DEFAULTS)

    def _request(self, system: str, user: str, max_tokens: int = 1500, timeout: int = 60) -> str:
        payload = {
            "model": self._api_model(),
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
        }
        data = self._http_post(self.ENDPOINT, headers, payload, timeout=timeout)
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = "".join(parts)
        if not text:
            raise ProviderError("Empty response from Anthropic.", status="bad_response")
        return text

    def complete_json(self, system: str, user: str) -> dict:
        return self._parse_json_blob(self._request(system, user))

    def validate(self) -> ValidationResult:
        model_echo = self._api_model()
        if not self.api_key:
            return ValidationResult(False, "invalid_key", "No API key provided.", model_echo=model_echo)
        try:
            txt = self._request("You are a connectivity test.",
                                'Reply with exactly: {"status":"ok"}', max_tokens=20, timeout=12)
            return ValidationResult(
                True, "ok",
                f"✓ Connected to Anthropic. Model '{model_echo}' is responding and the key has available credits.",
                detail=txt.strip()[:60], model_echo=model_echo)
        except ProviderError as e:
            return ValidationResult(False, e.status, _friendly(e.status, "Anthropic"),
                                    detail=str(e), model_echo=model_echo)


# ──────────────────────────────────────────────
# OpenAI
# ──────────────────────────────────────────────

class OpenAIProvider(AIProvider):
    name = "openai"
    ENDPOINT = "https://api.openai.com/v1/chat/completions"

    def _api_model(self) -> str:
        return _resolve_model(self.model, _OPENAI_DEFAULTS)

    def _request(self, system: str, user: str, max_tokens: int = 1500, timeout: int = 60) -> str:
        payload = {
            "model": self._api_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": max_tokens,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key or ''}",
        }
        data = self._http_post(self.ENDPOINT, headers, payload, timeout=timeout)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise ProviderError("Unexpected response shape from OpenAI.", status="bad_response")

    def complete_json(self, system: str, user: str) -> dict:
        return self._parse_json_blob(self._request(system, user))

    def validate(self) -> ValidationResult:
        model_echo = self._api_model()
        if not self.api_key:
            return ValidationResult(False, "invalid_key", "No API key provided.", model_echo=model_echo)
        try:
            self._request("You are a connectivity test.",
                          'Reply with JSON: {"status":"ok"}', max_tokens=20, timeout=12)
            return ValidationResult(
                True, "ok",
                f"✓ Connected to OpenAI. Model '{model_echo}' is responding and the key has available quota.",
                model_echo=model_echo)
        except ProviderError as e:
            return ValidationResult(False, e.status, _friendly(e.status, "OpenAI"),
                                    detail=str(e), model_echo=model_echo)


# ──────────────────────────────────────────────
# Groq (OpenAI-compatible chat completions API)
# ──────────────────────────────────────────────

class GroqProvider(AIProvider):
    name = "groq"
    ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

    def _api_model(self) -> str:
        return _resolve_model(self.model, _GROQ_DEFAULTS)

    def _build_payload(self, system: str, user: str, max_tokens: int, json_mode: bool) -> dict:
        payload = {
            "model": self._api_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }
        # Groq supports OpenAI-style JSON mode on most models. The prompt already
        # contains the word "JSON" (required by Groq for this mode).
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        return payload

    def _request(self, system: str, user: str, max_tokens: int = 1500, timeout: int = 60) -> str:
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key or ''}",
        }
        # First attempt with JSON mode on.
        try:
            data = self._http_post(
                self.ENDPOINT, headers,
                self._build_payload(system, user, max_tokens, json_mode=True),
                timeout=timeout)
        except ProviderError as e:
            # Some Groq models reject response_format=json_object with a 400.
            # Retry once without JSON mode; _parse_json_blob will extract the
            # JSON object from the text response.
            msg = str(e).lower()
            if e.status == "bad_response" and ("response_format" in msg or "json" in msg):
                data = self._http_post(
                    self.ENDPOINT, headers,
                    self._build_payload(system, user, max_tokens, json_mode=False),
                    timeout=timeout)
            else:
                raise
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise ProviderError("Unexpected response shape from Groq.", status="bad_response")

    def complete_json(self, system: str, user: str) -> dict:
        return self._parse_json_blob(self._request(system, user))

    def validate(self) -> ValidationResult:
        model_echo = self._api_model()
        if not self.api_key:
            return ValidationResult(False, "invalid_key", "No API key provided.", model_echo=model_echo)
        try:
            self._request("You are a connectivity test.",
                          'Reply with JSON: {"status":"ok"}', max_tokens=20, timeout=12)
            return ValidationResult(
                True, "ok",
                f"✓ Connected to Groq. Model '{model_echo}' is responding and the key is active.",
                model_echo=model_echo)
        except ProviderError as e:
            return ValidationResult(False, e.status, _friendly(e.status, "Groq"),
                                    detail=str(e), model_echo=model_echo)


# ──────────────────────────────────────────────
# Google Gemini
# ──────────────────────────────────────────────

class GeminiProvider(AIProvider):
    name = "gemini"
    # Uses the Generative Language API. Model is interpolated into the path.
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def _api_model(self) -> str:
        return _resolve_model(self.model, _GEMINI_DEFAULTS)

    def _endpoint(self) -> str:
        # Auth is via ?key= query param for this API.
        return f"{self.BASE}/{self._api_model()}:generateContent?key={self.api_key or ''}"

    def _request(self, system: str, user: str, max_tokens: int = 1500, timeout: int = 60) -> str:
        payload = {
            # Gemini takes the system prompt separately as system_instruction.
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": self.temperature if self.temperature is not None else 0.2,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        headers = {"content-type": "application/json"}
        data = self._http_post(self._endpoint(), headers, payload, timeout=timeout)

        # Surface a blocked-prompt reason if Gemini returned no candidates.
        if not data.get("candidates"):
            fb = data.get("promptFeedback", {})
            reason = fb.get("blockReason") or "no candidates returned"
            raise ProviderError(f"Gemini returned no output ({reason}).", status="bad_response")

        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError):
            raise ProviderError("Unexpected response shape from Gemini.", status="bad_response")

        if not text:
            raise ProviderError("Empty response from Gemini.", status="bad_response")
        return text

    def complete_json(self, system: str, user: str) -> dict:
        return self._parse_json_blob(self._request(system, user))

    def validate(self) -> ValidationResult:
        model_echo = self._api_model()
        if not self.api_key:
            return ValidationResult(False, "invalid_key", "No API key provided.", model_echo=model_echo)
        try:
            self._request("You are a connectivity test.",
                          'Reply with JSON: {"status":"ok"}', max_tokens=20, timeout=12)
            return ValidationResult(
                True, "ok",
                f"✓ Connected to Google Gemini. Model '{model_echo}' is responding and the key is active.",
                model_echo=model_echo)
        except ProviderError as e:
            return ValidationResult(False, e.status, _friendly(e.status, "Gemini"),
                                    detail=str(e), model_echo=model_echo)


# ──────────────────────────────────────────────
# Hugging Face (Inference Providers router — OpenAI-compatible)
# ──────────────────────────────────────────────

class HuggingFaceProvider(AIProvider):
    """
    Free-tier option: routes chat completions through Hugging Face's
    Inference Providers router (https://router.huggingface.co/v1), which is
    OpenAI-compatible and auto-selects whichever backend currently serves the
    requested model. The token is free (huggingface.co/settings/tokens, needs
    "Inference Providers" permission); the shared free tier is rate-limited
    (~1,000 req/day at time of writing) rather than paid per-token.
    """
    name = "huggingface"
    ENDPOINT = "https://router.huggingface.co/v1/chat/completions"

    def _api_model(self) -> str:
        return _resolve_model(self.model, _HUGGINGFACE_DEFAULTS)

    def _build_payload(self, system: str, user: str, max_tokens: int, json_mode: bool) -> dict:
        payload = {
            "model": self._api_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        return payload

    def _request(self, system: str, user: str, max_tokens: int = 1500, timeout: int = 60) -> str:
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key or ''}",
        }
        # Not every backend behind the router honours response_format on every
        # model, so try JSON mode first and retry once in plain-text mode if
        # that specific request is rejected (mirrors the Groq provider).
        try:
            data = self._http_post(
                self.ENDPOINT, headers,
                self._build_payload(system, user, max_tokens, json_mode=True),
                timeout=timeout)
        except ProviderError as e:
            msg = str(e).lower()
            if e.status == "bad_response" and ("response_format" in msg or "json" in msg):
                data = self._http_post(
                    self.ENDPOINT, headers,
                    self._build_payload(system, user, max_tokens, json_mode=False),
                    timeout=timeout)
            else:
                raise
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise ProviderError("Unexpected response shape from Hugging Face.", status="bad_response")

    def complete_json(self, system: str, user: str) -> dict:
        return self._parse_json_blob(self._request(system, user))

    def validate(self) -> ValidationResult:
        model_echo = self._api_model()
        if not self.api_key:
            return ValidationResult(False, "invalid_key", "No API key provided.", model_echo=model_echo)
        try:
            self._request("You are a connectivity test.",
                          'Reply with JSON: {"status":"ok"}', max_tokens=20, timeout=15)
            return ValidationResult(
                True, "ok",
                f"✓ Connected to Hugging Face. Model '{model_echo}' is responding on the free-tier router.",
                model_echo=model_echo)
        except ProviderError as e:
            return ValidationResult(False, e.status, _friendly(e.status, "Hugging Face"),
                                    detail=str(e), model_echo=model_echo)


def _friendly(status: str, provider: str) -> str:
    return {
        "invalid_key":     f"✗ The {provider} API key is invalid or unauthorized. Check you pasted the full key.",
        "no_credits":      f"✗ The {provider} key is valid but has no credits / quota left. Add billing to use it.",
        "rate_limited":    f"✗ {provider} is rate-limiting this key right now. It works — just wait and retry.",
        "model_not_found": f"✗ The selected model is not available to this {provider} key. Try another model or enter the exact model id.",
        "network_error":   f"✗ Could not reach {provider} (network or server issue). Check connectivity and retry.",
        "bad_response":    f"✗ {provider} responded but the format was unexpected.",
    }.get(status, f"✗ {provider} connection failed (unknown error).")


# ──────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────

def get_provider(runtime_cfg: dict) -> Optional[AIProvider]:
    """Returns None when no real provider is usable (feature disabled, or no
    API key saved) — callers (ai_resume_parser.is_available()/parse_resume_ai())
    treat that as "AI parsing isn't available right now" and fall back to the
    offline regex+spaCy parser, which always works."""
    provider = runtime_cfg.get("provider", "huggingface")
    model = runtime_cfg.get("model", "hf-default")
    temperature = runtime_cfg.get("temperature", 0.2)
    api_key = runtime_cfg.get("api_key")

    # Master feature toggle — OFF by default (see scoring/config_store.py).
    # While disabled, external LLM providers are never invoked, even if a key
    # is still saved from an earlier session.
    from scoring import config_store
    if not config_store.is_feature_enabled():
        api_key = None

    if not api_key:
        return None

    if provider == "anthropic":
        return ClaudeProvider(model, temperature, api_key)
    if provider == "openai":
        return OpenAIProvider(model, temperature, api_key)
    if provider == "gemini":
        return GeminiProvider(model, temperature, api_key)
    if provider == "groq":
        return GroqProvider(model, temperature, api_key)
    if provider == "huggingface":
        return HuggingFaceProvider(model, temperature, api_key)
    raise ProviderError(f"Unknown provider: {provider}", status="unknown")
