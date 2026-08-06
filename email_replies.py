"""
email_replies.py — track replies to bulk campaign emails sent via Gmail OAuth.

HOW IT WORKS
────────────────────────────────────────────────────────────────────────────
When emailer.send_campaign() sends a message through the Gmail API (OAuth
path — see gmail_oauth.py), Gmail returns a thread_id for that send. We
record one entry per thread here. A background poller (mirrors
dual_writer.py's _background_refresh() daemon-thread pattern) periodically
calls threads.get() for EVERY tracked thread (not just ones with no reply
yet) and diffs the full message list against what we've already stored,
by Gmail message id. Every message not seen before is recorded, tagged
is_candidate=True/False by comparing its From address against the
connected account — so a full back-and-forth (candidate replies, a manual
follow-up sent from real Gmail, the candidate replying again, and so on)
is captured as a real conversation, not just a single first reply.

This module ONLY reads Gmail (gmail.readonly is enough — see gmail_oauth.py's
SCOPES). "Marking a reply read/handled" is a purely in-app flag; nothing
here ever calls the Gmail API to archive, label, or mark anything read in
the user's real inbox.

Storage: output/email_replies.json — same plain-JSON, threading.Lock()
pattern as form_tracking.py. Not part of dual_writer's Postgres sync (v1
scope, matching form_tracking.py's own precedent).
"""

from __future__ import annotations

import asyncio
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


def _migrate_record(rec: dict) -> dict:
    """Older records stored a single `reply` dict (first reply only). Fold
    it into the new `thread_messages` list (one entry) so existing history
    isn't lost when this ships. Idempotent — a no-op once migrated. Runs on
    every load; only persisted to disk the next time this record is saved
    by poll_once()/mark_read(), same lazy-write pattern used elsewhere."""
    if "thread_messages" not in rec:
        old = rec.pop("reply", None)
        if old:
            entry = dict(old)
            entry["is_candidate"] = True
            entry.setdefault("message_id", f"legacy-{rec.get('sent_message_id', '')}")
            rec["thread_messages"] = [entry]
        else:
            rec["thread_messages"] = []
    rec.pop("reply", None)
    return rec


def _load() -> dict:
    if not REPLIES_FILE.exists():
        return {"records": {}}
    try:
        data = json.loads(REPLIES_FILE.read_text(encoding="utf-8"))
        data.setdefault("records", {})
        for rec in data["records"].values():
            _migrate_record(rec)
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
            "thread_messages": [],
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
    # Gmail's quote-attribution line: "On <weekday>, <month> <day>, <year>
    # at <time> <name> <email> wrote:". Anchored on the distinctive
    # weekday+month+year date stamp (not just "On ... wrote:") so it can't
    # false-positive on a candidate's own sentence like "On the form I
    # wrote: ..." or "...I already wrote a detailed cover letter" — and
    # deliberately NOT anchored to a line start, since some clients run the
    # quote header on directly after the reply's last sentence with no
    # separating newline.
    re.compile(
        r"\bOn\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}"
        r".{0,150}?\bwrote:",
        re.IGNORECASE | re.DOTALL,
    ),
    # Outlook-style header block: a "From:" line followed within a few lines
    # by Sent:/Date:/To:/Subject: — narrower than a bare "From:" match so a
    # candidate's own reply starting a line with "From:" isn't cut short.
    re.compile(r"^[ \t]*From:\s.+\n(?:.*\n){0,3}?^(?:Sent|Date|To|Subject):\s.+$", re.IGNORECASE | re.MULTILINE),
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


_INTENT_CATEGORIES = {
    "not_interested": {"label": "Not Interested", "badge_bg": "rgba(239, 68, 68, 0.18)", "badge_color": "#f87171"},
    "auto_reply": {"label": "Auto-Reply / OOO", "badge_bg": "rgba(156, 163, 175, 0.18)", "badge_color": "#9ca3af"},
    "question": {"label": "Question / Inquiry", "badge_bg": "rgba(245, 158, 11, 0.18)", "badge_color": "#fbbf24"},
    "interested": {"label": "Interested / Form Submitted", "badge_bg": "rgba(52, 211, 153, 0.18)", "badge_color": "#34d399"},
    "neutral": {"label": "Replied", "badge_bg": "rgba(96, 165, 250, 0.18)", "badge_color": "#60a5fa"},
}

