"""
wipe_all_data.py — Full reset of all HopCharge HR datasets.

WHAT IT DOES (in safe order):
  1. Backs up everything in output/ to a timestamped backup folder.
  2. If Neon/Postgres is reachable, TRUNCATEs the data tables.
  3. Resets the JSON files in output/ to their correct empty shapes.

WHY THIS ORDER: the app's sync job is "Postgres wins" — so if you clear only
the JSON while Postgres still holds data, the next reconcile copies it back.
Clearing Postgres first prevents that.

IMPORTANT — RUN THIS WITH THE APP CLOSED.
  Close the dashboard window first, so no sync runs mid-wipe.

USAGE:
    # preview only, changes nothing:
    python wipe_all_data.py --dry-run

    # actually wipe (asks you to type CONFIRM):
    python wipe_all_data.py

Backups go to:  output/_backup_before_wipe_<timestamp>/
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Resolve output dir the same way the app does.
try:
    from config import OUTPUT_DIR
except Exception:
    OUTPUT_DIR = Path(__file__).resolve().parent / "output"

OUTPUT_DIR = Path(OUTPUT_DIR)

# ── Empty shapes: each file reset to what the code expects when "empty" ──
# Shapes confirmed against the loaders in your modules (rejected_store,
# form_tracking, store.py, etc.).
EMPTY_SHAPES: dict[str, object] = {
    "form_responses.json":     {"responses": []},
    "candidates.json":         [],                       # bare list (exporter)
    "accepted_candidates.json":{"accepted": []},
    "rejected_candidates.json":{"rejected": []},
    "selected_candidates.json":{"selected": []},
    "colleges.json":           [],                       # bare list (store.py)
    "employees.json":          {"employees": []},
    "form_tracking.json":      {"tokens": {}, "by_email": {}},
    "status_tokens.json":      {"tokens": {}, "by_email": {}},
    "rubrics.json":            None,                      # delete, app re-seeds
    "ai_config.json":          None,                      # delete, keeps no keys
    "calendly_invite_log.json":[],
    "drive_sync_state.json":   None,                      # delete, re-created
}

# Log/ledger style files to just delete (they re-create themselves).
DELETE_FILES = [
    "ai_audit.log",
    "drive_seen.json",        # dedupe ledger, if present
]

# Postgres tables to truncate (from models.py __tablename__).
PG_TABLES = [
    "form_responses", "candidates", "accepted_candidates", "rejected_candidates",
    "selected_candidates", "employees", "colleges", "form_tracking",
    "status_tokens", "scoring_rubrics", "calendly_invites", "app_config",
    "sync_ledger",
]


def backup() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = OUTPUT_DIR / f"_backup_before_wipe_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in OUTPUT_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
            copied += 1
    print(f"  Backed up {copied} file(s) → {dest}")
    return dest


def wipe_postgres(dry: bool) -> str:
    """Truncate PG tables if reachable. Returns a status string."""
    try:
        from database import db_available, session_scope
    except Exception as e:
        return f"skipped (no database module: {e})"

    try:
        if not db_available():
            return "skipped (Neon not reachable / DATABASE_URL not set)"
    except Exception as e:
        return f"skipped (health check failed: {e})"

    if dry:
        return f"WOULD TRUNCATE {len(PG_TABLES)} tables"

    from sqlalchemy import text
    with session_scope() as s:
        # RESTART IDENTITY + CASCADE to clear cleanly regardless of FKs.
        joined = ", ".join(PG_TABLES)
        s.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    return f"truncated {len(PG_TABLES)} tables"


def wipe_json(dry: bool) -> None:
    for fname, shape in EMPTY_SHAPES.items():
        path = OUTPUT_DIR / fname
        if shape is None:
            if path.exists():
                print(f"  {'WOULD DELETE' if dry else 'deleted'}  {fname}")
                if not dry:
                    path.unlink()
            continue
        print(f"  {'WOULD RESET ' if dry else 'reset      '}  {fname}")
        if not dry:
            path.write_text(json.dumps(shape, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    for fname in DELETE_FILES:
        path = OUTPUT_DIR / fname
        if path.exists():
            print(f"  {'WOULD DELETE' if dry else 'deleted'}  {fname}")
            if not dry:
                path.unlink()


def main() -> int:
    dry = "--dry-run" in sys.argv

    print("=" * 60)
    print("  HopCharge HR — FULL DATA WIPE")
    print("=" * 60)
    print(f"  Output folder: {OUTPUT_DIR}")
    print(f"  Mode: {'DRY RUN (no changes)' if dry else 'LIVE'}")
    print()
    print("  Make sure the dashboard app is CLOSED before continuing.")
    print()

    if not OUTPUT_DIR.exists():
        print("  Output folder not found — nothing to do.")
        return 0

    if not dry:
        ans = input('  Type CONFIRM to wipe everything (or anything else to cancel): ').strip()
        if ans != "CONFIRM":
            print("  Cancelled. Nothing changed.")
            return 1
        print()
        print("  1) Backing up...")
        backup()

    print()
    print("  2) Postgres...")
    print(f"     {wipe_postgres(dry)}")

    print()
    print("  3) JSON files...")
    wipe_json(dry)

    print()
    print("  Done." if not dry else "  Dry run complete — no changes made.")
    if not dry:
        print("  Restart the dashboard. It will come up empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
