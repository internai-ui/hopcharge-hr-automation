"""
admin_settings.py — Editable admin configuration (email text + score thresholds).

Values are read THROUGH this store at runtime. If an override exists in
output/admin_settings.json it wins; otherwise the hardcoded DEFAULTS apply.
"Reset to default" simply removes the override key, so defaults always remain
the safe fallback even if the file is deleted or corrupt.

Stored shape (output/admin_settings.json):
{
  "email": {
    "recruitment": { "subject": "...", "body": "..." },
    "onboarding":  { "subject": "...", "body": "..." }
  },
  "thresholds": { "auto_reject": 30, "auto_move": 65 }
}
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.admin_settings")

SETTINGS_FILE: Path = OUTPUT_DIR / "admin_settings.json"
_lock = threading.Lock()


# ──────────────────────────────────────────────
# DEFAULTS — the editable prose + threshold defaults.
# The email "body" is the human-readable message; the surrounding HTML chrome
# (header, button, footer) stays fixed in emailer.py. Placeholders allowed in
# body: {name}. The button + form link are added by the template automatically.
# ──────────────────────────────────────────────

DEFAULT_RECRUITMENT_SUBJECT = "Thank You for Applying to Hopcharge — Next Steps"
DEFAULT_RECRUITMENT_BODY = (
    "Thank you for your interest in joining Hopcharge. We appreciate the time and "
    "effort you have invested in your application and are pleased to confirm that we "
    "have received it successfully.\n\n"
    "As part of our recruitment process, we kindly request you to complete the "
    "following assessment form at your earliest convenience. Your responses will help "
    "our team evaluate your profile and match you with the most suitable opportunity "
    "within our organisation.\n\n"
    "Please note that completing this form is a mandatory step in the process. "
    "Candidates who do not respond within five business days may not be considered "
    "for the current hiring cycle."
)

DEFAULT_ONBOARDING_SUBJECT = "Welcome to the Hopcharge Family 🎉"
DEFAULT_ONBOARDING_BODY = (
    "Congratulations! After a competitive selection process, you stood out — and we "
    "could not be more excited to welcome you to the Hopcharge family.\n\n"
    "Before your first day, we need a few details from you to complete the onboarding "
    "process. Please complete your onboarding form within 3 business days."
)

DEFAULT_AUTO_REJECT = 30
DEFAULT_AUTO_MOVE = 65

DEFAULTS = {
    "email": {
        "recruitment": {"subject": DEFAULT_RECRUITMENT_SUBJECT, "body": DEFAULT_RECRUITMENT_BODY},
        "onboarding":  {"subject": DEFAULT_ONBOARDING_SUBJECT,  "body": DEFAULT_ONBOARDING_BODY},
    },
    "thresholds": {"auto_reject": DEFAULT_AUTO_REJECT, "auto_move": DEFAULT_AUTO_MOVE},
}


# ──────────────────────────────────────────────
# Storage
# ──────────────────────────────────────────────

def _load_raw() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        logger.warning("admin_settings.json unreadable — using defaults.")
        return {}


def _save_raw(data: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def _merged() -> dict:
    """Defaults deep-merged with any saved overrides (overrides win)."""
    raw = _load_raw()
    out = {
        "email": {
            "recruitment": dict(DEFAULTS["email"]["recruitment"]),
            "onboarding":  dict(DEFAULTS["email"]["onboarding"]),
        },
        "thresholds": dict(DEFAULTS["thresholds"]),
    }
    e = raw.get("email", {})
    for k in ("recruitment", "onboarding"):
        if isinstance(e.get(k), dict):
            for fld in ("subject", "body"):
                if e[k].get(fld) is not None:
                    out["email"][k][fld] = e[k][fld]
    t = raw.get("thresholds", {})
    for k in ("auto_reject", "auto_move"):
        if t.get(k) is not None:
            try:
                out["thresholds"][k] = int(t[k])
            except (TypeError, ValueError):
                pass
    return out


# ──────────────────────────────────────────────
# Public accessors (used by emailer.py / stores at runtime)
# ──────────────────────────────────────────────

def get_settings() -> dict:
    return _merged()


def get_email(kind: str) -> dict:
    """kind = 'recruitment' | 'onboarding' → {subject, body}."""
    return _merged()["email"].get(kind, DEFAULTS["email"].get(kind, {}))


def get_threshold(name: str) -> int:
    """name = 'auto_reject' | 'auto_move'."""
    return int(_merged()["thresholds"].get(name, DEFAULTS["thresholds"][name]))


# ──────────────────────────────────────────────
# Per-role scoring tuning (Feature: tunable strictness + semantic traits)
#
# Stored under raw["role_tuning"][role_key]:
#   { "strictness": 0-100, "creativeness": 0-100,
#     "traits": [ {"name": "...", "description": "..."} ] }
#
#   strictness    50 = current/neutral behaviour. Higher = harsher marking,
#                 lower = more lenient. In AI mode it tells the model how strict
#                 to be AND scales the sub-score; in OFFLINE mode the rules engine
#                 internalises it (tighter floors, smaller nudge, a downward gain).
#   creativeness  50 = neutral. Higher = reward original / unconventional but
#                 sound answers (offline: trust exact keywords less, reward
#                 substance + lexical variety more; AI: a prompt clause). Lower =
#                 favour standard, textbook-correct answers only.
#   traits        Free-text qualities the HR wants judged by GIST/meaning (not
#                 keywords). In AI mode the model rates how strongly each answer
#                 demonstrates them; that nudges the score within a small band.
# ──────────────────────────────────────────────

DEFAULT_STRICTNESS = 50
DEFAULT_CREATIVENESS = 50


def get_role_tuning(role_key: str) -> dict:
    """Return {"strictness": int, "creativeness": int, "traits": [{name, description}]}
    for a role, falling back to neutral defaults so scoring works before anything
    is set."""
    raw = _load_raw()
    rt = (raw.get("role_tuning") or {}).get(role_key) or {}
    try:
        strictness = int(rt.get("strictness", DEFAULT_STRICTNESS))
    except (TypeError, ValueError):
        strictness = DEFAULT_STRICTNESS
    strictness = max(0, min(100, strictness))
    try:
        creativeness = int(rt.get("creativeness", DEFAULT_CREATIVENESS))
    except (TypeError, ValueError):
        creativeness = DEFAULT_CREATIVENESS
    creativeness = max(0, min(100, creativeness))
    traits = []
    for t in (rt.get("traits") or []):
        if not isinstance(t, dict):
            continue
        nm = (t.get("name") or "").strip()
        if nm:
            traits.append({"name": nm[:80],
                           "description": (t.get("description") or "").strip()[:500]})
    return {"strictness": strictness, "creativeness": creativeness, "traits": traits}


def set_role_tuning(role_key: str, strictness: int, traits: list,
                    creativeness: int = DEFAULT_CREATIVENESS) -> dict:
    clean = []
    for t in (traits or []):
        if not isinstance(t, dict):
            continue
        nm = (t.get("name") or "").strip()
        if nm:
            clean.append({"name": nm[:80],
                          "description": (t.get("description") or "").strip()[:500]})
    try:
        s = max(0, min(100, int(strictness)))
    except (TypeError, ValueError):
        s = DEFAULT_STRICTNESS
    try:
        cr = max(0, min(100, int(creativeness)))
    except (TypeError, ValueError):
        cr = DEFAULT_CREATIVENESS
    with _lock:
        raw = _load_raw()
        raw.setdefault("role_tuning", {})[role_key] = {
            "strictness": s, "creativeness": cr, "traits": clean}
        _save_raw(raw)
    logger.info("Updated scoring tuning for role %s (strictness=%d, creativeness=%d, traits=%d)",
                role_key, s, cr, len(clean))
    return get_role_tuning(role_key)


# ──────────────────────────────────────────────
# FastAPI router
# ──────────────────────────────────────────────

router = APIRouter(prefix="/api/admin", tags=["admin"])


class EmailSettings(BaseModel):
    subject: str = Field(..., min_length=1, max_length=300)
    body:    str = Field(..., min_length=1, max_length=8000)


class ThresholdSettings(BaseModel):
    auto_reject: int = Field(..., ge=0, le=100)
    auto_move:   int = Field(..., ge=0, le=100)


@router.get("/settings")
async def get_all_settings():
    """Current effective settings (defaults + overrides) plus the pure defaults
    so the UI can show a 'reset to default' affordance."""
    return {"success": True, "settings": _merged(), "defaults": DEFAULTS}


@router.put("/email/{kind}")
async def update_email(kind: str, body: EmailSettings):
    if kind not in ("recruitment", "onboarding"):
        raise HTTPException(status_code=404, detail="Unknown email type.")
    with _lock:
        raw = _load_raw()
        raw.setdefault("email", {})[kind] = {"subject": body.subject, "body": body.body}
        _save_raw(raw)
    logger.info("Updated %s email text.", kind)
    return {"success": True, "email": _merged()["email"][kind]}


@router.post("/email/{kind}/reset")
async def reset_email(kind: str):
    if kind not in ("recruitment", "onboarding"):
        raise HTTPException(status_code=404, detail="Unknown email type.")
    with _lock:
        raw = _load_raw()
        if raw.get("email", {}).get(kind) is not None:
            raw["email"].pop(kind, None)
            _save_raw(raw)
    return {"success": True, "email": DEFAULTS["email"][kind]}


@router.put("/thresholds")
async def update_thresholds(body: ThresholdSettings):
    with _lock:
        raw = _load_raw()
        raw["thresholds"] = {"auto_reject": body.auto_reject, "auto_move": body.auto_move}
        _save_raw(raw)
    logger.info("Updated thresholds: reject<%d, move>%d", body.auto_reject, body.auto_move)
    return {"success": True, "thresholds": _merged()["thresholds"]}


@router.post("/thresholds/reset")
async def reset_thresholds():
    with _lock:
        raw = _load_raw()
        raw.pop("thresholds", None)
        _save_raw(raw)
    return {"success": True, "thresholds": DEFAULTS["thresholds"]}


# ── Per-role scoring tuning ──

class TraitModel(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field("", max_length=500)


class RoleTuning(BaseModel):
    strictness: int = Field(DEFAULT_STRICTNESS, ge=0, le=100)
    creativeness: int = Field(DEFAULT_CREATIVENESS, ge=0, le=100)
    traits: list[TraitModel] = Field(default_factory=list)


@router.get("/role-tuning/{role_key}")
async def get_role_tuning_ep(role_key: str):
    """Current strictness + custom traits for one role (neutral defaults if unset)."""
    return {"success": True, "role_key": role_key, "tuning": get_role_tuning(role_key)}


@router.put("/role-tuning/{role_key}")
async def put_role_tuning_ep(role_key: str, body: RoleTuning):
    """Save strictness + creativeness (0–100) + semantic traits for one role."""
    tuning = set_role_tuning(role_key, body.strictness,
                             [t.model_dump() for t in body.traits],
                             creativeness=body.creativeness)
    return {"success": True, "role_key": role_key, "tuning": tuning}
