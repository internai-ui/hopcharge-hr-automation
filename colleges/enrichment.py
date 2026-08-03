"""
colleges/enrichment.py — Email-enrichment provider seam (architecture only).

When discovery finds a NAME + INSTITUTION but no EMAIL, an enrichment provider
can sometimes resolve the address. These are paid third-party services
(Hunter.io, Apollo, Snov.io, RocketReach), so nothing here works until you add
an API key — exactly like the AI scoring engine's provider pattern.

This module gives you:
  • EnrichmentProvider  — abstract interface every provider implements
  • A registry + factory  — pick a provider by name
  • A NullProvider        — the default; returns "not configured" cleanly
  • A HunterProvider stub — shows the shape; fill in the HTTP call + your key

Confidence: providers return a 0-100 confidence the engine stores on the
contact so a reviewer can judge enriched emails (per the spec's 60-80 band).

No network call is made unless a real provider is configured with a key.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("volt_cv.colleges.enrichment")


@dataclass
class EnrichmentResult:
    email: str = ""
    confidence: int = 0
    provider: str = ""
    status: str = "ok"          # ok | not_configured | not_found | error
    message: str = ""


class EnrichmentProvider(ABC):
    name = "base"

    @abstractmethod
    def find_email(self, *, full_name: str, domain: str,
                   college_name: str = "") -> EnrichmentResult:
        ...


class NullProvider(EnrichmentProvider):
    """Default: enrichment disabled. Returns a clean 'not configured' result."""
    name = "none"

    def find_email(self, *, full_name, domain, college_name="") -> EnrichmentResult:
        return EnrichmentResult(
            status="not_configured", provider="none",
            message="No enrichment provider configured. Add an API key to enable.",
        )


class HunterProvider(EnrichmentProvider):
    """
    Hunter.io email-finder. STUB: wire the real request + your key to activate.
    Key is read from env HUNTER_API_KEY (keep secrets out of the repo).
    """
    name = "hunter"
    ENDPOINT = "https://api.hunter.io/v2/email-finder"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("HUNTER_API_KEY", "")

    def find_email(self, *, full_name, domain, college_name="") -> EnrichmentResult:
        if not self.api_key:
            return EnrichmentResult(status="not_configured", provider=self.name,
                                    message="HUNTER_API_KEY not set.")
        if not (full_name and domain):
            return EnrichmentResult(status="error", provider=self.name,
                                    message="Need both a name and a domain.")
        # ── Real implementation would look like: ──
        #   import requests
        #   parts = full_name.split()
        #   r = requests.get(self.ENDPOINT, params={
        #       "domain": domain, "first_name": parts[0],
        #       "last_name": parts[-1], "api_key": self.api_key}, timeout=15)
        #   data = r.json().get("data", {})
        #   return EnrichmentResult(email=data.get("email","") or "",
        #       confidence=int(data.get("score") or 0), provider=self.name,
        #       status="ok" if data.get("email") else "not_found")
        return EnrichmentResult(status="not_configured", provider=self.name,
                                message="Hunter provider stub — implement the HTTP call to enable.")


_REGISTRY = {
    "none":   NullProvider,
    "hunter": HunterProvider,
    # "apollo": ApolloProvider,   # add the same way
    # "snov":   SnovProvider,
}


def get_provider(name: str = "none", **kwargs) -> EnrichmentProvider:
    cls = _REGISTRY.get((name or "none").lower(), NullProvider)
    try:
        return cls(**kwargs) if kwargs else cls()
    except Exception as e:
        logger.warning("Enrichment provider %s init failed: %s", name, e)
        return NullProvider()


def available_providers() -> list[str]:
    return sorted(_REGISTRY.keys())
