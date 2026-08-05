"""
admin_settings.py — Editable admin configuration (email text).

Values are read THROUGH this store at runtime. If an override exists in
output/admin_settings.json it wins; otherwise the hardcoded DEFAULTS apply.
"Reset to default" simply removes the override key, so defaults always remain
the safe fallback even if the file is deleted or corrupt.

Stored shape (output/admin_settings.json):
{
  "email": {
    "recruitment": { "subject": "...", "body": "..." },
    "onboarding":  { "subject": "...", "body": "..." },
    "rejection":   { "subject": "...", "body": "..." }
  },
  "recruitment_form": { "form_id": "...", "form_link": "..." }
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
# DEFAULTS — the editable prose defaults.
# The email "body" is the human-readable message; the surrounding HTML chrome
# (header, button, footer) stays fixed in emailer.py. Placeholders allowed in
# body: {name} (all kinds), {role} (onboarding + rejection only — substituted
# by emailer.py, left as literal text if absent from a candidate's record).
# The button + form link are added by the template automatically.
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
    "could not be more excited to welcome you to the Hopcharge family as {role}.\n\n"
    "Before your first day, we need a few details from you to complete the onboarding "
    "process. Please complete your onboarding form within 3 business days so we can "
    "prepare everything for your arrival on time."
)

DEFAULT_REJECTION_SUBJECT = "Update on Your Hopcharge Application"
DEFAULT_REJECTION_BODY = (
    "Thank you for your interest in joining Hopcharge and for taking the time to apply "
    "for {role}. After careful consideration, we have decided not to move forward with "
    "your application at this time.\n\n"
    "This decision does not diminish the qualities you bring, and we encourage you to "
    "apply again for future opportunities that match your profile."
)

DEFAULTS = {
    "email": {
        "recruitment": {"subject": DEFAULT_RECRUITMENT_SUBJECT, "body": DEFAULT_RECRUITMENT_BODY},
        "onboarding":  {"subject": DEFAULT_ONBOARDING_SUBJECT,  "body": DEFAULT_ONBOARDING_BODY},
        "rejection":   {"subject": DEFAULT_REJECTION_SUBJECT,   "body": DEFAULT_REJECTION_BODY},
    },
    "recruitment_form": {"form_id": "", "form_link": ""},
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
            "rejection":   dict(DEFAULTS["email"]["rejection"]),
        },
        "recruitment_form": dict(DEFAULTS["recruitment_form"]),
    }
    e = raw.get("email", {})
    for k in ("recruitment", "onboarding", "rejection"):
        if isinstance(e.get(k), dict):
            for fld in ("subject", "body"):
                if e[k].get(fld) is not None:
                    out["email"][k][fld] = e[k][fld]
    rf = raw.get("recruitment_form", {})
    if isinstance(rf, dict):
        for fld in ("form_id", "form_link"):
            if rf.get(fld) is not None:
                out["recruitment_form"][fld] = rf[fld]
    return out


# ──────────────────────────────────────────────
# Public accessors (used by emailer.py / stores at runtime)
# ──────────────────────────────────────────────

def get_settings() -> dict:
    return _merged()


def get_email(kind: str) -> dict:
    """kind = 'recruitment' | 'onboarding' | 'rejection' → {subject, body}."""
    return _merged()["email"].get(kind, DEFAULTS["email"].get(kind, {}))


def get_recruitment_form() -> dict:
    """The saved Google Form ID (for the Forms API sync) and public form link
    (embedded as the recruitment email's CTA button) — {form_id, form_link}."""
    return _merged()["recruitment_form"]


# ──────────────────────────────────────────────
# FastAPI router
# ──────────────────────────────────────────────

router = APIRouter(prefix="/api/admin", tags=["admin"])


class EmailSettings(BaseModel):
    subject: str = Field(..., min_length=1, max_length=300)
    body:    str = Field(..., min_length=1, max_length=8000)


@router.get("/settings")
async def get_all_settings():
    """Current effective settings (defaults + overrides) plus the pure defaults
    so the UI can show a 'reset to default' affordance."""
    return {"success": True, "settings": _merged(), "defaults": DEFAULTS}


@router.put("/email/{kind}")
async def update_email(kind: str, body: EmailSettings):
    if kind not in ("recruitment", "onboarding", "rejection"):
        raise HTTPException(status_code=404, detail="Unknown email type.")
    with _lock:
        raw = _load_raw()
        raw.setdefault("email", {})[kind] = {"subject": body.subject, "body": body.body}
        _save_raw(raw)
    logger.info("Updated %s email text.", kind)
    return {"success": True, "email": _merged()["email"][kind]}


class EmailPreviewBody(BaseModel):
    subject: str = Field("", max_length=300)
    body:    str = Field("", max_length=8000)


@router.post("/email/{kind}/preview")
async def preview_email(kind: str, body: EmailPreviewBody):
    """Render the ACTUAL email HTML (same templates the real send path
    uses) with the given draft subject/body plus sample candidate data —
    never persists anything, purely for the admin editor's live preview."""
    if kind not in ("recruitment", "onboarding", "rejection"):
        raise HTTPException(status_code=404, detail="Unknown email type.")
    try:
        from emailer import render_email_preview
        preview = render_email_preview(kind, body.subject, body.body)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not render preview: {exc}")
    return {"success": True, **preview}


@router.post("/email/{kind}/reset")
async def reset_email(kind: str):
    if kind not in ("recruitment", "onboarding", "rejection"):
        raise HTTPException(status_code=404, detail="Unknown email type.")
    with _lock:
        raw = _load_raw()
        if raw.get("email", {}).get(kind) is not None:
            raw["email"].pop(kind, None)
            _save_raw(raw)
    return {"success": True, "email": DEFAULTS["email"][kind]}


class RecruitmentFormSettings(BaseModel):
    form_id:   str = Field("", max_length=300)
    form_link: str = Field("", max_length=500)


@router.put("/recruitment-form")
async def update_recruitment_form(body: RecruitmentFormSettings):
    if body.form_link and not body.form_link.startswith("http"):
        raise HTTPException(status_code=400, detail="Form link must be a valid URL (starting with http).")
    with _lock:
        raw = _load_raw()
        raw["recruitment_form"] = {"form_id": body.form_id.strip(), "form_link": body.form_link.strip()}
        _save_raw(raw)
    logger.info("Updated recruitment form settings (form_id=%s).", body.form_id.strip() or "(empty)")
    return {"success": True, "recruitment_form": _merged()["recruitment_form"]}
