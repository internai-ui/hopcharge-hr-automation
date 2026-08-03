"""
app_paths.py — one place that decides WHERE the app keeps its files.

Why this exists
---------------
When the app runs as a normal Python script, it can keep its data right next to
the code. But when it is packaged into a one-click bundle (PyInstaller), the code
lives inside a read-only / temporary location, so we must NOT write data there.

This module resolves two kinds of locations:

  • BUNDLE_DIR  — read-only files that ship INSIDE the app (e.g. the web UI,
                  the spaCy model). Resolved differently when frozen.
  • DATA_HOME   — a stable, user-visible folder OUTSIDE the app where the user's
                  own files live and where the app writes its records:
                      ~/HopchargeHR/
                        neon.env                ← user pastes secrets here
                        credentials/             ← user drops service_account.json here
                        output/                  ← app writes candidate/employee data
                        input_resumes/           ← optional CV drop folder

Everything else in the codebase should import paths FROM HERE instead of
computing them from __file__, so the same code works as a script and as a bundle.
"""

import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    """True when running inside a PyInstaller (or similar) one-click bundle."""
    return getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")


def bundle_dir() -> Path:
    """Folder containing read-only files that ship inside the app.

    - Frozen: PyInstaller extracts bundled data to sys._MEIPASS.
    - Script: the folder this file lives in (the project root).
    """
    if _is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def data_home() -> Path:
    """Stable, user-visible folder for the user's config and the app's data.

    Order of preference:
      1. HOPCHARGE_HOME env var, if the user/admin set one (advanced/override).
      2. Frozen build: ~/HopchargeHR  (easy to find in Finder/Explorer).
      3. Script build: the project folder itself (so existing dev setups keep
         working exactly as before — no behaviour change when not bundled).
    """
    override = os.environ.get("HOPCHARGE_HOME")
    if override:
        p = Path(override).expanduser()
    elif _is_frozen():
        p = Path.home() / "HopchargeHR"
    else:
        p = Path(__file__).resolve().parent
    p.mkdir(parents=True, exist_ok=True)
    return p


# Resolved once at import.
BUNDLE_DIR = bundle_dir()
DATA_HOME = data_home()

# Sub-locations (created on demand so first launch "just works").
OUTPUT_DIR = DATA_HOME / "output"
INPUT_DIR = DATA_HOME / "input_resumes"
CREDENTIALS_DIR = DATA_HOME / "credentials"
NEON_ENV = DATA_HOME / "neon.env"

for _d in (OUTPUT_DIR, INPUT_DIR, CREDENTIALS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# The web UI ships inside the bundle (read-only is fine for static files).
STATIC_DIR = BUNDLE_DIR / "static"


def describe() -> str:
    """Human-readable summary, handy for a first-run console message."""
    mode = "bundled app" if _is_frozen() else "python script"
    return (
        f"Running as: {mode}\n"
        f"  Your files & data live in: {DATA_HOME}\n"
        f"    - secrets file:   {NEON_ENV}\n"
        f"    - credentials in: {CREDENTIALS_DIR}\n"
        f"    - data saved in:  {OUTPUT_DIR}\n"
    )
