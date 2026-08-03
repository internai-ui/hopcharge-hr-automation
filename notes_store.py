"""
notes_store.py — Per-candidate notes (append-only timeline), keyed by response_id.

A candidate's notes follow them across every stage (Form Responses, Accepted/
HR Round, Rejected) because response_id is the universal candidate key used by
all the other stores. Notes are append-only: each note is timestamped and kept
in order, so you get a running history of remarks across interview rounds
rather than a single overwritten field.

JSON-only storage (output/candidate_notes.json), matching the current app state.
A future move to dual-write would slot in at _load/_save exactly like the other
stores.

Storage shape (output/candidate_notes.json):
{
  "notes": {
    "<response_id>": [
      {
        "id":       "n_ab12cd34",
        "text":     "Strong on ownership; follow up on availability.",
        "stage":    "Round 1 — Telephonic",   # free text, optional
        "author":   "",                         # optional, reserved for later
        "created_at": "2026-06-26T09:15:00Z"
      },
      ...
    ],
    ...
  }
}
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.notes")

NOTES_FILE: Path = OUTPUT_DIR / "candidate_notes.json"
_lock = threading.Lock()


# ──────────────────────────────────────────────
# Storage helpers
# ──────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return "n_" + secrets.token_hex(4)


def _load() -> dict:
    if not NOTES_FILE.exists():
        return {"notes": {}}
    try:
        data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        if "notes" not in data or not isinstance(data["notes"], dict):
            data = {"notes": {}}
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("candidate_notes.json corrupt — starting fresh.")
        return {"notes": {}}


def _save(data: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = NOTES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(NOTES_FILE)   # atomic on POSIX


# ──────────────────────────────────────────────
# Core operations
# ──────────────────────────────────────────────

def list_notes(response_id: str) -> list[dict]:
    """All notes for a candidate, oldest-first (timeline order)."""
    data = _load()
    notes = data["notes"].get(response_id, [])
    return sorted(notes, key=lambda n: n.get("created_at") or "")


def add_note(response_id: str, text: str, stage: str = "", author: str = "") -> dict:
    """Append a new timestamped note. Returns the created note."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Note text cannot be empty.")
    entry = {
        "id":         _new_id(),
        "text":       text,
        "stage":      (stage or "").strip(),
        "author":     (author or "").strip(),
        "created_at": _now_iso(),
    }
    with _lock:
        data = _load()
        data["notes"].setdefault(response_id, []).append(entry)
        _save(data)
    logger.info("Added note %s for candidate %s", entry["id"], response_id)
    return entry


def delete_note(response_id: str, note_id: str) -> bool:
    """Remove a single note by id. True if removed."""
    with _lock:
        data = _load()
        notes = data["notes"].get(response_id)
        if not notes:
            return False
        before = len(notes)
        data["notes"][response_id] = [n for n in notes if n.get("id") != note_id]
        removed = len(data["notes"][response_id]) < before
        # Clean up empty lists so the file doesn't accumulate dead keys.
        if not data["notes"][response_id]:
            data["notes"].pop(response_id, None)
        if removed:
            _save(data)
    if removed:
        logger.info("Deleted note %s for candidate %s", note_id, response_id)
    return removed


def note_counts() -> dict:
    """Map of response_id -> note count, for cheap row indicators in the UI."""
    data = _load()
    return {rid: len(notes) for rid, notes in data["notes"].items() if notes}


def delete_all_notes(response_id: str) -> bool:
    """Remove every note for a candidate (used when they leave the pipeline)."""
    with _lock:
        data = _load()
        if response_id in data["notes"]:
            data["notes"].pop(response_id, None)
            _save(data)
            logger.info("Cleared all notes for candidate %s", response_id)
            return True
    return False


# ──────────────────────────────────────────────
# FastAPI router  (mounted in app.py)
# ──────────────────────────────────────────────

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NoteBody(BaseModel):
    text:   str = Field(..., min_length=1, max_length=4000)
    stage:  str = Field("", max_length=120)
    author: str = Field("", max_length=120)


@router.get("/counts")
async def get_counts():
    """All note counts keyed by response_id — lets the table mark rows cheaply."""
    return {"success": True, "counts": note_counts()}


@router.get("/{response_id}")
async def get_notes(response_id: str):
    """List a candidate's notes (oldest first)."""
    return {"success": True, "notes": list_notes(response_id)}


@router.post("/{response_id}")
async def post_note(response_id: str, body: NoteBody):
    """Append a note to a candidate."""
    try:
        entry = add_note(response_id, body.text, body.stage, body.author)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "note": entry}


@router.delete("/{response_id}/{note_id}")
async def delete_note_route(response_id: str, note_id: str):
    """Delete a single note."""
    if not delete_note(response_id, note_id):
        raise HTTPException(status_code=404, detail="Note not found.")
    return {"success": True}
