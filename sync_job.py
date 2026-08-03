"""
sync_job.py — Reconcile offline edits between the JSON mirror and Postgres.

Conflict rule (you chose): Postgres wins.

The job compares, per dataset, the on-disk JSON checksum against the checksum
recorded in sync_ledger the last time dual_writer wrote it:

  * JSON unchanged since last write  → nothing to do.
  * JSON changed AND Postgres also changed since → CONFLICT → Postgres wins:
        overwrite the JSON file from Postgres.
  * JSON changed but Postgres did NOT change → the JSON was hand-edited while
        the app/PG was idle. We treat Postgres as truth regardless (your rule),
        so we also overwrite the JSON from Postgres — BUT we log it loudly and
        keep a .bak of the hand-edited file first, so nothing is silently lost.

In practice "Postgres wins" means the sync job's job is simple: make the JSON
match Postgres whenever they differ, preserving the overwritten JSON as a .bak.

Entry points:
  reconcile_all()         → run once over every dataset, return a report
  start_background(every) → spawn a daemon thread that calls reconcile_all()
                            every `every` seconds (default 300 = 5 min)

Wire into FastAPI:
    from sync_job import reconcile_all, start_background
    @app.on_event("startup")
    def _boot_sync():
        reconcile_all()            # catch edits made while the app was off
        start_background(300)      # then every 5 min
    @app.post("/api/sync/run")     # manual button
    def _manual_sync():
        return reconcile_all()
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

import models
from database import db_available, session_scope
from dual_writer import DATASETS, OUTPUT_DIR

logger = logging.getLogger("volt_cv.sync")


def _checksum_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ledger_checksum(session, fname: str):
    row = session.execute(
        select(models.SyncLedger).where(models.SyncLedger.file_name == fname)
    ).scalar_one_or_none()
    return row.json_checksum if row else None


def _write_ledger(session, fname, csum, direction, note):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    now = datetime.now(timezone.utc)
    stmt = pg_insert(models.SyncLedger).values(
        file_name=fname, json_checksum=csum,
        last_pulled_to_json=now, last_direction=direction, note=note,
    ).on_conflict_do_update(
        index_elements=["file_name"],
        set_={"json_checksum": csum, "last_pulled_to_json": now,
              "last_direction": direction, "note": note},
    )
    session.execute(stmt)


def reconcile_dataset(name: str) -> dict:
    """Reconcile one dataset. Postgres wins: if the JSON on disk differs from
    what Postgres would produce, back up the JSON and overwrite it from PG."""
    cfg = DATASETS[name]
    fname = cfg["file"]
    path = OUTPUT_DIR / fname

    if not db_available():
        return {"dataset": name, "action": "skipped", "reason": "PG unreachable"}

    with session_scope() as session:
        # What Postgres says the file should contain.
        pg_payload = cfg["to_json"](session)
        pg_text = json.dumps(pg_payload, indent=2, ensure_ascii=False)
        pg_csum = _checksum_text(pg_text)

        # What's on disk right now.
        disk_text = path.read_text(encoding="utf-8") if path.exists() else ""
        disk_csum = _checksum_text(disk_text) if disk_text else None

        last_csum = _ledger_checksum(session, fname)

        # Case 1: disk already matches PG → in sync.
        if disk_csum == pg_csum:
            if last_csum != pg_csum:
                _write_ledger(session, fname, pg_csum, "in_sync", "verified match")
            return {"dataset": name, "action": "in_sync"}

        # Case 2: disk differs from PG. Postgres wins.
        # If the disk file was hand-edited (disk_csum != last recorded), keep a
        # backup before overwriting so a mistaken edit is recoverable.
        hand_edited = (disk_csum is not None and disk_csum != last_csum)
        if hand_edited and path.exists():
            bak = path.with_suffix(path.suffix + f".bak-{int(time.time())}")
            shutil.copy2(path, bak)
            logger.warning(
                "Offline edit detected in %s — backed up to %s, "
                "overwriting from Postgres (PG wins).", fname, bak.name)

        path.write_text(pg_text, encoding="utf-8")
        _write_ledger(session, fname, pg_csum, "pg_wins",
                      "reconciled: JSON overwritten from PG"
                      + (" (hand-edit backed up)" if hand_edited else ""))
        return {
            "dataset": name,
            "action": "pg_overwrote_json",
            "hand_edited": hand_edited,
        }


def reconcile_all() -> dict:
    """Reconcile every registered dataset. Returns a report dict."""
    if not db_available():
        logger.info("sync: Postgres unreachable, skipping reconcile")
        return {"ok": False, "reason": "PG unreachable", "datasets": {}}
    report = {}
    for name in DATASETS:
        try:
            report[name] = reconcile_dataset(name)
        except Exception as e:
            logger.error("sync failed for %s: %s", name, e)
            report[name] = {"dataset": name, "action": "error", "error": str(e)}
    overwrote = sum(1 for r in report.values() if r.get("action") == "pg_overwrote_json")
    logger.info("sync: reconciled %d datasets, %d JSON files refreshed from PG",
                len(report), overwrote)
    return {"ok": True, "datasets": report, "refreshed": overwrote}


_thread = None


def start_background(every_seconds: int = 300):
    """Spawn a daemon thread that reconciles every `every_seconds`."""
    global _thread
    if _thread and _thread.is_alive():
        return
    def _loop():
        while True:
            time.sleep(every_seconds)
            try:
                reconcile_all()
            except Exception as e:
                logger.error("background sync error: %s", e)
    _thread = threading.Thread(target=_loop, daemon=True, name="sync-job")
    _thread.start()
    logger.info("sync: background reconcile every %ds", every_seconds)
