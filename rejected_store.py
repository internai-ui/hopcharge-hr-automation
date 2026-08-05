"""
rejected_store.py — Persistent store + API for rejected candidates.

When HR clicks "Reject" on a candidate in Form Responses, the candidate's full
profile (name, phone, email, every form answer) is copied into
output/rejected_candidates.json together with WHICH ROUND they were
rejected in and when. The Rejected tab in the UI reads from this store.

A candidate can also be restored (un-rejected), which removes them from the
store. Rejecting is idempotent per response_id: rejecting the same candidate
again just updates the round/notes instead of duplicating.

Storage shape (output/rejected_candidates.json):
{
  "rejected": [
    {
      "response_id":  "ACYDBNh…",
      "name":         "Rishab Agrawal",
      "email":        "rishab@example.com",
      "phone":        "9266042587",
      "role":         "Customer Support Executive",
      "answers":      [ {question, answer}, ... ],   # full form answers
      "rejected_round":  "Round 0 — Form Screening",
      "rejected_reason": "optional free text",
      "rejected_at":     "2026-06-10T09:15:00Z",
      "rejection_email_sent_at": null,   # set once the rejection notice is sent
    }, ...
  ]
}
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.rejected")

REJECTED_FILE: Path = OUTPUT_DIR / "rejected_candidates.json"
RESPONSES_FILE: Path = OUTPUT_DIR / "form_responses.json"

_lock = threading.Lock()

# The rounds HR can reject a candidate in. The UI shows these in a dropdown;
# free-text is also accepted so the pipeline can grow without code changes.
DEFAULT_ROUNDS = [
    "Round 0 — Form Screening",
    "Round 1 — Telephonic Interview",
    "Round 2 — Technical / Role Interview",
    "Round 3 — HR / Final Interview",
    "Offer Stage",
]


# ──────────────────────────────────────────────
# Storage helpers
# ──────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    # Reads through the dual-write layer (Postgres when reachable, else the
    # JSON mirror). Falls back to the local file if the layer is unavailable.
    try:
        import dual_writer
        data = dual_writer.read_dataset("rejected_candidates")
        if "rejected" not in data:
            data = {"rejected": []}
        return data
    except Exception:
        pass
    if not REJECTED_FILE.exists():
        return {"rejected": []}
    try:
        data = json.loads(REJECTED_FILE.read_text())
        if "rejected" not in data:
            data = {"rejected": []}
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("rejected_candidates.json corrupt — starting fresh.")
        return {"rejected": []}


def _save(data: dict) -> None:
    # Dual-write: Postgres + JSON mirror. Degrades to JSON-only if PG is down.
    try:
        import dual_writer
        dual_writer.write_dataset("rejected_candidates", data)
    except Exception:
        REJECTED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _load_responses() -> list[dict]:
    if not RESPONSES_FILE.exists():
        return []
    try:
        return json.loads(RESPONSES_FILE.read_text()).get("responses", [])
    except (json.JSONDecodeError, OSError):
        return []


def _pick(answers: list[dict], keys: list[str]) -> str:
    """Find the first answer whose question matches any key (case-insensitive)."""
    for key in keys:
        for a in answers:
            q = (a.get("question") or "").lower()
            if key in q:
                v = (a.get("answer") or "").strip()
                if v:
                    return v
    return ""


# ──────────────────────────────────────────────
# Core operations
# ──────────────────────────────────────────────

def _build_reject_entry(src: dict, round_name: str, reason: str = "") -> dict:
    """Build a rejected-store entry from a raw form response (no I/O)."""
    answers = src.get("answers", [])
    return {
        "response_id":     src.get("response_id"),
        "name":            _pick(answers, ["full name", "name"]) or "Anonymous",
        "email":           src.get("email") or _pick(answers, ["personal email", "email"]),
        "phone":           _pick(answers, ["phone", "mobile", "contact"]),
        "role":            src.get("role_name")
                           or _pick(answers, ["role applying", "role"]),
        "answers":         answers,                      # full Q&A preserved
        "rejected_round":  (round_name or "").strip() or DEFAULT_ROUNDS[0],
        "rejected_reason": (reason or "").strip(),
        "rejected_at":     _now_iso(),
        "rejection_email_sent_at": None,   # set by mark_rejection_emailed() once notified
    }


def reject_candidate(response_id: str, round_name: str,
                     reason: str = "") -> dict:
    """
    Copy the candidate's full record from form_responses.json into the
    rejected store, stamped with the round and time. Idempotent by id.
    """
    responses = _load_responses()
    src = next((r for r in responses if r.get("response_id") == response_id), None)
    if src is None:
        raise KeyError(f"No form response with id {response_id!r}")

    entry = _build_reject_entry(src, round_name, reason)

    with _lock:
        data = _load()
        # Idempotent: replace existing entry for the same response_id — but
        # carry forward rejection_email_sent_at so re-rejecting (e.g. a
        # different round/reason) doesn't erase the record that a rejection
        # notice was already sent.
        existing = next((r for r in data["rejected"] if r.get("response_id") == response_id), None)
        if existing is not None:
            entry["rejection_email_sent_at"] = existing.get("rejection_email_sent_at")
        data["rejected"] = [r for r in data["rejected"]
                            if r.get("response_id") != response_id]
        data["rejected"].append(entry)
        _save(data)

    logger.info("Rejected %s (%s) in %s", entry["name"], entry["email"],
                entry["rejected_round"])
    return entry


def restore_candidate(response_id: str) -> bool:
    """Remove a candidate from the rejected store (undo). True if removed."""
    with _lock:
        data = _load()
        before = len(data["rejected"])
        data["rejected"] = [r for r in data["rejected"]
                            if r.get("response_id") != response_id]
        removed = len(data["rejected"]) < before
        if removed:
            _save(data)
    if removed:
        logger.info("Restored candidate %s from rejected list", response_id)
    return removed


def mark_rejection_emailed(response_id: str) -> bool:
    """Stamp a rejected candidate as having been sent the rejection notice.
    Called by app.py's /api/send-rejection after a genuinely successful
    send (not on failure) so the UI can show who's already been notified.
    True if the candidate was found and updated."""
    with _lock:
        data = _load()
        entry = next((r for r in data["rejected"] if r.get("response_id") == response_id), None)
        if entry is None:
            return False
        entry["rejection_email_sent_at"] = _now_iso()
        _save(data)
    return True


def list_rejected() -> list[dict]:
    """All rejected candidates, most recently rejected first."""
    data = _load()
    return sorted(data["rejected"],
                  key=lambda r: r.get("rejected_at") or "", reverse=True)


def rejected_ids() -> set[str]:
    """Set of response_ids currently rejected (for badge state in the UI)."""
    return {r.get("response_id") for r in _load()["rejected"]}


# ──────────────────────────────────────────────
# FastAPI router  (mounted in app.py)
# ──────────────────────────────────────────────

router = APIRouter(prefix="/api/rejected", tags=["rejected"])


class RejectBody(BaseModel):
    response_id: str = Field(..., min_length=1)
    round_name: str = Field("", max_length=120)
    reason: str = Field("", max_length=500)


@router.get("")
async def get_rejected():
    """List all rejected candidates + available round names."""
    return {
        "rejected": list_rejected(),
        "rounds": DEFAULT_ROUNDS,
    }


@router.get("/ids")
async def get_rejected_ids():
    """Just the ids — lets the responses table mark rejected rows cheaply."""
    return {"ids": sorted(rejected_ids())}


@router.post("")
async def post_reject(body: RejectBody):
    """Reject a candidate: copies their full record into the rejected store."""
    try:
        entry = reject_candidate(body.response_id, body.round_name, body.reason)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "entry": entry}


@router.delete("/{response_id}")
async def delete_rejected(response_id: str):
    """Restore (un-reject) a candidate."""
    if not restore_candidate(response_id):
        raise HTTPException(status_code=404, detail="Candidate not in rejected list.")
    return {"success": True}
