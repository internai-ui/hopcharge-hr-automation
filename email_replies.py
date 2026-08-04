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


def _extract_body(payload: dict) -> tuple[str, str]:
    """Walk a Gmail message payload's MIME parts, returning (text, html) —
    the first text/plain and first text/html bodies found."""
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


def _internal_date_to_iso(ms: Optional[str]) -> Optional[str]:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def poll_once() -> dict:
    """Check every open ("sent") tracked thread for a reply. Cheap no-op
    when Gmail isn't connected. Holds _lock for the whole pass (Gmail API
    calls included) — simplest way to avoid a lost-update race against
    register_sent()/mark_read(), and call volume here is low (tens of
    threads at most) so the brief lock hold is not a real bottleneck."""
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
            rec["status"] = "replied"
            rec["reply"] = {
                "from": _header(headers, "From"),
                "snippet": reply_msg.get("snippet", ""),
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
