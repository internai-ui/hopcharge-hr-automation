"""
colleges/store.py — Persistence layer for colleges.

A deliberately thin repository over a single JSON file (output/colleges.json),
matching the file-based persistence the rest of the app uses (candidates.json,
form_responses.json). Every public function here is the seam where a future
PostgreSQL/SQLAlchemy implementation would slot in without touching routers or
services — same signatures, same return types.

Concurrency: a module-level threading.Lock guards read-modify-write cycles so
concurrent FastAPI requests can't corrupt the file. For a single-process local
app this is sufficient; a real DB would replace this with row-level locking.

Duplicate prevention: a college is considered a duplicate if its normalised
name matches an existing record (case/space/punctuation-insensitive). Callers
can opt to update-in-place instead of inserting.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Optional

from config import OUTPUT_DIR
from colleges.schemas import CollegeRecord, _now_iso

logger = logging.getLogger("volt_cv.colleges")

COLLEGES_FILE: Path = OUTPUT_DIR / "colleges.json"
_LOCK = threading.Lock()


# ──────────────────────────────────────────────
# Normalisation helpers (duplicate detection)
# ──────────────────────────────────────────────

def normalise_name(name: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace for dup matching.
    'I.I.T. Delhi ' and 'IIT  Delhi' → 'iit delhi'.

    Dotted acronyms are a common real-world variation, so after stripping
    punctuation we also collapse runs of single characters separated by spaces
    (e.g. 'i i t' → 'iit') back into one token."""
    s = (name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)        # punctuation → space
    s = re.sub(r"\s+", " ", s).strip()    # collapse whitespace
    # Collapse sequences of single letters: "i i t delhi" → "iit delhi"
    s = re.sub(r"\b(?:[a-z]\s){2,}[a-z]\b",
               lambda m: m.group(0).replace(" ", ""), s)
    return s


# ──────────────────────────────────────────────
# Low-level file IO
# ──────────────────────────────────────────────

def _read_raw() -> list[dict]:
    # Reads through the dual-write layer (Postgres when reachable, else the
    # JSON mirror). Falls back to the local file if the layer is unavailable.
    try:
        import dual_writer
        data = dual_writer.read_dataset("colleges")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("colleges", [])
    except Exception:
        pass
    if not COLLEGES_FILE.exists():
        return []
    try:
        with open(COLLEGES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("colleges", [])
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read colleges file: %s", exc)
        return []


def _write_raw(rows: list[dict]) -> None:
    # Dual-write: Postgres + the JSON mirror. Every colleges mutation
    # (create/update/delete/bulk_upsert/replace_all) flows through here, so a
    # partial row edit still lands in both stores. Degrades to JSON-only if PG
    # is unreachable, and the next sync reconciles.
    try:
        import dual_writer
        dual_writer.write_dataset("colleges", rows)
        return
    except Exception:
        pass
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = COLLEGES_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    tmp.replace(COLLEGES_FILE)   # atomic on POSIX


# ──────────────────────────────────────────────
# Public repository API
# ──────────────────────────────────────────────

def list_all() -> list[CollegeRecord]:
    return [CollegeRecord(**row) for row in _read_raw()]


def get(college_id: str) -> Optional[CollegeRecord]:
    for row in _read_raw():
        if row.get("id") == college_id:
            return CollegeRecord(**row)
    return None


def find_by_name(name: str) -> Optional[CollegeRecord]:
    target = normalise_name(name)
    for row in _read_raw():
        if normalise_name(row.get("college_name", "")) == target:
            return CollegeRecord(**row)
    return None


def create(record: CollegeRecord, *, on_duplicate: str = "skip") -> tuple[CollegeRecord, str]:
    """
    Insert a college. Returns (record, action) where action ∈
    {"created", "updated", "skipped"}.

    on_duplicate:
      • "skip"   — leave existing record untouched, return it with "skipped"
      • "update" — merge non-empty fields from `record` into existing
      • "error"  — raise ValueError
    """
    with _LOCK:
        rows = _read_raw()
        target = normalise_name(record.college_name)

        for i, row in enumerate(rows):
            if normalise_name(row.get("college_name", "")) == target:
                existing = CollegeRecord(**row)
                if on_duplicate == "error":
                    raise ValueError(f"College already exists: {record.college_name}")
                if on_duplicate == "update":
                    merged = _merge(existing, record)
                    merged.updated_at = _now_iso()
                    rows[i] = merged.to_dict()
                    _write_raw(rows)
                    logger.info("Updated existing college: %s", merged.college_name)
                    return merged, "updated"
                logger.info("Skipped duplicate college: %s", record.college_name)
                return existing, "skipped"

        rows.append(record.to_dict())
        _write_raw(rows)
        logger.info("Created college: %s (%s)", record.college_name, record.id)
        return record, "created"


def update(college_id: str, patch: dict) -> Optional[CollegeRecord]:
    """Partial update by id. Only keys present in `patch` are written."""
    with _LOCK:
        rows = _read_raw()
        for i, row in enumerate(rows):
            if row.get("id") == college_id:
                for k, v in patch.items():
                    if v is not None:
                        row[k] = v
                row["updated_at"] = _now_iso()
                rows[i] = row
                _write_raw(rows)
                return CollegeRecord(**row)
    return None


def delete(college_id: str) -> bool:
    with _LOCK:
        rows = _read_raw()
        new_rows = [r for r in rows if r.get("id") != college_id]
        if len(new_rows) == len(rows):
            return False
        _write_raw(new_rows)
        logger.info("Deleted college: %s", college_id)
        return True


def bulk_upsert(records: list[CollegeRecord], *, on_duplicate: str = "skip") -> dict:
    """Efficient batch insert used by CSV/Excel import. Single read-write cycle."""
    summary = {"created": 0, "updated": 0, "skipped": 0}
    with _LOCK:
        rows = _read_raw()
        index = {normalise_name(r.get("college_name", "")): i for i, r in enumerate(rows)}

        for rec in records:
            key = normalise_name(rec.college_name)
            if key in index:
                if on_duplicate == "update":
                    existing = CollegeRecord(**rows[index[key]])
                    merged = _merge(existing, rec)
                    merged.updated_at = _now_iso()
                    rows[index[key]] = merged.to_dict()
                    summary["updated"] += 1
                else:
                    summary["skipped"] += 1
            else:
                rows.append(rec.to_dict())
                index[key] = len(rows) - 1
                summary["created"] += 1

        _write_raw(rows)
    logger.info("Bulk upsert: %s", summary)
    return summary


def replace_all(records: list[CollegeRecord]) -> None:
    """Used by the prioritiser to write back recomputed scores in one shot."""
    with _LOCK:
        _write_raw([r.to_dict() for r in records])


# ──────────────────────────────────────────────
# Internal
# ──────────────────────────────────────────────

def _merge(existing: CollegeRecord, incoming: CollegeRecord) -> CollegeRecord:
    """Overlay non-empty incoming fields onto existing. Preserves id/created_at."""
    base = existing.to_dict()
    for k, v in incoming.to_dict().items():
        if k in ("id", "created_at"):
            continue
        # Only overwrite when incoming carries a meaningful value
        if v not in ("", None, 0) or base.get(k) in ("", None):
            if v not in ("", None):
                base[k] = v
    return CollegeRecord(**base)
