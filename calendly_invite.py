"""
calendly_invite.py — Round 1 bulk Calendly invite.

ONE endpoint group that emails every Round 1 candidate (read straight from
accepted_store) a Calendly booking link, via the connected Google account
(Gmail API — see gmail_oauth.py), same as emailer.py's send paths.

Mount in app.py, next to the other routers:

    from calendly_invite import router as calendly_invite_router
    app.include_router(calendly_invite_router)

Free Calendly  → one shared link goes to everyone (current behaviour).
Paid Calendly  → set settings.mode = "api" + an api_token and implement
                 _link_for() to mint a single-use link per candidate. The send
                 loop already calls _link_for(candidate), so nothing else changes.

Settings + a send log persist to output/ (calendly_settings.json,
calendly_invite_log.json), alongside your other stores.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from config import OUTPUT_DIR
from accepted_store import list_accepted
from emailer import _sanitize_name

logger = logging.getLogger("volt_cv.calendly_invite")

SETTINGS_FILE: Path = OUTPUT_DIR / "calendly_settings.json"
LOG_FILE: Path = OUTPUT_DIR / "calendly_invite_log.json"

DEFAULT_SUBJECT = "HopCharge — Schedule your Round 1 Interview"

_SETTINGS_DEFAULTS = {
    "default_link": "",      # legacy single shared link (kept for back-compat)
    # Per-role Round 1 Calendly links. Each role's accepted candidates get the
    # matching link. Keys mirror the canonical rubric role keys.
    "link_customer_support_executive": "",
    "link_operations_specialist": "",
    "link_business_development_manager": "",
    "link_deputy_general_manager": "",
    "link_management_trainee_founders_office": "",
    "link_general_manager_sales": "",
    "link_sales_manager_retail": "",
    "link_operations_supervisor": "",
    "link_field_application_engineer": "",
    "link_ai_engineer": "",
    "mode": "free",          # "free" (shared link) | "api" (paid, future)
    "api_token": "",         # paid only — Calendly personal access / OAuth token
    "company": "HopCharge",
}

# Canonical role keys we hold dedicated links for, with display labels.
# These mirror the rubric role keys in scoring/rubrics.py (updated form roles).
ROLE_LINK_KEYS = {
    "customer_support_executive": "Customer Support Executive",
    "operations_specialist": "Operations Specialist",
    "business_development_manager": "Business Development Manager",
    "deputy_general_manager": "Deputy General Manager",
    "management_trainee_founders_office": "Management Trainee (Founder's Office)",
    "general_manager_sales": "General Manager – Sales",
    "sales_manager_retail": "Sales Manager (Retail)",
    "operations_supervisor": "Operations Supervisor",
    "field_application_engineer": "Field Application Engineer",
    "ai_engineer": "AI Engineer",
}


# ──────────────────────────────────────────────
# Email templates (Calendly-specific; not the assessment email)
# ──────────────────────────────────────────────
HTML_TEMPLATE = """\
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f7;font-family:Arial,Helvetica,sans-serif;color:#333">
  <div style="max-width:600px;margin:32px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,.08)">
    <div style="background:#0B1020;padding:30px 40px;text-align:center">
      <div style="font-size:26px;color:#FFB400;font-weight:700;letter-spacing:4px">HOPCHARGE</div>
      <div style="margin-top:6px;color:#7A90B8;font-size:11px;letter-spacing:2px;text-transform:uppercase">Talent · Round 1 Interview</div>
    </div>
    <div style="padding:34px 40px">
      <p style="font-size:16px;color:#222;font-weight:600;margin:0 0 18px">Dear {name},</p>
      <p style="margin:0 0 16px;line-height:1.7;font-size:15px;color:#444">
        Congratulations on clearing the HR round at <strong>HopCharge</strong>{role_line}.
        We'd like to invite you to your <strong>Round 1 interview</strong>.
      </p>
      <p style="margin:0 0 16px;line-height:1.7;font-size:15px;color:#444">
        Please pick a time that suits you using the link below — it only takes a few
        seconds, and you'll receive a calendar invite automatically.
      </p>
      <div style="text-align:center;margin:28px 0">
        <a href="{link}" target="_blank" style="display:inline-block;background:#FFB400;color:#000;font-weight:700;font-size:14px;letter-spacing:1px;text-transform:uppercase;padding:14px 34px;border-radius:6px;text-decoration:none">Book your Round 1 slot →</a>
      </div>
      <p style="margin:18px 0 0;line-height:1.6;font-size:12px;color:#999">
        If the button doesn't work, copy this link into your browser:<br>
        <a href="{link}" style="color:#7c5cff">{link}</a>
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:26px 0">
      <p style="margin:0;line-height:1.7;font-size:15px;color:#444">
        Looking forward to speaking with you.<br><br>
        Warm regards,<br><strong>HopCharge Recruitment Team</strong>
      </p>
    </div>
    <div style="background:#f9f9f9;padding:18px 40px;text-align:center;font-size:11px;color:#aaa">
      © 2026 HopCharge. This email was sent as part of the HopCharge recruitment process.
    </div>
  </div>