_INTENT_SYSTEM_PROMPT = (
    "Classify a candidate's email reply to a recruitment campaign into exactly one category.\n"
    "Categories:\n"
    "- not_interested: candidate declines, opts out, or asks to stop being contacted\n"
    "- auto_reply: an automatic out-of-office reply, or an automated mail-delivery-failure/"
    "bounce notice -- not a real reply typed by a person\n"
    "- question: candidate asks about the role (salary, location, timing, requirements) "
    "without yet confirming interest\n"
    "- interested: candidate confirms interest, says they've submitted or will submit the "
    "form, or is otherwise clearly engaged\n"
    "- neutral: none of the above clearly applies\n"
    'Return ONLY this JSON: {"category":"<one of the five category names above>"}'
)


def _detect_reply_intent(text: str) -> dict:
    """Fast, fully offline keyword classifier. Used as the always-available
    fallback when AI-based classification (_classify_intent_ai) is off,
    unconfigured, or fails -- and directly by read paths (list_replies/
    get_reply_detail's legacy-data fallback) which must never make a
    network call on a GET request."""
    clean = (text or "").lower()

    keyword_rules = [
        ("not_interested", [
            "not interested", "no thanks", "don't contact", "dont contact",
            "leave me alone", "remove me", "unsubscribe", "not looking",
            "pass on this", "decline", "please stop", "no interest", "not open",
        ]),
        ("auto_reply", [
            "automatic reply", "out of office", "auto-reply", "auto reply",
            "currently away", "vacation responder", "on leave until",
        ]),
        ("question", [
            "?", "salary", "ctc", "location", "timings", "hybrid", "remote",
            "role details", "job description", "stipend", "duration",
        ]),
        ("interested", [
            "interested", "filled", "submitted", "completed", "attached",
            "looking forward", "thank you", "thanks for reaching", "glad to",
            "happy to", "available for interview", "schedule",
        ]),
    ]
    for category, keywords in keyword_rules:
        if any(kw in clean for kw in keywords):
            return {"category": category, **_INTENT_CATEGORIES[category]}

    return {"category": "neutral", **_INTENT_CATEGORIES["neutral"]}


def _classify_intent_ai(text: str) -> Optional[dict]:
    """Try classifying via the same AI-provider infra as AI-based CV parsing
    (ai_config_store/ai_providers -- Claude/OpenAI/Gemini/Groq/HuggingFace).
    More reliable than keyword matching for cases keywords get wrong -- e.g.
    a Gmail delivery-failure bounce that happens to contain a "?" in a URL
    reads as auto_reply to an LLM, not "question" the way a bare "?" search
    would classify it. Returns None (never raises) when AI parsing is off,
    unconfigured, or the call fails for any reason -- callers fall back to
    _detect_reply_intent in that case, mirroring exactly how AI-based CV
    parsing falls back to the offline parser."""
    try:
        import ai_config_store
        from ai_providers import get_provider
    except ImportError:
        return None
    try:
        if not ai_config_store.is_available():
            return None
        provider = get_provider(ai_config_store.get_runtime_config())
        out = provider.complete_json(_INTENT_SYSTEM_PROMPT, (text or "")[:4000])
        category = (out or {}).get("category")
        info = _INTENT_CATEGORIES.get(category)
        if not info:
            return None
        return {"category": category, **info}
    except Exception as exc:
        logger.warning("AI reply-intent classification failed, falling back to keywords: %s", exc)
        return None


def _classify_intent(text: str) -> dict:
    """Primary entry point for classifying a NEW candidate reply at
    record-time (poll_once() only -- never call this from a read path, it
    may make a blocking network call). Tries AI classification first,
    falls back to keyword matching on any failure so this always returns
    something and a flaky/unconfigured provider never blocks a poll cycle."""
    return _classify_intent_ai(text) or _detect_reply_intent(text)


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


def _build_message_entry(msg: dict, connected_email: str) -> dict:
    """Extract + classify one Gmail message into our stored shape. Does all
    the CPU/network-bound work up front -- including AI intent
    classification, which may call an LLM -- so callers can do this OUTSIDE
    _lock and only merge the ready-made entries under the lock as a cheap,
    purely in-memory step (same principle as keeping Gmail API calls out
    of the lock)."""
    headers = (msg.get("payload") or {}).get("headers", [])
    _, addr = parseaddr(_header(headers, "From"))
    is_candidate = addr.strip().lower() != connected_email

    body_text, body_html = _extract_body(msg.get("payload") or {})
    entry = {
        "message_id": msg.get("id"),
        "from": _header(headers, "From"),
        "is_candidate": is_candidate,
        "received_at": _internal_date_to_iso(msg.get("internalDate")),
        "snippet": _make_preview(body_text, msg.get("snippet", "")),
        "body_text": body_text,
        "body_html": body_html,
    }
    if is_candidate:
        clean_text = _strip_quoted_text(body_text or msg.get("snippet", ""))
        entry["clean_text"] = clean_text
        entry["intent"] = _classify_intent(clean_text or body_text or msg.get("snippet", ""))
    return entry


