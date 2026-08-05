"""
accepted_store.py — Persistent store + API for accepted candidates.

Mirror of rejected_store.py. When HR clicks "Accept" on a candidate in Form
Responses, the candidate's full profile (name, phone, email, every form answer)
is copied into output/accepted_candidates.json together with WHICH ROUND they
were accepted in and when.

For now the UI's Accepted section only surfaces candidates accepted in
"Round 1", but the store keeps the round on every entry so future rounds can be
shown without a data migration.

Storage shape (output/accepted_candidates.json):
{
  "accepted": [
    {
      "response_id": "...", "name": "...", "email": "...", "phone": "...",
      "role": "...", "answers": [ {question, answer}, ... ],
      "accepted_round": "Round 1 — Telephonic Interview",
      "accepted_note": "optional free text",
      "accepted_at": "2026-06-10T09:15:00Z"
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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.accepted")

ACCEPTED_FILE: Path = OUTPUT_DIR / "accepted_candidates.json"
RESPONSES_FILE: Path = OUTPUT_DIR / "form_responses.json"

_lock = threading.Lock()

# Accepted candidates move through four fixed stages, in order.
# Accepting from Form Responses always lands a candidate in the first stage (hr).
# 'onboarded' is the terminal stage: a selected candidate who has been sent the
# onboarding form and is effectively hired. They stay preserved here, out of the
# active Round 2 working list.
STAGES = ["hr", "round1", "round2", "onboarded"]
STAGE_LABELS = {
    "hr":         "HR Round",
    "round1":     "Round 1",
    "round2":     "Round 2",
    "onboarded":  "Onboarded",
}


def _next_stage(stage: str) -> str | None:
    """The stage a candidate is promoted into, or None if already at the end."""
    try:
        i = STAGES.index(stage)
    except ValueError:
        return None
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


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
        data = dual_writer.read_dataset("accepted_candidates")
        if "accepted" not in data:
            data = {"accepted": []}
        return data
    except Exception:
        pass
    if not ACCEPTED_FILE.exists():
        return {"accepted": []}
    try:
        data = json.loads(ACCEPTED_FILE.read_text())
        if "accepted" not in data:
            data = {"accepted": []}
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("accepted_candidates.json corrupt — starting fresh.")
        return {"accepted": []}


def _save(data: dict) -> None:
    # Dual-write: Postgres + JSON mirror. Degrades to JSON-only if PG is down.
    try:
        import dual_writer
        dual_writer.write_dataset("accepted_candidates", data)
    except Exception:
        ACCEPTED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


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

def _build_accept_entry(src: dict, note: str = "") -> dict:
    """Build an accepted-store entry from a raw form response (no I/O)."""
    answers = src.get("answers", [])
    now = _now_iso()
    return {
        "response_id":     src.get("response_id"),
        "name":            _pick(answers, ["full name", "name"]) or "Anonymous",
        "email":           src.get("email") or _pick(answers, ["personal email", "email"]),
        "phone":           _pick(answers, ["phone", "mobile", "contact"]),
        "role":            src.get("role_name")
                           or _pick(answers, ["role applying", "role"]),
        "answers":         answers,                      # full Q&A preserved
        "stage":           STAGES[0],                    # always enters at HR Round
        "accepted_note":   (note or "").strip(),
        "accepted_at":     now,
        "stage_changed_at": now,
        # audit trail of stage transitions
        "history":         [{"stage": STAGES[0], "at": now}],
    }


def accept_candidate(response_id: str, note: str = "") -> dict:
    """
    Copy the candidate's full record from form_responses.json into the accepted
    store at the FIRST stage (HR Round). Stamped with stage + time. Idempotent
    by id — re-accepting resets the candidate to the HR Round.
    """
    responses = _load_responses()
    src = next((r for r in responses if r.get("response_id") == response_id), None)
    if src is None:
        raise KeyError(f"No form response with id {response_id!r}")

    entry = _build_accept_entry(src, note)

    with _lock:
        data = _load()
        # Idempotent: replace existing entry for the same response_id
        data["accepted"] = [r for r in data["accepted"]
                            if r.get("response_id") != response_id]
        data["accepted"].append(entry)
        _save(data)

    logger.info("Accepted %s (%s) into %s", entry["name"], entry["email"],
                STAGE_LABELS[entry["stage"]])
    return entry


def promote_candidate(response_id: str) -> dict:
    """
    Move a candidate to the next stage (hr → round1 → round2). Raises KeyError
    if not found, ValueError if already at the final stage.
    """
    with _lock:
        data = _load()
        entry = next((r for r in data["accepted"]
                      if r.get("response_id") == response_id), None)
        if entry is None:
            raise KeyError(f"No accepted candidate with id {response_id!r}")
        cur = entry.get("stage", STAGES[0])
        nxt = _next_stage(cur)
        if nxt is None:
            raise ValueError(f"Candidate already at final stage ({STAGE_LABELS.get(cur, cur)}).")
        now = _now_iso()
        entry["stage"] = nxt
        entry["stage_changed_at"] = now
        entry.setdefault("history", []).append({"stage": nxt, "at": now})
        _save(data)
    logger.info("Promoted %s → %s", entry.get("name"), STAGE_LABELS[nxt])
    return entry


def demote_candidate(response_id: str) -> dict:
    """Move a candidate back one stage (round2 → round1 → hr). For undo."""
    with _lock:
        data = _load()
        entry = next((r for r in data["accepted"]
                      if r.get("response_id") == response_id), None)
        if entry is None:
            raise KeyError(f"No accepted candidate with id {response_id!r}")
        cur = entry.get("stage", STAGES[0])
        i = STAGES.index(cur) if cur in STAGES else 0
        if i == 0:
            raise ValueError("Candidate is already at the first stage (HR Round).")
        prev = STAGES[i - 1]
        now = _now_iso()
        entry["stage"] = prev
        entry["stage_changed_at"] = now
        entry.setdefault("history", []).append({"stage": prev, "at": now})
        _save(data)
    logger.info("Moved %s back → %s", entry.get("name"), STAGE_LABELS[prev])
    return entry


def remove_candidate(response_id: str) -> bool:
    """Remove a candidate from the accepted store entirely (undo accept)."""
    with _lock:
        data = _load()
        before = len(data["accepted"])
        data["accepted"] = [r for r in data["accepted"]
                            if r.get("response_id") != response_id]
        removed = len(data["accepted"]) < before
        if removed:
            _save(data)
    if removed:
        logger.info("Removed candidate %s from accepted list", response_id)
    return removed


def _migrate(entry: dict) -> dict:
    """Back-compat: older entries used 'accepted_round' free-text; map to a stage."""
    if "stage" not in entry:
        entry["stage"] = STAGES[0]
    return entry


def list_accepted(stage: str | None = None) -> list[dict]:
    """Accepted candidates, most recent first; optionally filtered by stage."""
    data = _load()
    rows = [_migrate(r) for r in data["accepted"]]
    if stage:
        rows = [r for r in rows if r.get("stage") == stage]
    return sorted(rows, key=lambda r: r.get("stage_changed_at") or r.get("accepted_at") or "", reverse=True)


def stage_counts() -> dict:
    """Count of candidates in each stage (for tab badges)."""
    data = _load()
    counts = {s: 0 for s in STAGES}
    for r in data["accepted"]:
        st = _migrate(r).get("stage", STAGES[0])
        if st in counts:
            counts[st] += 1
    return counts


def accepted_ids() -> set[str]:
    """Set of response_ids currently accepted (for badge state in the UI)."""
    return {r.get("response_id") for r in _load()["accepted"]}


# ──────────────────────────────────────────────
# FastAPI router  (mounted in app.py)
# ──────────────────────────────────────────────

router = APIRouter(prefix="/api/accepted", tags=["accepted"])


class AcceptBody(BaseModel):
    response_id: str = Field(..., min_length=1)
    note: str = Field("", max_length=500)


class IdBody(BaseModel):
    response_id: str = Field(..., min_length=1)


@router.get("")
async def get_accepted(stage: str | None = None):
    """
    List accepted candidates, optionally filtered to one stage
    (stage = 'hr' | 'round1' | 'round2'). Also returns stage metadata
    and per-stage counts for the UI tabs.
    """
    return {
        "accepted": list_accepted(stage),
        "stages": STAGES,
        "stage_labels": STAGE_LABELS,
        "counts": stage_counts(),
    }


@router.get("/ids")
async def get_accepted_ids():
    """Just the ids — lets the responses table mark accepted rows cheaply."""
    return {"ids": sorted(accepted_ids())}


@router.post("")
async def post_accept(body: AcceptBody):
    """Accept a candidate from Form Responses → enters at the HR Round stage."""
    try:
        entry = accept_candidate(body.response_id, body.note)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "entry": entry}


@router.post("/promote")
async def post_promote(body: IdBody):
    """Promote a candidate to the next stage (hr → round1 → round2)."""
    try:
        entry = promote_candidate(body.response_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"success": True, "entry": entry, "stage_label": STAGE_LABELS[entry["stage"]]}


@router.post("/demote")
async def post_demote(body: IdBody):
    """Move a candidate back one stage (round2 → round1 → hr)."""
    try:
        entry = demote_candidate(body.response_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"success": True, "entry": entry, "stage_label": STAGE_LABELS[entry["stage"]]}


@router.delete("/{response_id}")
async def delete_accepted(response_id: str):
    """Remove (un-accept) a candidate entirely.

    A candidate's notes follow them through every pipeline stage; they are
    cleaned up only here, when the candidate is removed from the pipeline
    (including from the onboarded stage). Promote/demote keep the notes.
    """
    if not remove_candidate(response_id):
        raise HTTPException(status_code=404, detail="Candidate not in accepted list.")
    # Clear this candidate's notes now that they're out of the pipeline.
    try:
        from notes_store import delete_all_notes
        delete_all_notes(response_id)
    except Exception as exc:
        logger.warning("Could not clear notes for removed candidate %s: %s", response_id, exc)
    return {"success": True}
