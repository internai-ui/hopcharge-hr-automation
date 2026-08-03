"""
status_portal.py — Candidate-facing application status page (standalone, public).

A candidate opens ONE personal magic link (…/status/<token>) — sent via the
"Check Application Status" button in the recruitment emails — and sees a simple
progress stepper:  Applied → Under review → Interviews → Decision.

SECURITY MODEL (by design, not just a check)
  • Reached only through an unguessable 256-bit token that maps to exactly one
    candidate. Tokens are never sequential and are never listed anywhere public.
  • No list / search / count endpoint exists on the public surface, and the page
    never computes or sends a total — so the number of candidates can't leak.
  • An unknown/garbled token returns an identical neutral "not found" page, so the
    link can't be probed to discover which tokens are valid (no enumeration).
  • Public lookups are rate-limited per IP to block brute-forcing.
  • Scores, AI reasoning, and rejection reasons are NEVER sent to the browser —
    only a friendly stage label.
  • Responses carry no-store + noindex headers, so pages aren't cached or indexed.

Mount in app.py next to the other routers:
    from status_portal import router as status_router
    app.include_router(status_router)

Requires a public base URL (set on the Send Emails tab → stored in
output/tracking_config.json as public_base_url) for the email links to resolve.
The dashboard's own API stays local/unauthenticated as before; the ONLY public
surface added here is /status/<token>.
"""

from __future__ import annotations

import html
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.status_portal")

TOKENS_FILE = OUTPUT_DIR / "status_tokens.json"
SELECTED_FILE = OUTPUT_DIR / "selected_candidates.json"
RESPONSES_FILE = OUTPUT_DIR / "form_responses.json"
ACCEPTED_FILE = OUTPUT_DIR / "accepted_candidates.json"
REJECTED_FILE = OUTPUT_DIR / "rejected_candidates.json"
TRACKING_FILE = OUTPUT_DIR / "tracking_config.json"


