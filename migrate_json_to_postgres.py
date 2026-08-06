"""
migrate_json_to_postgres.py — One-time migration of all output/*.json into Neon.

Run AFTER schema.sql has created the tables:
    export DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require"
    export EMPLOYEE_FIELD_KEY="your-secret-passphrase"
    python migrate_json_to_postgres.py --source ./output

What it does, in one transaction per table:
  * Reads each JSON file from the source directory.
  * Normalises the shape into ORM rows (parsing dates, unnesting wrappers).
  * Upserts on the natural key (response_id / token / employee legacy id /
    college legacy id / config key) so re-running is idempotent — it updates
    existing rows instead of duplicating them.
  * Encrypts the 5 sensitive employee fields via pgcrypto on the way in.
  * Records a checksum in sync_ledger so the dual-write layer knows the JSON
    and PG started in sync.

Flags:
  --source DIR   directory holding the JSON files (default ./output)
  --dry-run      parse + validate everything, print a summary, write nothing
  --only NAME    migrate just one dataset (e.g. --only employees)

Nothing is ever deleted. Safe to run repeatedly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

import crypto
import models
from database import session_scope, db_available


# ── date parsing ──────────────────────────────────────────────────────────────
def parse_dt(value):
    """Best-effort ISO-8601 / Z parsing. Returns aware datetime or None.
    Leaves obviously-garbage values (the employees test data has dates like
    '12.2.1932' and 'fewfewewf') as None rather than raising."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    try:
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  ⚠️  {path.name}: invalid JSON ({e}) — skipped")
        return None


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def record_ledger(session, file_name, csum):
    stmt = pg_insert(models.SyncLedger).values(
        file_name=file_name,
        json_checksum=csum,
        last_pushed_to_pg=datetime.now(timezone.utc),
        last_direction="pg_wins",
        note="initial migration",
    ).on_conflict_do_update(
        index_elements=["file_name"],
        set_={
            "json_checksum": csum,
            "last_pushed_to_pg": datetime.now(timezone.utc),
            "last_direction": "pg_wins",
        },
    )
    session.execute(stmt)


# ── per-dataset upserts ─────────────────────────────────────────────────────
def _upsert(session, model, rows, conflict_col):
    """Generic idempotent upsert on a single unique column."""
    n = 0
    for r in rows:
        stmt = pg_insert(model).values(**r)
        update_cols = {k: stmt.excluded[k] for k in r if k != conflict_col}
        stmt = stmt.on_conflict_do_update(
            index_elements=[conflict_col], set_=update_cols
        )
        session.execute(stmt)
        n += 1
    return n


def migrate_pipeline(session, src, dry, fname, model):
    data = load_json(src / fname)
    if not data:
        return 0
    key = "accepted" if "accepted" in data else "rejected"
    items = data.get(key, [])
    rows = []
    for it in items:
        base = dict(
            response_id=it.get("response_id"), name=it.get("name"),
            email=it.get("email"), phone=it.get("phone"), role=it.get("role"),
            answers=it.get("answers", []),
            recommendation=it.get("recommendation"),
            history=it.get("history", []),
        )
        if key == "accepted":
            base.update(
                stage=it.get("stage", "hr"),
                accepted_note=it.get("accepted_note", ""),
                accepted_at=parse_dt(it.get("accepted_at")),
                stage_changed_at=parse_dt(it.get("stage_changed_at")),
            )
        else:
            base.update(
                rejected_round=it.get("rejected_round"),
                rejected_reason=it.get("rejected_reason", ""),
                rejected_at=parse_dt(it.get("rejected_at")),
            )
        rows.append(base)
    rows = [r for r in rows if r["response_id"]]
    if dry:
        return len(rows)
    return _upsert(session, model, rows, "response_id")


def migrate_selected(session, src, dry):
    data = load_json(src / "selected_candidates.json")
    if not data:
        return 0
    rows = [dict(
        response_id=s.get("response_id"), email=s.get("email"),
        selected_at=parse_dt(s.get("at")),
    ) for s in data.get("selected", []) if s.get("response_id")]
    if dry:
        return len(rows)
    return _upsert(session, models.SelectedCandidate, rows, "response_id")