def _record_entry(rec: dict, entry: dict) -> bool:
    """Append a pre-built message entry (see _build_message_entry) to
    rec['thread_messages'] if not already recorded (dedup by Gmail message
    id). Returns True iff it was a new CANDIDATE message (used by callers
    to flip status/read). Purely in-memory -- safe to call while holding
    _lock."""
    msgs = rec.setdefault("thread_messages", [])
    msg_id = entry.get("message_id")
    if not msg_id or any(m.get("message_id") == msg_id for m in msgs):
        return False
    msgs.append(entry)
    return bool(entry.get("is_candidate"))


def _merge_messages(data: dict, messages_by_thread: dict) -> int:
    """Merge pre-built message entries (grouped by thread_id, see
    _build_message_entry) into the JSON store and flip status/read for any
    thread that got a new candidate message. Caller holds _lock and saves
    afterward. Returns new_replies (threads with >=1 new candidate message
    this cycle, not raw message count — matches the original per-thread
    semantics)."""
    new_replies = 0
    for thread_id, entries in messages_by_thread.items():
        rec = data["records"].get(thread_id)
        if rec is None:
            continue  # not (or no longer) one of our tracked campaign threads
        new_candidate = False
        for entry in entries:
            if _record_entry(rec, entry):
                new_candidate = True
        if new_candidate:
            rec["status"] = "replied"
            rec["read"] = False
            new_replies += 1
            logger.info("New reply detected from %s (thread=%s)", rec.get("candidate_email"), thread_id)
    return new_replies


def poll_once() -> dict:
    """Check for new messages on every tracked thread.

    Steady state uses Gmail's history.list for incremental sync: one API
    call (a couple more only if the mailbox has been very active) tells us
    every message added anywhere in the mailbox since the last poll,
    regardless of how many threads we're tracking — O(1) per cycle, not
    O(tracked threads). Falls back to the old full per-thread walk only
    when there's no historyId yet (first-ever poll) or Gmail reports ours
    has expired (~7 days of inactivity, HTTP 404) — both rare, and both
    self-healing: a fallback run ends by capturing a fresh historyId so the
    next cycle goes back to incremental.

    All Gmail API calls happen OUTSIDE _lock; the lock is only held around
    the in-memory merge + JSON save, so a slow/large sync never blocks
    mark_read() or other readers for its full duration.
    """
    if not gmail_oauth.is_connected():
        return {"checked": 0, "new_replies": 0, "skipped": "not_connected"}

    try:
        service = gmail_oauth.get_gmail_service()
    except gmail_oauth.GmailNotConnectedError:
        return {"checked": 0, "new_replies": 0, "skipped": "not_connected"}

    connected_email = (gmail_oauth.public_status().get("connected_email") or "").strip().lower()
    start_history_id = gmail_oauth.get_history_id()

    with _lock:
        snapshot = _load()["records"]
        tracked_thread_ids = set(snapshot.keys())
        seen_message_ids = {
            m.get("message_id")
            for rec in snapshot.values()
            for m in (rec.get("thread_messages") or [])
            if m.get("message_id")
        }

    if start_history_id:
        try:
            new_refs = []  # (message_id, thread_id), only for threads we track
            page_token = None
            next_history_id = None
            while True:
                resp = service.users().history().list(
                    userId="me", startHistoryId=start_history_id,
                    historyTypes=["messageAdded"], pageToken=page_token,
                ).execute()
                for h in resp.get("history", []):
                    for added in h.get("messagesAdded", []):
                        m = added.get("message") or {}
                        msg_id = m.get("id")
                        # Skip refetching messages we've already recorded --
                        # history.list can legitimately re-report a message
                        # if ranges overlap (e.g. after a fallback resync).
                        if msg_id and msg_id not in seen_message_ids and m.get("threadId") in tracked_thread_ids:
                            new_refs.append((msg_id, m["threadId"]))
                next_history_id = resp.get("historyId") or next_history_id
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            messages_by_thread: dict = {}
            for msg_id, thread_id in new_refs:
                try:
                    msg_full = service.users().messages().get(
                        userId="me", id=msg_id, format="full").execute()
                except HttpError as exc:
                    logger.warning("poll_once: messages.get failed for %s: %s", msg_id, exc)
                    continue
                # Classification (possibly an LLM call) happens here, still
                # outside _lock.
                entry = _build_message_entry(msg_full, connected_email)
                messages_by_thread.setdefault(thread_id, []).append(entry)

            with _lock:
                data = _load()
                checked = len(data["records"])
                new_replies = _merge_messages(data, messages_by_thread)
                if messages_by_thread:
                    _save(data)

            if next_history_id:
                gmail_oauth.set_history_id(next_history_id)
            return {"checked": checked, "new_replies": new_replies}

        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status == 404:
                logger.info("Gmail historyId expired — falling back to a full resync.")
            else:
                logger.warning("history.list failed (%s) — falling back to a full resync.", exc)
            # fall through to the full resync below

    # ── Full resync: no historyId yet, or Gmail expired ours. One-time
    # O(tracked threads) walk, same cost as before this fix — only reached
    # here, never on a normal cycle. ──
    thread_payloads = []
    for thread_id in tracked_thread_ids:
        try:
            thread_payloads.append((thread_id, service.users().threads().get(
                userId="me", id=thread_id, format="full").execute()))
        except HttpError as exc:
            logger.warning("full resync: threads.get failed for %s: %s", thread_id, exc)

    # Classification (possibly an LLM call per NEW candidate message)
    # happens here, still outside _lock. Skip messages we've already
    # recorded/classified in a previous cycle -- a full resync re-fetches
    # each tracked thread's ENTIRE message list from Gmail, so without this
    # filter every already-known message would get needlessly reclassified
    # (and re-billed, for a paid LLM provider) every time a resync runs.
    messages_by_thread: dict = {}
    for thread_id, thread in thread_payloads:
        messages_by_thread[thread_id] = [
            _build_message_entry(m, connected_email)
            for m in (thread.get("messages") or [])
            if m.get("id") not in seen_message_ids
        ]

    with _lock:
        data = _load()
        checked = len(data["records"])
        new_replies = _merge_messages(data, messages_by_thread)
        if messages_by_thread:
            _save(data)

    try:
        profile = service.users().getProfile(userId="me").execute()
        gmail_oauth.set_history_id(profile.get("historyId"))
    except HttpError as exc:
        logger.warning("Could not capture historyId after full resync: %s", exc)

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