# ──────────────────────────────────────────────
# Tiny JSON helpers
# ──────────────────────────────────────────────
def _read(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(email: str) -> str:
    return (email or "").strip().lower()


# ──────────────────────────────────────────────
# Token store (idempotent magic links, keyed by email)
# ──────────────────────────────────────────────
def _load_tokens() -> dict:
    d = _read(TOKENS_FILE, {})
    d.setdefault("tokens", {})     # token -> {email, created_at}
    d.setdefault("by_email", {})   # email -> token
    return d


def get_or_create_token(email: str) -> str:
    """Return a stable per-candidate token, minting one on first use."""
    e = _norm(email)
    if not e:
        return ""
    store = _load_tokens()
    existing = store["by_email"].get(e)
    if existing and existing in store["tokens"]:
        return existing
    token = secrets.token_urlsafe(32)            # 256-bit, URL-safe, unguessable
    store["tokens"][token] = {"email": e, "created_at": _now()}
    store["by_email"][e] = token
    _write(TOKENS_FILE, store)
    return token


def _resolve(token: str) -> Optional[str]:
    if not token or len(token) < 20:
        return None
    return _load_tokens()["tokens"].get(token, {}).get("email")


def get_base_url() -> str:
    cfg = _read(TRACKING_FILE, {})
    return (cfg.get("public_base_url") or "").strip().rstrip("/")


def status_url(base_url: str, email: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    token = get_or_create_token(email)
    if not base or not token:
        return ""
    return f"{base}/status/{token}"


def status_button_html(email: str, base_url: Optional[str] = None,
                       label: str = "Check Application Status") -> str:
    """Pink pill button for the recruitment emails. Returns '' when no public
    base URL is configured, so emails never show a broken link."""
    url = status_url(base_url if base_url is not None else get_base_url(), email)
    if not url:
        return ""
    return (
        '<div style="margin:0 0 18px">'
        f'<a href="{html.escape(url, quote=True)}" '
        'style="display:inline-block;background:#E8537F;color:#ffffff;font-size:15px;'
        'font-weight:700;text-decoration:none;padding:13px 30px;border-radius:28px">'
        f'{html.escape(label)} &rarr;</a></div>'
    )


# ──────────────────────────────────────────────
# Selection store ("Selected for the company")
# ──────────────────────────────────────────────
def _load_selected() -> dict:
    # Reads selected candidates through the dual-write layer (Postgres when
    # reachable, else the JSON mirror); falls back to the file directly.
    try:
        import dual_writer
        d = dual_writer.read_dataset("selected_candidates")
    except Exception:
        d = _read(SELECTED_FILE, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("selected", [])   # [{response_id, email, at}]
    return d


def _save_selected(store: dict) -> None:
    # Dual-write selected candidates: Postgres + JSON mirror, JSON-only if down.
    try:
        import dual_writer
        dual_writer.write_dataset("selected_candidates", store)
    except Exception:
        _write(SELECTED_FILE, store)


def selected_ids() -> set:
    return {s.get("response_id") for s in _load_selected()["selected"] if s.get("response_id")}


def _is_selected_email(email: str) -> bool:
    e = _norm(email)
    return any(_norm(s.get("email")) == e for s in _load_selected()["selected"])


def mark_selected(response_id: str) -> dict:
    store = _load_selected()
    if any(s.get("response_id") == response_id for s in store["selected"]):
        return {"ok": True, "already": True}
    email = ""
    for a in _accepted():
        if a.get("response_id") == response_id:
            email = a.get("email", "")
            break
    store["selected"].append({"response_id": response_id, "email": email, "at": _now()})
    _save_selected(store)
    return {"ok": True}


def unmark_selected(response_id: str) -> dict:
    store = _load_selected()
    store["selected"] = [s for s in store["selected"] if s.get("response_id") != response_id]
    _save_selected(store)
    return {"ok": True}


# ──────────────────────────────────────────────
# Store readers (matched by email)
# ──────────────────────────────────────────────
def _responses() -> list:
    return _read(RESPONSES_FILE, {}).get("responses", []) or []


def _accepted() -> list:
    return _read(ACCEPTED_FILE, {}).get("accepted", []) or []


def _rejected() -> list:
    return _read(REJECTED_FILE, {}).get("rejected", []) or []


def _find_response(email: str) -> Optional[dict]:
    e = _norm(email)
    for r in _responses():
        if _norm(r.get("email")) == e:
            return r
    return None


def _find_accepted(email: str) -> Optional[dict]:
    e = _norm(email)
    for a in _accepted():
        if _norm(a.get("email")) == e:
            return a
    return None


def _find_rejected(email: str) -> Optional[dict]:
    e = _norm(email)
    for r in _rejected():
        if _norm(r.get("email")) == e:
            return r
    return None


def _answer_matching(resp: dict, needles: list) -> str:
    for a in (resp.get("answers") or []):
        q = (a.get("question") or "").lower()
        if any(n in q for n in needles):
            val = (a.get("answer") or "").strip()
            if val:
                return val
    return ""


def _name_for(email: str) -> str:
    rec = _find_accepted(email) or _find_rejected(email)
    if rec and rec.get("name"):
        return rec["name"].strip()
    resp = _find_response(email)
    if resp:
        nm = _answer_matching(resp, ["full name", "your name", "name"])
        if nm:
            return nm
    local = _norm(email).split("@")[0].replace(".", " ").replace("_", " ")
    return local.title() if local else "there"


def _role_for(email: str) -> str:
    rec = _find_accepted(email) or _find_rejected(email)
    if rec and rec.get("role"):
        return str(rec["role"]).replace("_", " ").strip()
    resp = _find_response(email)
    if resp:
        return _answer_matching(resp, ["role applying", "role", "position"]).replace("_", " ")
    return ""


# ──────────────────────────────────────────────
# Stage computation  (Applied → Review → Interviews → Decision)
# ──────────────────────────────────────────────
def _steps(applied, review, interviews, decision):
    return [
        {"key": "applied", "label": "Applied", **applied},
        {"key": "review", "label": "Under review", **review},
        {"key": "interviews", "label": "Interviews", **interviews},
        {"key": "decision", "label": "Decision", **decision},
    ]


def compute_status(email: str) -> dict:
    name = _name_for(email)
    first = name.split()[0] if name else "there"
    role = _role_for(email)

    selected = _is_selected_email(email)
    rej = _find_rejected(email)
    acc = _find_accepted(email)
    resp = _find_response(email)

    if acc and (acc.get("stage") or "").lower() == "onboarded":
        # Final 'Onboarded' stage — hired and sent the onboarding form. Checked
        # before the generic 'selected' branch so onboarded candidates get the
        # richer welcome/onboarding message (an onboarded candidate is usually
        # also in the selected set).
        outcome = "selected"
        headline = "Welcome to Hopcharge \U0001F389"
        sub = ("Congratulations and welcome aboard! You've been selected and your "
               "onboarding is underway. Please check your email for the onboarding "
               "form and next steps.")
        steps = _steps(
            {"state": "done"}, {"state": "done"},
            {"state": "done", "note": "Cleared"},
            {"state": "done", "note": "Onboarding"},
        )
    elif selected:
        outcome = "selected"
        headline = "You're selected \U0001F389"
        sub = ("Congratulations! You've been selected to join Hopcharge. "
               "Our team will reach out with the next steps.")
        steps = _steps(
            {"state": "done"}, {"state": "done"},
            {"state": "done", "note": "Cleared"},
            {"state": "done", "note": "Selected"},
        )
    elif rej:
        outcome = "rejected"
        headline = "Application closed"
        sub = ("Thank you for your interest in Hopcharge and for the time you invested in the process. "
               "We won't be moving forward with your application at this stage. We genuinely appreciate "
               "it and wish you the very best ahead.")
        rr = (rej.get("rejected_round") or "").lower()
        reached = any(k in rr for k in ["round 1", "round1", "r1", "round 2", "round2", "r2", "hr", "interview"])
        steps = _steps(
            {"state": "done"}, {"state": "done"},
            {"state": "done", "note": "Completed"} if reached else {"state": "skipped"},
            {"state": "closed", "note": "Not selected"},
        )
    elif acc:
        outcome = "in_progress"
        stage = (acc.get("stage") or "hr").lower()
        stage_label = {"hr": "HR Round", "round1": "Round 1", "round2": "Final round",
                       "onboarded": "Onboarding"}.get(stage, "Interview")
        headline = "You're in the interview stage"
        sub = f"Congratulations — you've been shortlisted. You're currently at: {stage_label}."
        steps = _steps(
            {"state": "done"}, {"state": "done"},
            {"state": "current", "note": stage_label},
            {"state": "upcoming"},
        )
    elif resp:
        outcome = "review"
        headline = "Under review"
        sub = "Thanks for applying! Your application has been received and is being reviewed by our team."
        steps = _steps(
            {"state": "done"}, {"state": "current"},
            {"state": "upcoming"}, {"state": "upcoming"},
        )
    else:
        outcome = "applied"
        headline = "Application received"
        sub = "We've received your details. Complete your assessment form to move your application forward."
        steps = _steps(
            {"state": "current"}, {"state": "upcoming"},
            {"state": "upcoming"}, {"state": "upcoming"},
        )

    return {"name": name, "first_name": first, "role": role,
            "headline": headline, "sub": sub, "outcome": outcome, "steps": steps}


# ──────────────────────────────────────────────
# Themed HTML (matches the email aesthetic)
# ──────────────────────────────────────────────
_PAGE_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:0;background:#eef1f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,Helvetica,sans-serif;color:#1f2a44}
.wrap{max-width:560px;margin:34px auto;padding:0 14px}
.card{background:#fff;border:1px solid #e6e9f0;border-radius:14px;overflow:hidden;box-shadow:0 18px 50px rgba(31,45,89,.08)}
.head{background:#1F2D59;padding:26px 32px}
.brand{color:#fff;font-size:24px;font-weight:800;letter-spacing:-.5px}
.brand sup{font-size:11px;font-weight:600}
.eyebrow{color:#9FB0D8;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin-top:6px}
.body{padding:30px 32px}
.greet{font-size:13px;color:#8a93a6;margin:0 0 6px}
.headline{font-size:24px;font-weight:800;color:#1F2D59;margin:0 0 10px;line-height:1.2}
.sub{font-size:14.5px;line-height:1.65;color:#5b6472;margin:0 0 6px}
.role{display:inline-block;margin:14px 0 4px;font-size:12px;color:#5b6472;background:#f3f4f8;border:1px solid #e6e9f0;border-radius:20px;padding:5px 14px}
.stepper{list-style:none;margin:26px 0 4px;padding:0}
.step{position:relative;padding:0 0 30px 46px;min-height:34px}
.step:last-child{padding-bottom:0}
.step::before{content:'';position:absolute;left:16px;top:30px;bottom:2px;width:2px;background:#e1e5ee}
.step:last-child::before{display:none}
.step.done::before{background:#16a34a}
.dot{position:absolute;left:0;top:0;width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700}
.step.upcoming .dot{background:#fff;border:2px solid #cdd3df;color:#aab1c2}
.step.skipped .dot{background:#fff;border:2px dashed #cdd3df;color:#cdd3df}
.step.done .dot{background:#16a34a;color:#fff}
.step.closed .dot{background:#9aa3b6;color:#fff}
.step.current .dot{background:#2F5BEA;color:#fff;box-shadow:0 0 0 5px rgba(47,91,234,.16)}
.s-label{font-size:15px;font-weight:700;color:#1f2a44;line-height:34px}
.step.upcoming .s-label,.step.skipped .s-label{color:#9aa3b6;font-weight:600}
.s-note{font-size:12.5px;color:#5b6472;margin-top:-6px}
.step.current .s-note{color:#2F5BEA;font-weight:700}
.foot{padding:18px 32px 26px;border-top:1px solid #eef1f6;font-size:12.5px;color:#8a93a6}
.notice{padding:40px 32px;text-align:center}
.notice .headline{margin-bottom:12px}
@media(max-width:480px){.head,.body,.foot{padding-left:22px;padding-right:22px}}
"""


def _stepper(steps: list) -> str:
    out = []
    n = 0
    for s in steps:
        n += 1
        st = s.get("state", "upcoming")
        if st == "done":
            glyph = "\u2713"
        elif st == "closed":
            glyph = "\u2013"
        elif st == "current":
            glyph = "\u25CF"
        else:
            glyph = str(n)
        note = s.get("note") or ""
        note_html = f'<div class="s-note">{html.escape(note)}</div>' if note else ""
        out.append(
            f'<li class="step {st}"><span class="dot">{glyph}</span>'
            f'<div class="s-label">{html.escape(s["label"])}</div>{note_html}</li>'
        )
    return '<ol class="stepper">' + "".join(out) + "</ol>"


def _shell(inner_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="robots" content="noindex,nofollow">
<title>Application Status — Hopcharge</title>
<style>{_PAGE_CSS}</style>
</head><body>
  <div class="wrap"><div class="card">
    <div class="head"><div class="brand">Hopcharge</div><div class="eyebrow">Application Status</div></div>
    {inner_html}
  </div></div>
</body></html>"""


def _render_page(st: dict) -> str:
    role = f'<div class="role">Applied for: {html.escape(st["role"]).title()}</div>' if st.get("role") else ""
    body = f"""
    <div class="body">
      <p class="greet">Hi {html.escape(st['first_name'])},</p>
      <h1 class="headline">{html.escape(st['headline'])}</h1>
      <p class="sub">{html.escape(st['sub'])}</p>
      {role}
      {_stepper(st['steps'])}
    </div>
    <div class="foot">Questions about your application? Just reply to the email we sent you.</div>"""
    return _shell(body)


def _render_notice(title: str, message: str) -> str:
    body = f"""
    <div class="notice">
      <h1 class="headline">{html.escape(title)}</h1>
      <p class="sub">{html.escape(message)}</p>
    </div>"""
    return _shell(body)


# ──────────────────────────────────────────────
# Rate limiting + security headers
# ──────────────────────────────────────────────
_RATE: dict = {}
_RATE_LIMIT = 20      # requests
_RATE_WINDOW = 60     # seconds

_SEC_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_ok(ip: str) -> bool:
    now = time.time()
    q = _RATE.setdefault(ip, [])
    while q and q[0] < now - _RATE_WINDOW:
        q.pop(0)
    if len(q) >= _RATE_LIMIT:
        return False
    q.append(now)
    return True


# ──────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────
router = APIRouter(tags=["status"])


@router.get("/status", response_class=HTMLResponse)
async def status_root(request: Request):
    if not _rate_ok(_client_ip(request)):
        return HTMLResponse(_render_notice("Too many requests", "Please wait a minute and try again."),
                            status_code=429, headers=_SEC_HEADERS)
    return HTMLResponse(
        _render_notice("Open your personal link",
                       "To protect your privacy, application status can only be viewed through the "
                       "\u201cCheck Application Status\u201d button in the email we sent you."),
        status_code=200, headers=_SEC_HEADERS)


@router.get("/status/{token}", response_class=HTMLResponse)
async def status_page(token: str, request: Request):
    if not _rate_ok(_client_ip(request)):
        return HTMLResponse(_render_notice("Too many requests", "Please wait a minute and try again."),
                            status_code=429, headers=_SEC_HEADERS)
    email = _resolve(token)
    if not email:
        # identical neutral response for any unknown/garbled token (no enumeration)
        return HTMLResponse(
            _render_notice("Application not found",
                           "We couldn't find an application for this link. Please use the "
                           "\u201cCheck Application Status\u201d button in your email."),
            status_code=404, headers=_SEC_HEADERS)
    return HTMLResponse(_render_page(compute_status(email)), status_code=200, headers=_SEC_HEADERS)


# ── Internal selection API (dashboard only — same local surface as the rest) ──
class _SelBody(BaseModel):
    response_id: str


@router.get("/api/selection")
async def api_selection():
    return {"ids": sorted(selected_ids())}


@router.post("/api/selection/mark")
async def api_selection_mark(body: _SelBody):
    return mark_selected(body.response_id)


@router.post("/api/selection/unmark")
async def api_selection_unmark(body: _SelBody):
    return unmark_selected(body.response_id)
