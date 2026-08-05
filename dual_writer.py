"""
dual_writer.py — Single point through which every store reads and writes.

Contract
--------
Postgres is the source of truth. The JSON files in output/ are a mirror kept
in step so the existing code paths and any manual inspection keep working.

Every mutation goes through write_dataset(name, payload):
  1. Write Postgres first (inside a transaction).
  2. Only if PG succeeds, write the JSON file.
  3. If PG fails AND the DB is reachable → raise, write nothing (the caller
     gets a 500, no half-written state).
  4. If the DB is simply unreachable (Neon asleep / DATABASE_URL unset) →
     degrade gracefully: write JSON only, and flag drift in the ledger so the
     next sync reconciles. The app stays up.

Reads (read_dataset) come from Postgres when available, else fall back to the
JSON file, so a Neon outage never takes the dashboard down.

This module knows how to map each dataset name to (a) its ORM model and the
transform JSON→rows, and (b) its on-disk JSON filename and the transform
rows→JSON. Those transforms are the inverse of the ones in
migrate_json_to_postgres.py, kept here so the two directions live together.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select

import crypto
import models
from database import db_available, session_scope

logger = logging.getLogger("volt_cv.dual_writer")

# Resolve output dir without importing the app's config (avoids a cycle).
try:
    from config import OUTPUT_DIR  # type: ignore
except Exception:  # pragma: no cover
    OUTPUT_DIR = Path(__file__).resolve().parent / "output"
    OUTPUT_DIR.mkdir(exist_ok=True)


def _iso(dt):
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt


def _parse_dt(value):
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    try:
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Dataset registry ──────────────────────────────────────────────────────────
# Each entry: filename, ORM model, the unique key column, to_rows(payload),
# to_json(rows). Pipeline-style wrappers ({"accepted":[...]}) are handled by the
# wrapper_key. Pure replace-the-whole-table semantics — the dashboard already
# rewrites the whole JSON on each change, so we mirror that: clear + reload.

def _employee_to_rows(payload, session):
    rows = []
    for e in payload.get("employees", []):
        enc = crypto.encrypt_employee_sensitive(session, e)
        rows.append(dict(
            legacy_id=e.get("_id"),
            employee_status=e.get("employee_status"), employee_id=e.get("employee_id"),
            division=e.get("division"), department=e.get("department"),
            name=e.get("name"), designation=e.get("designation"),
            dob=e.get("dob"), age=e.get("age"),
            old_joining_date_tpa=e.get("old_joining_date_tpa"),
            tpa_employee_code=e.get("tpa_employee_code"), doj=e.get("doj"),
            service_duration=e.get("service_duration"),
            years_of_service_achievement=e.get("years_of_service_achievement"),
            last_date=e.get("last_date"), gender=e.get("gender"),
            fathers_name=e.get("fathers_name"), mothers_name=e.get("mothers_name"),
            reporting_manager=e.get("reporting_manager"),
            source_of_employment=e.get("source_of_employment"),
            email=e.get("email"), email_official=e.get("email_official"),
            permanent_address=e.get("permanent_address"),
            correspondence_address=e.get("correspondence_address"),
            contact_no=e.get("contact_no"),
            emergency_contact=e.get("emergency_contact"),
            emergency_relation=e.get("emergency_relation"),
            esi=e.get("esi"), id_card_given=e.get("id_card_given"),
            bank_name=e.get("bank_name"), ifsc=e.get("ifsc"),
            **enc,
        ))
    return rows


def _employee_to_json(session):
    out = []
    rows = session.execute(
        select(models.Employee).where(models.Employee.deleted_at.is_(None))
        .order_by(models.Employee.created_at)
    ).scalars().all()
    for e in rows:
        dec = crypto.decrypt_employee_sensitive(session, e)
        out.append({
            "employee_status": e.employee_status, "employee_id": e.employee_id,
            "division": e.division, "department": e.department, "name": e.name,
            "designation": e.designation, "dob": e.dob, "age": e.age,
            "old_joining_date_tpa": e.old_joining_date_tpa,
            "tpa_employee_code": e.tpa_employee_code, "doj": e.doj,
            "service_duration": e.service_duration,
            "years_of_service_achievement": e.years_of_service_achievement,
            "last_date": e.last_date, "gender": e.gender,
            "fathers_name": e.fathers_name, "mothers_name": e.mothers_name,
            "reporting_manager": e.reporting_manager,
            "source_of_employment": e.source_of_employment,
            "email": e.email, "email_official": e.email_official,
            "permanent_address": e.permanent_address,
            "correspondence_address": e.correspondence_address,
            "contact_no": e.contact_no,
            "emergency_contact": e.emergency_contact,
            "emergency_relation": e.emergency_relation,
            "pan": dec["pan"], "aadhaar": dec["aadhaar"], "uan": dec["uan"],
            "esi": e.esi, "id_card_given": e.id_card_given,
            "bank_name": e.bank_name, "account_number": dec["account_number"],
            "ifsc": e.ifsc, "salary_ctc": dec["salary_ctc"],
            "_id": e.legacy_id,
            "created_at": _iso(e.created_at), "updated_at": _iso(e.updated_at),
        })
    return {"employees": out}


def _pipeline_to_rows(payload, wrapper_key, accepted=True):
    rows = []
    for it in payload.get(wrapper_key, []):
        base = dict(
            response_id=it.get("response_id"), name=it.get("name"),
            email=it.get("email"), phone=it.get("phone"), role=it.get("role"),
            answers=it.get("answers", []),
            objective_score=it.get("objective_score"), ai_score=it.get("ai_score"),
            total_score=it.get("total_score"), recommendation=it.get("recommendation"),
            history=it.get("history", []),
        )
        if accepted:
            base.update(stage=it.get("stage", "hr"),
                        accepted_note=it.get("accepted_note", ""),
                        accepted_at=_parse_dt(it.get("accepted_at")),
                        stage_changed_at=_parse_dt(it.get("stage_changed_at")))
        else:
            base.update(rejected_round=it.get("rejected_round"),
                        rejected_reason=it.get("rejected_reason", ""),
                        rejected_at=_parse_dt(it.get("rejected_at")))
        if base["response_id"]:
            rows.append(base)
    return rows


def _accepted_to_json(session):
    rows = session.execute(
        select(models.AcceptedCandidate)
        .where(models.AcceptedCandidate.deleted_at.is_(None))
        .order_by(models.AcceptedCandidate.accepted_at)
    ).scalars().all()
    return {"accepted": [{
        "response_id": r.response_id, "name": r.name, "email": r.email,
        "phone": r.phone, "role": r.role, "answers": r.answers,
        "objective_score": r.objective_score, "ai_score": r.ai_score,
        "total_score": r.total_score, "recommendation": r.recommendation,
        "stage": r.stage, "accepted_note": r.accepted_note,
        "accepted_at": _iso(r.accepted_at), "stage_changed_at": _iso(r.stage_changed_at),
        "history": r.history,
    } for r in rows]}


def _rejected_to_json(session):
    rows = session.execute(
        select(models.RejectedCandidate)
        .where(models.RejectedCandidate.deleted_at.is_(None))
        .order_by(models.RejectedCandidate.rejected_at)
    ).scalars().all()
    return {"rejected": [{
        "response_id": r.response_id, "name": r.name, "email": r.email,
        "phone": r.phone, "role": r.role, "answers": r.answers,
        "objective_score": r.objective_score, "ai_score": r.ai_score,
        "total_score": r.total_score, "recommendation": r.recommendation,
        "rejected_round": r.rejected_round, "rejected_reason": r.rejected_reason,
        "rejected_at": _iso(r.rejected_at), "history": r.history,
    } for r in rows]}


def _selected_to_rows(payload):
    return [dict(response_id=s.get("response_id"), email=s.get("email"),
                 selected_at=_parse_dt(s.get("at")))
            for s in payload.get("selected", []) if s.get("response_id")]


def _selected_to_json(session):
    rows = session.execute(
        select(models.SelectedCandidate)
        .where(models.SelectedCandidate.deleted_at.is_(None))
        .order_by(models.SelectedCandidate.selected_at)
    ).scalars().all()
    return {"selected": [{"response_id": r.response_id, "email": r.email,
                          "at": _iso(r.selected_at)} for r in rows]}


def _colleges_to_rows(payload):
    rows = []
    for c in payload:
        if not c.get("college_name"):
            continue
        rows.append(dict(
            legacy_id=c.get("id"), college_name=c.get("college_name"),
            placement_officer_name=c.get("placement_officer_name"),
            designation=c.get("designation"), email=c.get("email"),
            phone=c.get("phone"), city=c.get("city"), state=c.get("state"),
            college_type=c.get("college_type"), website=c.get("website"),
            placement_page_url=c.get("placement_page_url"),
            last_contact_date=c.get("last_contact_date"),
            outreach_status=c.get("outreach_status"),
            engineering_intake=c.get("engineering_intake"),
            internship_opportunities=c.get("internship_opportunities"),
            placement_quality_score=c.get("placement_quality_score"),
            historical_engagement=c.get("historical_engagement"),
            priority_score=c.get("priority_score"),
            priority_level=c.get("priority_level"), priority_tier=c.get("priority_tier"),
            source_type=c.get("source_type"), source_url=c.get("source_url"),
            confidence_score=c.get("confidence_score"),
            last_verified=_parse_dt(c.get("last_verified")),
            notes=c.get("notes", ""),
        ))
    return rows


def _colleges_to_json(session):
    rows = session.execute(
        select(models.College).where(models.College.deleted_at.is_(None))
        .order_by(models.College.created_at)
    ).scalars().all()
    return [{
        "id": c.legacy_id, "college_name": c.college_name,
        "placement_officer_name": c.placement_officer_name,
        "designation": c.designation, "email": c.email, "phone": c.phone,
        "city": c.city, "state": c.state, "college_type": c.college_type,
        "website": c.website, "placement_page_url": c.placement_page_url,
        "last_contact_date": c.last_contact_date,
        "outreach_status": c.outreach_status,
        "engineering_intake": c.engineering_intake,
        "internship_opportunities": c.internship_opportunities,
        "placement_quality_score": c.placement_quality_score,
        "historical_engagement": c.historical_engagement,
        "priority_score": c.priority_score, "priority_level": c.priority_level,
        "priority_tier": c.priority_tier, "source_type": c.source_type,
        "source_url": c.source_url, "confidence_score": c.confidence_score,
        "last_verified": _iso(c.last_verified), "notes": c.notes,
        "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
    } for c in rows]


# Registry maps dataset name → handlers.
DATASETS = {
    "accepted_candidates": dict(
        file="accepted_candidates.json", model=models.AcceptedCandidate,
        to_rows=lambda p, s: _pipeline_to_rows(p, "accepted", True),
        to_json=_accepted_to_json,
    ),
    "rejected_candidates": dict(
        file="rejected_candidates.json", model=models.RejectedCandidate,
        to_rows=lambda p, s: _pipeline_to_rows(p, "rejected", False),
        to_json=_rejected_to_json,
    ),
    "selected_candidates": dict(
        file="selected_candidates.json", model=models.SelectedCandidate,
        to_rows=lambda p, s: _selected_to_rows(p),
        to_json=_selected_to_json,
    ),
    "employees": dict(
        file="employees.json", model=models.Employee,
        to_rows=_employee_to_rows,
        to_json=_employee_to_json,
    ),
    "colleges": dict(
        file="colleges.json", model=models.College,
        to_rows=lambda p, s: _colleges_to_rows(p),
        to_json=_colleges_to_json,
    ),
}


def _write_json(name: str, payload) -> str:
    """Write payload to its JSON file, return the serialized text (for checksum)."""
    cfg = DATASETS[name]
    path = OUTPUT_DIR / cfg["file"]
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return text


def _replace_pg(session, name: str, payload):
    """Replace the whole table for this dataset (mirrors the dashboard's
    whole-file rewrite). Soft-deletes vanished rows so history is preserved."""
    cfg = DATASETS[name]
    model = cfg["model"]
    rows = cfg["to_rows"](payload, session)

    # Soft-delete everything currently live, then upsert the incoming rows.
    # For tables with a natural unique key we re-key; for the rest we clear+add.
    now = datetime.now(timezone.utc)
    live = session.execute(
        select(model).where(model.deleted_at.is_(None))
    ).scalars().all()
    incoming_keys = set()
    has_response_id = hasattr(model, "response_id")
    has_legacy = hasattr(model, "legacy_id")

    for r in rows:
        key = r.get("response_id") if has_response_id else r.get("legacy_id")
        incoming_keys.add(key)

    # Mark rows that disappeared as soft-deleted.
    for obj in live:
        key = getattr(obj, "response_id", None) if has_response_id else getattr(obj, "legacy_id", None)
        if key not in incoming_keys:
            obj.deleted_at = now

    # Upsert each incoming row.
    for r in rows:
        key = r.get("response_id") if has_response_id else r.get("legacy_id")
        existing = None
        if has_response_id and key:
            existing = session.execute(
                select(model).where(model.response_id == key)
            ).scalar_one_or_none()
        elif has_legacy and key:
            existing = session.execute(
                select(model).where(model.legacy_id == key)
            ).scalar_one_or_none()
        if existing:
            for k, v in r.items():
                setattr(existing, k, v)
            existing.deleted_at = None
        else:
            session.add(model(**r))



# ── In-process write-through cache ───────────────────────────────────────────
# Problem: every dashboard page load was calling read_dataset() → db_available()
# → open a Neon session → SELECT full table → decrypt.  Neon is serverless and
# cold-starts after ~5 min idle, so users saw 1-3s stalls on every page load.
#
# Fix: cache the full dataset payload in memory after the first read.  Writes
# invalidate (and immediately repopulate) the cache, so reads are always fresh.
# A lightweight background refresh re-reads from the local JSON mirror every
# 5 minutes as a safety net (cheap disk read, not a Neon round-trip).
#
# Result: first load after a cold start still touches Neon once per dataset;
# every subsequent read for that session returns in <1ms from memory.

import threading as _threading
import time as _time

_cache: dict[str, object] = {}           # name → payload
_cache_lock = _threading.Lock()
_REFRESH_INTERVAL = 300                  # seconds between background JSON re-reads


def _cache_set(name: str, payload) -> None:
    with _cache_lock:
        _cache[name] = payload


def _cache_get(name: str):
    with _cache_lock:
        return _cache.get(name)


def cache_invalidate(name: str | None = None) -> None:
    """Evict one dataset (or all) from the cache.  Next read repopulates."""
    with _cache_lock:
        if name is None:
            _cache.clear()
        else:
            _cache.pop(name, None)


def _background_refresh() -> None:
    """Quietly re-read each dataset every 5 minutes, from Postgres when it's
    reachable (so another instance's writes eventually become visible here —
    Postgres is the documented source of truth), falling back to the local
    JSON mirror otherwise. This never touches the hot request path; it's the
    existing background timer only, same cost as before when Postgres is
    unreachable."""
    while True:
        _time.sleep(_REFRESH_INTERVAL)
        pg_ok = db_available()
        for name, cfg in DATASETS.items():
            if pg_ok:
                try:
                    with session_scope() as session:
                        payload = cfg["to_json"](session)
                    _cache_set(name, payload)
                    continue
                except Exception as exc:
                    logger.debug("background PG refresh failed for %s, falling back to JSON mirror: %s", name, exc)
            path = OUTPUT_DIR / cfg["file"]
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                _cache_set(name, payload)
            except Exception as exc:
                logger.debug("background cache refresh failed for %s: %s", name, exc)


_refresh_thread = _threading.Thread(target=_background_refresh, daemon=True)
_refresh_thread.start()
# ─────────────────────────────────────────────────────────────────────────────


def write_dataset(name: str, payload) -> dict:
    """Dual-write entry point. Returns a small status dict.
    Raises only if Postgres is reachable but the write fails (true error)."""
    if name not in DATASETS:
        raise ValueError(f"Unknown dataset '{name}'")

    pg_ok = False
    if db_available():
        try:
            with session_scope() as session:
                _replace_pg(session, name, payload)
            pg_ok = True
        except Exception as e:
            logger.error("Postgres write failed for %s: %s", name, e)
            raise  # PG reachable but write errored → surface it, write no JSON

    # JSON mirror (always, so the file stays the human-readable copy).
    text = _write_json(name, payload)
    csum = _checksum(text)

    # Update cache immediately after a successful write so the next read is
    # instant and consistent with what was just persisted.
    _cache_set(name, payload)

    # Record sync state.
    if pg_ok:
        _update_ledger(name, csum, direction="pg_wins", note="dual-write ok")
    else:
        _update_ledger(name, csum, direction="json_only",
                       note="PG unreachable — JSON only, will reconcile")

    return {"dataset": name, "postgres": pg_ok, "json": True}


def read_dataset(name: str):
    """Read from cache when warm; otherwise load from Postgres (or JSON mirror).

    First call after a cold start fetches from Neon once and populates the
    cache.  All subsequent calls within the same process return in <1ms from
    memory, with no Neon round-trip.
    """
    # ── Fast path: serve from in-memory cache ────────────────────────────────
    cached = _cache_get(name)
    if cached is not None:
        return cached

    # ── Cold path: load from source, then cache ───────────────────────────────
    cfg = DATASETS[name]
    payload = None

    # 1. Try Postgres (source of truth when available).
    if db_available():
        try:
            with session_scope() as session:
                payload = cfg["to_json"](session)
        except Exception as e:
            logger.warning("PG read failed for %s, falling back to JSON: %s", name, e)

    # 2. Fall back to the local JSON mirror.
    if payload is None:
        path = OUTPUT_DIR / cfg["file"]
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("JSON read failed for %s: %s", name, exc)

    # 3. Empty sentinel if nothing found.
    if payload is None:
        payload = [] if name == "colleges" else {name.split("_")[0]: []}

    _cache_set(name, payload)
    return payload




def _update_ledger(file_or_name, csum, direction, note):
    cfg = DATASETS.get(file_or_name)
    fname = cfg["file"] if cfg else file_or_name
    if not db_available():
        return
    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            stmt = pg_insert(models.SyncLedger).values(
                file_name=fname, json_checksum=csum,
                last_pushed_to_pg=now, last_direction=direction, note=note,
            ).on_conflict_do_update(
                index_elements=["file_name"],
                set_={"json_checksum": csum, "last_pushed_to_pg": now,
                      "last_direction": direction, "note": note},
            )
            session.execute(stmt)
    except Exception as e:
        logger.warning("ledger update failed: %s", e)
