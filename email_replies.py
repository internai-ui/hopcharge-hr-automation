"""
email_replies.py — track replies to bulk campaign emails sent via Gmail OAuth.

HOW IT WORKS
────────────────────────────────────────────────────────────────────────────
When emailer.send_campaign() sends a message through the Gmail API (OAuth
path — see gmail_oauth.py), Gmail returns a thread_id for that send. We
record one entry per thread here. A background poller (mirrors
dual_writer.py's _background_refresh() daemon-thread pattern) periodically
calls threads.get() for every thread still in "sent" status and checks
whether a message shows up whose sender isn't our own connected account —
that's a reply.

This module ONLY reads Gmail (gmail.readonly is enough — see gmail_oauth.py's
SCOPES). "Marking a reply read/handled" is a purely in-app flag; nothing
here ever calls the Gmail API to archive, label, or mark anything read in
the user's real inbox.

Storage: output/email_replies.json — same plain-JSON, threading.Lock()
pattern as form_tracking.py. Not part of dual_writer's Postgres sync (v1
scope, matching form_tracking.py's own precedent).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from googleapiclient.errors import HttpError

import gmail_oauth
from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.email_replies")

REPLIES_FILE: Path = OUTPUT_DIR / "email_replies.json"
POLL_INTERVAL = 300  # seconds — matches dual_writer.py's cache-refresh cadence

_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════
# Storage helpers
# ══════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    if not REPLIES_FILE.exists():
        return {"records": {}}
    try:
        data = json.loads(REPLIES_FILE.read_text(encoding="utf-8"))
        data.setdefault("records", {})
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("email_replies.json corrupt — starting fresh.")
        return {"records": {}}


def _save(data: dict) -> None:
    REPLIES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# 1. Register a sent campaign email (called from emailer.send_campaign)
# ══════════════════════════════════════════════════════════════════════════

def register_sent(thread_id: str, message_id: str, email: str,
                   name: str = "", form_link: str = "") -> None:
    if not thread_id:
        return
    email = (email or "").strip().lower()
    with _lock:
        data = _load()
        data["records"][thread_id] = {
            "candidate_email": email,
            "candidate_name": name,
            "form_link": form_link,
            "sent_message_id": message_id,
            "sent_at": _now_iso(),
            "status": "sent",       # sent → replied
            "reply": None,
            "read": True,            # nothing to read until a reply arrives
            "handled_at": None,
        }
        _save(data)
        logger.info("Registered sent campaign email for reply tracking: %s (thread=%s)", email, thread_id)


# ══════════════════════════════════════════════════════════════════════════
# 2. Poll Gmail for replies (background thread + manual "Check now")
# ══════════════════════════════════════════════════════════════════════════

def _header(headers: list[dict], name: str) -> str:
    name = name.lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == name:
            return h.get("value") or ""
    return ""


def _b64_decode(data: str) -> str:
    try:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


# Best-effort markers for where a reply's OWN text ends and the quoted
# thread history begins — the same patterns Gmail/Outlook themselves use to
# decide what to fold under "···" in their own UI. None of these are 100%
# reliable across every email client, but together they cover the large
# majority of real replies; worst case a stray quote line slips through,
# which is a cosmetic issue, not a correctness one.
_QUOTE_MARKERS_TEXT = [
    re.compile(r"^[ \t]*-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t]*-{2,}\s*Forwarded message\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^_{5,}\s*$", re.MULTILINE),
    re.compile(r"(?:\n|^)[ \t]*On\s+.*?\s+wrote:\s*", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bOn\b.{0,250}?\bwrote:\s*", re.IGNORECASE | re.DOTALL),
    re.compile(r"^[ \t]*From:\s.+\n(?:.*\n){0,3}?^(?:Sent|Date|To|Subject):\s.+$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t]*From:\s.+$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t]*>", re.MULTILINE),
]
_QUOTE_MARKER_HTML = re.compile(
    r"<div[^>]*class=\"[^\"]*gmail_quote[^\"]*\"|<blockquote\b", re.IGNORECASE)


def _strip_quoted_text(text: str) -> str:
    """Cut a plain-text reply body at the first quote marker found, so a
    reply email only shows the candidate's own new text, not the entire
    thread history re-quoted underneath it."""
    if not text:
        return ""
    cut = len(text)
    for pattern in _QUOTE_MARKERS_TEXT:
        m = pattern.search(text)
        if m and m.start() < cut:
            if m.start() > 0:
                cut = m.start()
    cleaned = text[:cut].rstrip()
    return cleaned if cleaned else text.strip()


def _strip_quoted_html(html: str) -> str:
    """Same idea as _strip_quoted_text but for the HTML body."""
    if not html:
        return html
    m = _QUOTE_MARKER_HTML.search(html)
    if m and m.start() > 0:
        return html[:m.start()]
    return html


def _detect_reply_intent(text: str) -> dict:
    """Classify reply text into intent category and display badge information."""
    clean = (text or "").lower()

    # 1. Not Interested / Opt Out
    not_interested_keywords = [
        "not interested", "no thanks", "don't contact", "dont contact",
        "leave me alone", "remove me", "unsubscribe", "not looking",
        "pass on this", "decline", "please stop", "no interest", "not open"
    ]
    for kw in not_interested_keywords:
        if kw in clean:
            return {
                "category": "not_interested",
                "label": "Not Interested",
                "badge_bg": "rgba(239, 68, 68, 0.18)",
                "badge_color": "#f87171",
                "icon": "🔴"
            }

    # 2. Out of Office / Auto Reply
    auto_reply_keywords = [
        "automatic reply", "out of office", "auto-reply", "auto reply",
        "currently away", "vacation responder", "on leave until"
    ]
    for kw in auto_reply_keywords:
        if kw in clean:
            return {
                "category": "auto_reply",
                "label": "Auto-Reply / OOO",
                "badge_bg": "rgba(156, 163, 175, 0.18)",
                "badge_color": "#9ca3af",
                "icon": "⚪"
            }

    # 3. Question / Inquiry
    question_keywords = [
        "?", "salary", "ctc", "location", "timings", "hybrid", "remote",
        "role details", "job description", "stipend", "duration"
    ]
    for kw in question_keywords:
        if kw in clean:
            return {
                "category": "question",
                "label": "Question / Inquiry",
                "badge_bg": "rgba(245, 158, 11, 0.18)",
                "badge_color": "#fbbf24",
                "icon": "🟡"
            }

    # 4. Interested / Applied
    interested_keywords = [
        "interested", "filled", "submitted", "completed", "attached",
        "looking forward", "thank you", "thanks for reaching", "glad to",
        "happy to", "available for interview", "schedule"
    ]
    for kw in interested_keywords:
        if kw in clean:
            return {
                "category": "interested",
                "label": "Interested / Form Submitted",
                "badge_bg": "rgba(52, 211, 153, 0.18)",
                "badge_color": "#34d399",
                "icon": "🟢"
            }

    # Default / General Response
    return {
        "category": "neutral",
        "label": "Replied",
        "badge_bg": "rgba(96, 165, 250, 0.18)",
        "badge_color": "#60a5fa",
        "icon": "🔵"
    }


def _extract_body(payload: dict) -> tuple[str, str]:
    """Walk a Gmail message payload's MIME parts, returning (text, html) —
    the full text/plain and text/html bodies found."""
    text = ""
    html = ""

    def walk(part: dict) -> None:
        nonlocal text, html
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain" and not text:
            text = _b64_decode(data)
        elif data and mime == "text/html" and not html:
            html = _b64_decode(data)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload or {})
    return text, html


def _make_preview(body_text: str, fallback: str, limit: int = 160) -> str:
    """Short one-line preview for the Replies table — derived from the
    already quote-stripped body_text, not Gmail's own raw snippet (which
    can include quote-header text like "On Tue, ... wrote:" right after the
    real reply when a client doesn't put the quote on its own line)."""
    text = " ".join((_strip_quoted_text(body_text) or "").split())
    if not text:
        text = " ".join((fallback or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _internal_date_to_iso(ms: Optional[str]) -> Optional[str]:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def poll_once() -> dict:
    """Check every open ("sent") tracked thread for a reply."""
    if not gmail_oauth.is_connected():
        return {"checked": 0, "new_replies": 0, "skipped": "not_connected"}

    try:
        service = gmail_oauth.get_gmail_service()
    except gmail_oauth.GmailNotConnectedError:
        return {"checked": 0, "new_replies": 0, "skipped": "not_connected"}

    connected_email = (gmail_oauth.public_status().get("connected_email") or "").strip().lower()

    checked = 0
    new_replies = 0
    with _lock:
        data = _load()
        changed = False
        for thread_id, rec in data["records"].items():
            if rec.get("status") != "sent":
                continue
            checked += 1
            try:
                thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
            except HttpError as exc:
                logger.warning("poll_once: threads.get failed for %s: %s", thread_id, exc)
                continue

            reply_msg = None
            for msg in (thread.get("messages") or [])[1:]:  # skip our own first message
                headers = (msg.get("payload") or {}).get("headers", [])
                _, addr = parseaddr(_header(headers, "From"))
                if addr.strip().lower() != connected_email:
                    reply_msg = msg
                    break
            if not reply_msg:
                continue

            headers = (reply_msg.get("payload") or {}).get("headers", [])
            body_text, body_html = _extract_body(reply_msg.get("payload") or {})
            clean_text = _strip_quoted_text(body_text or reply_msg.get("snippet", ""))
            intent = _detect_reply_intent(clean_text or body_text or reply_msg.get("snippet", ""))

            rec["status"] = "replied"
            rec["reply"] = {
                "from": _header(headers, "From"),
                "snippet": _make_preview(body_text, reply_msg.get("snippet", "")),
                "clean_text": clean_text,
                "intent": intent,
                "body_text": body_text,
                "body_html": body_html,
                "received_at": _internal_date_to_iso(reply_msg.get("internalDate")),
                "message_id": reply_msg.get("id"),
            }
            rec["read"] = False
            changed = True
            new_replies += 1
            logger.info("New reply detected from %s (thread=%s)", rec.get("candidate_email"), thread_id)

        if changed:
            _save(data)

    return {"checked": checked, "new_replies": new_replies}


def _poller_loop() -> None:
    """Daemon thread, started at import — mirrors dual_writer.py's
    _background_refresh() exactly (no FastAPI startup hook exists anywhere
    in this app)."""
    import time as _time
    while True:
        _time.sleep(POLL_INTERVAL)
        try:
            poll_once()
        except Exception as exc:
            logger.error("email_replies background poll failed: %s", exc, exc_info=True)


_poller_thread = threading.Thread(target=_poller_loop, daemon=True)
_poller_thread.start()


# ══════════════════════════════════════════════════════════════════════════
# 3. Read tracking data for the dashboard — in-app only, never touches Gmail
# ══════════════════════════════════════════════════════════════════════════

def list_replies(status: str = "all") -> list[dict]:
    data = _load()
    rows = []
    for thread_id, rec in data["records"].items():
        if status == "unread" and not (rec.get("status") == "replied" and not rec.get("read", True)):
            continue
        row = {
            "thread_id": thread_id,
            "candidate_email": rec.get("candidate_email"),
            "candidate_name": rec.get("candidate_name"),
            "status": rec.get("status"),
            "sent_at": rec.get("sent_at"),
            "read": rec.get("read", True),
        }
        reply = rec.get("reply")
        if reply:
            row["reply_from"] = reply.get("from")
            row["reply_snippet"] = reply.get("snippet")
            row["received_at"] = reply.get("received_at")
            
            raw_text = reply.get("body_text", "")
            clean_text = reply.get("clean_text") or _strip_quoted_text(raw_text or reply.get("snippet", ""))
            intent = reply.get("intent") or _detect_reply_intent(clean_text or raw_text or reply.get("snippet", ""))

            row["clean_text"] = clean_text
            row["intent"] = intent
            row["clean_snippet"] = clean_text if clean_text else reply.get("snippet", "")

            # Filter by intent category if specified
            if status in ["not_interested", "interested", "question", "auto_reply"] and intent.get("category") != status:
                continue

        rows.append(row)
    rows.sort(key=lambda r: r.get("received_at") or r.get("sent_at") or "", reverse=True)
    return rows


def get_reply_detail(thread_id: str) -> Optional[dict]:
    data = _load()
    rec = data["records"].get(thread_id)
    if not rec:
        return None
    row = dict(rec)
    row["thread_id"] = thread_id
    if rec.get("reply"):
        reply = dict(rec["reply"])
        raw_text = reply.get("body_text", "")
        clean_text = reply.get("clean_text") or _strip_quoted_text(raw_text or reply.get("snippet", ""))
        intent = reply.get("intent") or _detect_reply_intent(clean_text or raw_text or reply.get("snippet", ""))
        reply["clean_text"] = clean_text
        reply["intent"] = intent
        row["reply"] = reply
    return row


def mark_read(thread_id: str, read: bool = True) -> Optional[dict]:
    with _lock:
        data = _load()
        rec = data["records"].get(thread_id)
        if not rec:
            return None
        rec["read"] = read
        rec["handled_at"] = _now_iso() if read else None
        _save(data)
        row = dict(rec)
        row["thread_id"] = thread_id
        return row


def replies_summary() -> dict:
    data = _load()
    rows = list(data["records"].values())
    return {
        "total": len(rows),
        "replied": sum(1 for r in rows if r.get("status") == "replied"),
        "unread": sum(1 for r in rows if r.get("status") == "replied" and not r.get("read", True)),
    }


# ══════════════════════════════════════════════════════════════════════════
# FastAPI router
# ══════════════════════════════════════════════════════════════════════════

router = APIRouter(prefix="/api/email-replies", tags=["email-replies"])


@router.get("")
async def get_replies(status: str = "all"):
    return {"success": True, "replies": list_replies(status), "summary": replies_summary()}


@router.get("/{thread_id}")
async def get_reply(thread_id: str):
    rec = get_reply_detail(thread_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Reply thread not found.")
    return {"success": True, "reply": rec}


@router.post("/check-now")
async def post_check_now():
    try:
        result = poll_once()
    except gmail_oauth.GmailNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, **result}


@router.post("/{thread_id}/mark-read")
async def post_mark_read(thread_id: str, read: bool = True):
    rec = mark_read(thread_id, read)
    if not rec:
        raise HTTPException(status_code=404, detail="Reply thread not found.")
    return {"success": True, "reply": rec}
