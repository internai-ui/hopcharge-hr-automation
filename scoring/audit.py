"""
scoring/audit.py — Feature 9: evaluation audit trail + simple rate limiting.

  • log_evaluation()  appends an immutable line to output/ai_audit.log (JSONL)
    recording who/what/when of each scoring run — without storing answer text
    or any secret.
  • RateLimiter       a small in-process token-bucket guarding scoring calls so
    a runaway bulk loop can't hammer a paid provider.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.scoring")

AUDIT_FILE: Path = OUTPUT_DIR / "ai_audit.log"


def log_evaluation(response_id: str, result: dict) -> None:
    """Append a single JSONL audit record. Best-effort; never raises."""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "candidate_evaluated",
            "response_id": response_id,
            "role": result.get("scored_role"),
            "provider": result.get("provider_used"),
            "objective_score": result.get("objective_score"),
            "ai_score": result.get("ai_score"),
            "total_score": result.get("total_score"),
            "recommendation": result.get("recommendation"),
        }
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        logger.error("Audit write failed: %s", exc)


def read_audit(limit: int = 200) -> list[dict]:
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


class RateLimiter:
    """Token bucket: `rate` calls per `per` seconds, thread-safe."""
    def __init__(self, rate: int = 30, per: float = 60.0):
        self.rate = rate
        self.per = per
        self.allowance = float(rate)
        self.last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self.allowance += (now - self.last) * (self.rate / self.per)
            self.last = now
            if self.allowance > self.rate:
                self.allowance = float(self.rate)
            if self.allowance < 1.0:
                return False
            self.allowance -= 1.0
            return True


# Shared limiter for scoring endpoints (30 evaluations / minute by default).
scoring_limiter = RateLimiter(rate=30, per=60.0)