def migrate_employees(session, src, dry):
    data = load_json(src / "employees.json")
    if not data:
        return 0
    items = data.get("employees", [])
    if dry:
        return len(items)
    n = 0
    for e in items:
        enc = crypto.encrypt_employee_sensitive(session, e)
        row = dict(
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
        )
        # Upsert on legacy_id so re-runs update rather than duplicate.
        legacy = row["legacy_id"]
        existing = None
        if legacy:
            existing = session.execute(
                select(models.Employee).where(models.Employee.legacy_id == legacy)
            ).scalar_one_or_none()
        if existing:
            for k, v in row.items():
                setattr(existing, k, v)
        else:
            session.add(models.Employee(**row))
        n += 1
    return n


def migrate_colleges(session, src, dry):
    data = load_json(src / "colleges.json")
    if not data:
        return 0
    rows = []
    for c in data:
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
            priority_level=c.get("priority_level"),
            priority_tier=c.get("priority_tier"),
            source_type=c.get("source_type"), source_url=c.get("source_url"),
            confidence_score=c.get("confidence_score"),
            last_verified=parse_dt(c.get("last_verified")),
            notes=c.get("notes", ""),
        ))
    rows = [r for r in rows if r["college_name"]]
    if dry:
        return len(rows)
    # Upsert on legacy_id manually (not a DB unique constraint).
    n = 0
    for r in rows:
        legacy = r["legacy_id"]
        existing = None
        if legacy:
            existing = session.execute(
                select(models.College).where(models.College.legacy_id == legacy)
            ).scalar_one_or_none()
        if existing:
            for k, v in r.items():
                setattr(existing, k, v)
        else:
            session.add(models.College(**r))
        n += 1
    return n


# ── orchestration ─────────────────────────────────────────────────────────────
DATASETS = [
    ("accepted",           "accepted_candidates.json",
        lambda s, src, dry: migrate_pipeline(s, src, dry, "accepted_candidates.json", models.AcceptedCandidate)),
    ("rejected",           "rejected_candidates.json",
        lambda s, src, dry: migrate_pipeline(s, src, dry, "rejected_candidates.json", models.RejectedCandidate)),
    ("selected",           "selected_candidates.json", migrate_selected),
    ("employees",          "employees.json",          migrate_employees),
    ("colleges",           "colleges.json",           migrate_colleges),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="./output", help="dir with JSON files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="migrate one dataset only")
    args = ap.parse_args()

    src = Path(args.source).resolve()
    if not src.exists():
        print(f"✗ Source directory not found: {src}")
        sys.exit(1)

    if not args.dry_run and not db_available():
        print("✗ Cannot reach Postgres. Check DATABASE_URL and that schema.sql ran.")
        sys.exit(1)

    print(f"{'DRY RUN — ' if args.dry_run else ''}Migrating from {src}\n")
    summary = {}

    datasets = DATASETS if not args.only else [d for d in DATASETS if d[0] == args.only]
    if args.only and not datasets:
        print(f"✗ Unknown dataset '{args.only}'. "
              f"Choose from: {', '.join(d[0] for d in DATASETS)}")
        sys.exit(1)

    # One transaction for the whole migration: all-or-nothing.
    with session_scope() as session:
        for name, fname, fn in datasets:
            try:
                count = fn(session, src, args.dry_run)
                summary[name] = count
                print(f"  {'would load' if args.dry_run else 'loaded':>10} {count:>4}  {name}")
                # ledger entry per real file
                if not args.dry_run and fname != "(several)":
                    p = src / fname
                    if p.exists():
                        record_ledger(session, fname, checksum(p))
            except Exception as e:
                print(f"  ✗ {name}: {e}")
                raise  # rolls back the entire transaction

        if args.dry_run:
            session.rollback()

    print(f"\n{'Dry run complete — nothing written.' if args.dry_run else '✓ Migration committed.'}")
    print(f"Total rows: {sum(summary.values())}")


if __name__ == "__main__":
    main()