def _latest_candidate_message(rec: dict) -> Optional[dict]:
    for m in reversed(rec.get("thread_messages", []) or []):
        if m.get("is_candidate"):
            return m
    return None


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
            "reply_count": sum(1 for m in rec.get("thread_messages", []) or [] if m.get("is_candidate")),
        }
        latest = _latest_candidate_message(rec)
        if latest:
            row["reply_from"] = latest.get("from")
            row["reply_snippet"] = latest.get("snippet")
            row["received_at"] = latest.get("received_at")

            raw_text = latest.get("body_text", "")
            clean_text = latest.get("clean_text") or _strip_quoted_text(raw_text or latest.get("snippet", ""))
            intent = latest.get("intent") or _detect_reply_intent(clean_text or raw_text or latest.get("snippet", ""))

            row["clean_text"] = clean_text
            row["intent"] = intent
            row["clean_snippet"] = clean_text if clean_text else latest.get("snippet", "")

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
    messages = []
    for m in rec.get("thread_messages", []) or []:
        m = dict(m)
        if m.get("is_candidate"):
            raw_text = m.get("body_text", "")
            clean_text = m.get("clean_text") or _strip_quoted_text(raw_text or m.get("snippet", ""))
            intent = m.get("intent") or _detect_reply_intent(clean_text or raw_text or m.get("snippet", ""))
            m["clean_text"] = clean_text
            m["intent"] = intent
        messages.append(m)
    row["thread_messages"] = messages
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
    # poll_once() makes blocking network calls (googleapiclient isn't async)
    # and can take a while on a full resync -- run it off the event loop so
    # it doesn't stall every other request/user while it's in flight.
    try:
        result = await asyncio.to_thread(poll_once)
    except gmail_oauth.GmailNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, **result}


@router.post("/{thread_id}/mark-read")
async def post_mark_read(thread_id: str, read: bool = True):
    rec = mark_read(thread_id, read)
    if not rec:
        raise HTTPException(status_code=404, detail="Reply thread not found.")
    return {"success": True, "reply": rec}
