"""
ai_providers.py — External LLM Provider integrations for AI CV Parsing.
Supports HuggingFace, Anthropic Claude, OpenAI, Google Gemini, and Groq.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("volt_cv.ai_providers")


class ProviderError(Exception):
    def __init__(self, message: str, status: str = "unknown"):
        super().__init__(message)
        self.status = status


@dataclass
class ValidationResult:
    ok: bool
    status: str
    message: str

    def to_dict(self):
        return {"ok": self.ok, "status": self.status, "message": self.message}


class AIProvider:
    name: str = "base"

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        raise NotImplementedError

    def validate(self) -> ValidationResult:
        try:
            res = self.complete_json(
                "Return ONLY JSON: {\"status\":\"ok\"}",
                "Respond with status ok"
            )
            if isinstance(res, dict) and res.get("status") == "ok":
                return ValidationResult(True, "ok", "Connection successful")
            return ValidationResult(True, "ok", "Connected successfully")
        except ProviderError as e:
            return ValidationResult(False, e.status, str(e))
        except Exception as e:
            return ValidationResult(False, "unknown", str(e))


def _clean_json_str(s: str) -> str:
    s = s.strip()
    if s.startswith("```json"):
        s = s[7:]
    elif s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def _parse_json_response(raw_text: str) -> dict:
    cleaned = _clean_json_str(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise ProviderError(f"Failed to parse JSON response: {raw_text[:200]}", "bad_response")


class HuggingFaceProvider(AIProvider):
    name = "huggingface"

    def __init__(self, model: str, api_key: str | None = None, temperature: float = 0.2):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        url = f"https://api-inference.huggingface.co/models/{self.model}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": 1500,
        }

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text_content = data["choices"][0]["message"]["content"]
                return _parse_json_response(text_content)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            if e.code in (401, 403):
                raise ProviderError("Invalid HuggingFace token or missing permissions", "invalid_key")
            elif e.code == 429:
                raise ProviderError("HuggingFace rate limit exceeded", "rate_limited")
            raise ProviderError(f"HuggingFace API error ({e.code}): {err_body[:200]}", "network_error")
        except Exception as e:
            raise ProviderError(f"HuggingFace request failed: {e}", "network_error")


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str, temperature: float = 0.2):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.api_key:
            raise ProviderError("OpenAI API key is missing", "invalid_key")

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text_content = data["choices"][0]["message"]["content"]
                return _parse_json_response(text_content)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ProviderError("Invalid OpenAI API key", "invalid_key")
            elif e.code == 429:
                raise ProviderError("OpenAI quota/rate limit exceeded", "rate_limited")
            raise ProviderError(f"OpenAI error ({e.code})", "network_error")
        except Exception as e:
            raise ProviderError(f"OpenAI request failed: {e}", "network_error")


class ClaudeProvider(AIProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, temperature: float = 0.2):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.api_key:
            raise ProviderError("Anthropic API key missing", "invalid_key")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": self.temperature,
            "max_tokens": 1500,
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text_content = data["content"][0]["text"]
                return _parse_json_response(text_content)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ProviderError("Invalid Anthropic key", "invalid_key")
            raise ProviderError(f"Anthropic error ({e.code})", "network_error")
        except Exception as e:
            raise ProviderError(f"Anthropic request failed: {e}", "network_error")


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, model: str, api_key: str, temperature: float = 0.2):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.api_key:
            raise ProviderError("Gemini API key missing", "invalid_key")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "response_mime_type": "application/json",
            },
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text_content = data["candidates"][0]["content"]["parts"][0]["text"]
                return _parse_json_response(text_content)
        except urllib.error.HTTPError as e:
            if e.code in (400, 403):
                raise ProviderError("Invalid Gemini API key or request", "invalid_key")
            raise ProviderError(f"Gemini error ({e.code})", "network_error")
        except Exception as e:
            raise ProviderError(f"Gemini request failed: {e}", "network_error")


class GroqProvider(AIProvider):
    name = "groq"

    def __init__(self, model: str, api_key: str, temperature: float = 0.2):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.api_key:
            raise ProviderError("Groq API key missing", "invalid_key")

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text_content = data["choices"][0]["message"]["content"]
                return _parse_json_response(text_content)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ProviderError("Invalid Groq API key", "invalid_key")
            raise ProviderError(f"Groq error ({e.code})", "network_error")
        except Exception as e:
            raise ProviderError(f"Groq request failed: {e}", "network_error")


def get_provider(config: dict) -> AIProvider:
    prov = config.get("provider", "huggingface")
    model = config.get("model", "")
    key = config.get("api_key")
    temp = float(config.get("temperature", 0.2))

    if prov == "anthropic":
        return ClaudeProvider(model, key or "", temp)
    elif prov == "openai":
        return OpenAIProvider(model, key or "", temp)
    elif prov == "gemini":
        return GeminiProvider(model, key or "", temp)
    elif prov == "groq":
        return GroqProvider(model, key or "", temp)
    else:
        return HuggingFaceProvider(model, key, temp)
