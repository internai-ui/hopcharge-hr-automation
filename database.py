"""
database.py — Neon PostgreSQL connection + session management.

Neon specifics handled here:
  * sslmode=require is forced (Neon rejects non-TLS connections).
  * We use a NullPool-friendly setup with pool_pre_ping so connections that
    Neon's autosuspend has closed are transparently recycled. Neon's free tier
    suspends compute after ~5 min idle; pre_ping avoids "server closed the
    connection unexpectedly" on the first query after a wake.
  * The connection string comes from the DATABASE_URL env var. Get yours from
    the Neon dashboard → Connection Details → "Connection string" (the pooled
    one, ending in ...-pooler.neon.tech, is preferred for a web app).

Set it once, e.g. in a .env file (loaded by your process manager) or shell:
    export DATABASE_URL="postgresql+psycopg://USER:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require"
    export EMPLOYEE_FIELD_KEY="a-long-random-passphrase-keep-this-secret"

The +psycopg suffix selects the psycopg (v3) driver. Install with:
    pip install "psycopg[binary]" sqlalchemy
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


# ── Load neon.env BEFORE reading DATABASE_URL ────────────────────────────────
# The app loads neon.env at startup, but standalone scripts (migrate, verify,
# wipe) import database.py directly and would otherwise see no DATABASE_URL.
# Loading it here means EVERY entry point gets the same environment. Any var
# already set in the real environment wins (so `export DATABASE_URL=...` still
# overrides the file).
def _load_neon_env() -> None:
    try:
        from app_paths import NEON_ENV as env_path
    except Exception:
        env_path = Path(__file__).resolve().parent / "neon.env"
    if not env_path.exists():
        alt = Path(__file__).resolve().parent / "neon.env"
        if not alt.exists():
            return
        env_path = alt
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass  # a malformed file must never stop startup


_load_neon_env()

# ── Connection string ────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    # Normalise to the psycopg v3 driver SQLAlchemy expects.
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Force TLS for Neon if the caller forgot it.
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"


def _make_engine():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy your Neon connection string into the "
            "DATABASE_URL environment variable before starting the app."
        )
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,      # survive Neon autosuspend / dropped sockets
        pool_recycle=300,        # recycle connections older than 5 min
        pool_size=5,
        max_overflow=5,
        future=True,
    )


# Lazily build the engine so importing this module never fails when DATABASE_URL
# is absent (e.g. during the JSON-only fallback path).
_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), class_=Session, expire_on_commit=False, future=True
        )
    return _SessionLocal


@contextmanager
def session_scope():
    """Transactional scope. Commits on success, rolls back on any exception."""
    factory = get_session_factory()
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def db_available() -> bool:
    """Quick health probe — used by the dual-write layer to decide whether the
    Postgres write should be attempted at all. Returns False if DATABASE_URL is
    unset or Neon is unreachable, so the app degrades to JSON-only instead of
    erroring."""
    if not DATABASE_URL:
        return False
    try:
        from sqlalchemy import text
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
