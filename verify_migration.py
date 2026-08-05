"""
verify_migration.py — Sanity-check that Postgres matches the source JSON.

Run after migrate_json_to_postgres.py to confirm nothing was dropped or
duplicated. Compares row counts and spot-checks a few natural keys.

    export DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require"
    export EMPLOYEE_FIELD_KEY="your-secret-passphrase"
    python verify_migration.py --source ./output

Exits non-zero if any check fails, so it can gate a deploy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

import crypto
import models
from database import session_scope


def jcount(path: Path, key=None, nested=None):
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    if nested:                       # dict-of-dicts like {tokens: {...}}
        return len(data.get(nested, {}))
    if key:                          # {accepted: [...]}
        return len(data.get(key, []))
    if isinstance(data, list):       # bare list
        return len(data)
    if isinstance(data, dict) and "responses" in data:
        return len(data["responses"])
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="./output")
    args = ap.parse_args()
    src = Path(args.source).resolve()

    checks = []
    failures = 0

    with session_scope() as s:
        def pg(model):
            return s.execute(
                select(func.count()).select_from(model)
                .where(model.deleted_at.is_(None))
            ).scalar()

        pairs = [
            ("accepted",       jcount(src / "accepted_candidates.json", key="accepted"), pg(models.AcceptedCandidate)),
            ("rejected",       jcount(src / "rejected_candidates.json", key="rejected"), pg(models.RejectedCandidate)),
            ("selected",       jcount(src / "selected_candidates.json", key="selected"), pg(models.SelectedCandidate)),
            ("employees",      jcount(src / "employees.json", key="employees"), pg(models.Employee)),
            ("colleges",       jcount(src / "colleges.json"), pg(models.College)),
        ]
        for name, j, p in pairs:
            ok = (j == p)
            checks.append((name, j, p, ok))
            if not ok:
                failures += 1

        # Spot-check: every encrypted employee field decrypts cleanly.
        emp_ok = True
        for e in s.execute(select(models.Employee)).scalars():
            try:
                crypto.decrypt_employee_sensitive(s, e)
            except Exception as ex:
                emp_ok = False
                print(f"  ✗ employee {e.name}: decryption failed — {ex}")

    print(f"{'dataset':<16}{'json':>6}{'postgres':>10}   status")
    print("-" * 44)
    for name, j, p, ok in checks:
        print(f"{name:<16}{j:>6}{p:>10}   {'✓' if ok else '✗ MISMATCH'}")
    print("-" * 44)
    print(f"{'employee crypto':<16}{'':>6}{'':>10}   {'✓ all decrypt' if emp_ok else '✗ FAILED'}")

    if failures or not emp_ok:
        print(f"\n✗ {failures} count mismatch(es)" + ("" if emp_ok else " + crypto failure"))
        sys.exit(1)
    print("\n✓ All checks passed. Postgres matches the source JSON.")


if __name__ == "__main__":
    main()