</body></html>
"""

TEXT_TEMPLATE = """\
Dear {name},

Congratulations on clearing the HR round at HopCharge{role_line}. We'd like to
invite you to your Round 1 interview.

Please pick a time that suits you using the link below:

{link}

Looking forward to speaking with you.

Warm regards,
HopCharge Recruitment Team
"""


# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
def _load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return dict(_SETTINGS_DEFAULTS)
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        merged = dict(_SETTINGS_DEFAULTS)
        if isinstance(data, dict):
            merged.update({k: v for k, v in data.items() if k in _SETTINGS_DEFAULTS})
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(_SETTINGS_DEFAULTS)


def _save_settings(patch: dict) -> dict:
    cur = _load_settings()
    for k, v in (patch or {}).items():
        if k in _SETTINGS_DEFAULTS and v is not None:
            cur[k] = v
    if cur.get("mode") not in ("free", "api"):
        cur["mode"] = "free"
    SETTINGS_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False))
    return _mask(cur)


def _mask(s: dict) -> dict:
    s = dict(s)
    s["api_token"] = "***set***" if s.get("api_token") else ""
    return s


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _role_pretty(role: str) -> str:
    role = (role or "").strip()
    return role.replace("_", " ").replace("-", " ").title() if role else ""


def _role_key(role: str) -> str:
    """Normalise any stored role value to a canonical key like
    'customer_support_executive' — matching the rubric role keys. Handles pretty
    labels, apostrophes ("Founder's" → founders), en/em dashes ("General Manager
    – Sales" → general_manager_sales), parentheses and extra whitespace so
    matching a candidate to their role's link is robust."""
    import re
    s = (role or "").strip().lower()
    s = s.replace("’", "").replace("'", "").replace("`", "")   # drop apostrophes
    s = re.sub(r"[^a-z0-9]+", "_", s)   # any other non-alphanumeric run -> single _
    return s.strip("_")


def _round1_candidates() -> list[dict]:
    """Round 1 candidates that have an email — straight from accepted_store."""
    out, seen = [], set()
    for r in list_accepted("round1"):
        email = (r.get("email") or "").strip()
        if not email or "@" not in email or email.lower() in seen:
            continue
        seen.add(email.lower())
        out.append({
            "response_id": r.get("response_id"),
            "name": (r.get("name") or "Candidate").strip() or "Candidate",
            "email": email,
            "role": _role_pretty(r.get("role")),
            "role_key": _role_key(r.get("role")),
        })
    return out


def _link_for(candidate: dict, settings: dict) -> str:
    """Free mode: pick the Calendly link for this candidate's ROLE.

    Uses settings['link_<role_key>'] for the candidate's role. The legacy single
    'default_link' is used ONLY as a fallback when NO per-role links are
    configured at all (so older single-link setups keep working). Once any
    per-role link is set, per-role links are authoritative and a candidate whose
    role has no link returns '' — the caller then skips them rather than send
    the wrong booking page.
    """
    rk = candidate.get("role_key") or _role_key(candidate.get("role"))
    role_link = (settings.get(f"link_{rk}") or "").strip()
    if role_link:
        return role_link
    any_role_link = any((settings.get(f"link_{k}") or "").strip() for k in ROLE_LINK_KEYS)
    if any_role_link:
        return ""   # per-role mode is active; this role just has no link → skip
    return (settings.get("default_link") or "").strip()   # legacy fallback only


def _link_for_api(candidate: dict, settings: dict) -> str:
    """Paid mode (future): mint a single-use Calendly link per candidate."""
    raise NotImplementedError(
        "Paid Calendly per-candidate links are not wired yet. Implement this with "
        "your Calendly API token, then set settings.mode='api'."
    )


def _build_message(sender: str, name: str, role: str, email: str, link: str):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    safe = _sanitize_name(name)
    role_line = f" for the {role} position" if role else ""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = DEFAULT_SUBJECT
    msg["From"] = f"HopCharge Recruitment <{sender}>"
    msg["To"] = email
    msg.attach(MIMEText(TEXT_TEMPLATE.format(name=safe, role_line=role_line, link=link), "plain", _charset="utf-8"))
    msg.attach(MIMEText(HTML_TEMPLATE.format(name=safe, role_line=role_line, link=link), "html", _charset="utf-8"))
    return msg


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_log(entry: dict) -> None:
    try:
        log = json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []
        if not isinstance(log, list):
            log = []
        log.append(entry)
        LOG_FILE.write_text(json.dumps(log, indent=2, ensure_ascii=False))
    except Exception:
        pass  # logging must never break a send


# ──────────────────────────────────────────────
# Send (Gmail API via the connected Google account — same path as
# emailer.send_campaign)
# ──────────────────────────────────────────────
def send_round1_invites(calendly_url: Optional[str] = None,
                        role_links: Optional[dict] = None) -> dict:
    import gmail_oauth
    gmail_service = gmail_oauth.get_gmail_service()
    sender_address = (gmail_oauth.public_status().get("connected_email") or "").strip()
    if not sender_address:
        sender_address = "hr@hopcharge.com"

    settings = _load_settings()
    # Apply any role links passed for this send (already persisted by the route,
    # but accept them here too so the function is usable directly).
    if role_links:
        for rk, link in role_links.items():
            if rk in ROLE_LINK_KEYS:
                settings[f"link_{rk}"] = (link or "").strip()
    company = settings.get("company") or "HopCharge"
    cands = _round1_candidates()
    if not cands:
        return {"success": False, "sent": 0, "failed": 0, "total": 0,
                "error": "No Round 1 candidates with a valid email were found."}

    mode = settings.get("mode", "free")
    use_api = mode == "api" and bool(settings.get("api_token"))

    # A legacy single link may still be supplied; keep it as a fallback only.
    if calendly_url:
        settings["default_link"] = calendly_url.strip()

    # In free mode, make sure at least one usable link exists.
    if not use_api:
        any_link = any((settings.get(f"link_{rk}") or "").strip() for rk in ROLE_LINK_KEYS) \
                   or (settings.get("default_link") or "").strip()
        if not any_link:
            return {"success": False, "sent": 0, "failed": 0, "total": len(cands),
                    "error": "No Calendly links set. Add a link for each role in the dialog."}

    results = []
    skipped = []
    for c in cands:
        try:
            link = _link_for_api(c, settings) if use_api else _link_for(c, settings)
            if not link:
                # No link for this candidate's role — skip rather than
                # send the wrong booking page.
                skipped.append({"name": c["name"], "email": c["email"],
                                "role": c["role"], "status": "skipped",
                                "error": "No Calendly link set for this role"})
                logger.warning("Round1 invite skipped (no link for role %s) → %s",
                               c.get("role_key"), c["email"])
                continue
            msg = _build_message(sender_address, c["name"], c["role"], c["email"], link)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            gmail_service.users().messages().send(userId="me", body={"raw": raw}).execute()
            results.append({"name": c["name"], "email": c["email"],
                            "role": c["role"], "status": "sent"})
            logger.info("Round1 invite → %s <%s> [%s]", c["name"], c["email"], c["role"])
        except Exception as exc:
            results.append({"name": c["name"], "email": c["email"],
                            "role": c["role"], "status": "failed", "error": str(exc)})
            logger.error("Round1 invite failed → %s: %s", c["email"], exc)

    all_results = results + skipped
    sent = sum(1 for r in results if r["status"] == "sent")
    failed = sum(1 for r in results if r["status"] == "failed")
    _append_log({"at": _now_iso(), "sender": sender_address, "mode": mode,
                 "link": "(per-role links)" if not use_api else "(per-candidate API links)",
                 "total": len(cands), "sent": sent, "failed": failed,
                 "skipped": len(skipped), "results": all_results})
    logger.info("Round1 Calendly invite — sent: %d, failed: %d, skipped: %d",
                sent, failed, len(skipped))
    return {"success": sent > 0, "sent": sent, "failed": failed,
            "skipped": len(skipped), "total": len(cands), "results": all_results,
            "link": "(per-role links)" if not use_api else "(per-candidate API links)"}


# ──────────────────────────────────────────────
# FastAPI router (mounted in app.py)
# ──────────────────────────────────────────────
router = APIRouter(prefix="/api/round1-invite", tags=["round1-invite"])


class SettingsBody(BaseModel):
    # extra='allow' so a link_<role_key> for ANY role in _SETTINGS_DEFAULTS is
    # accepted; _save_settings still whitelists by _SETTINGS_DEFAULTS keys.
    model_config = ConfigDict(extra="allow")
    default_link: Optional[str] = None
    link_customer_support_executive: Optional[str] = None
    link_operations_specialist: Optional[str] = None
    link_business_development_manager: Optional[str] = None
    link_deputy_general_manager: Optional[str] = None
    link_management_trainee_founders_office: Optional[str] = None
    link_general_manager_sales: Optional[str] = None
    link_sales_manager_retail: Optional[str] = None
    link_operations_supervisor: Optional[str] = None
    link_field_application_engineer: Optional[str] = None
    link_ai_engineer: Optional[str] = None
    mode: Optional[str] = None
    api_token: Optional[str] = None
    company: Optional[str] = None


class SendBody(BaseModel):
    calendly_url: Optional[str] = None          # legacy single link (optional)
    role_links: Optional[dict] = None           # {role_key: url}


@router.get("")
async def preview():
    """Round 1 candidate count + names, the saved per-role Calendly links, and
    a per-role breakdown so the dialog can show how many go to each link."""
    s = _load_settings()
    cands = _round1_candidates()
    # Count candidates per role key.
    by_role = {}
    for c in cands:
        rk = c.get("role_key") or ""
        by_role[rk] = by_role.get(rk, 0) + 1
    roles = [{
        "role_key": rk,
        "label": label,
        "count": by_role.get(rk, 0),
        "link": s.get(f"link_{rk}", ""),
    } for rk, label in ROLE_LINK_KEYS.items()]
    # Any candidates whose role isn't one of the known link roles.
    unmapped = sorted({c["role_key"] for c in cands if c["role_key"] not in ROLE_LINK_KEYS})
    return {"count": len(cands), "candidates": cands,
            "roles": roles, "unmapped_roles": unmapped,
            "calendly_url": s.get("default_link", ""), "mode": s.get("mode", "free")}


@router.get("/settings")
async def get_settings():
    return _mask(_load_settings())


@router.post("/settings")
async def post_settings(body: SettingsBody):
    data = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict(exclude_unset=True)
    # Validate every link-ish field that's present.
    for key in ("default_link", "link_customer_support_executive", "link_operations_specialist"):
        link = (data.get(key) or "").strip()
        if link and "calendly.com/" not in link.lower():
            raise HTTPException(status_code=400,
                                detail=f"{key} does not look like a Calendly URL.")
    return _save_settings(data)


@router.post("/send")
async def post_send(body: SendBody):
    import gmail_oauth
    if not gmail_oauth.is_connected():
        raise HTTPException(
            status_code=400,
            detail="Google account is not connected. Connect it on the Send Emails page first."
        )

    role_links = body.role_links or {}
    # Validate + persist the per-role links so future sends go straight through.
    persist = {}
    for rk, link in role_links.items():
        link = (link or "").strip()
        if rk not in ROLE_LINK_KEYS:
            continue
        if link and "calendly.com/" not in link.lower():
            raise HTTPException(status_code=400,
                                detail=f"The {ROLE_LINK_KEYS[rk]} link doesn't look like a Calendly link.")
        persist[f"link_{rk}"] = link
    legacy = (body.calendly_url or "").strip()
    if legacy and "calendly.com/" not in legacy.lower():
        raise HTTPException(status_code=400, detail="calendly_url does not look like a Calendly link.")
    if legacy:
        persist["default_link"] = legacy
    if persist:
        _save_settings(persist)

    try:
        return send_round1_invites(calendly_url=legacy or None, role_links=role_links)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
