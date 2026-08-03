"""
drive_sync.py — Fetch & import NEW CVs from a Google Drive folder.

Adds a manual "Sync from Drive" capability alongside the existing drag-and-drop
upload. It lists PDFs in a shared-drive / team folder, imports only the files it
hasn't seen before, runs each through the SAME pipeline as the upload flow
(extractor.extract_text → parser.parse_resume → CandidateRecord), and APPENDS the
results to output/candidates.json — re-exporting candidates.csv / .xlsx so all
three stay in sync.

Mount in app.py next to the other routers:

    from drive_sync import router as drive_router
    app.include_router(drive_router)

Authentication reuses the SAME service-account key already used for Google Forms
(auto-detected in the project folder), so nothing extra to paste. Share the Drive
folder with the service-account email (Viewer) and make sure the Google Drive API
is enabled in the Cloud project.

No new dependencies — google-api-python-client / google-auth (already used by the
Forms integration) and pandas / openpyxl (already used by the exporter).
"""

from __future__ import annotations

import io
import json
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from config import OUTPUT_DIR, BASE_DIR
from extractor import extract_text
from parser import parse_resume
from exporter import export_all
from schemas import CandidateRecord

logger = logging.getLogger("volt_cv.drive_sync")

SETTINGS_FILE = OUTPUT_DIR / "drive_settings.json"
STATE_FILE = OUTPUT_DIR / "drive_sync_state.json"
CANDIDATES_FILE = OUTPUT_DIR / "candidates.json"

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
PDF_MIME = "application/pdf"
FOLDER_MIME = "application/vnd.google-apps.folder"

_SETTINGS_DEFAULTS = {"folder_id": "", "folder_name": "", "credentials_path": ""}


# ──────────────────────────────────────────────
# Settings & state
# ──────────────────────────────────────────────
def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text())
            merged = dict(_SETTINGS_DEFAULTS)
            merged.update({k: v for k, v in data.items() if k in _SETTINGS_DEFAULTS})
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_SETTINGS_DEFAULTS)


def _save_settings(patch: dict) -> dict:
    cur = _load_settings()
    for k, v in (patch or {}).items():
        if k in _SETTINGS_DEFAULTS and v is not None:
            cur[k] = v
    SETTINGS_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False))
    return cur


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            if isinstance(data, dict):
                data.setdefault("imported", {})
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"imported": {}, "last_synced": "", "folder_id": "", "folder_name": ""}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ──────────────────────────────────────────────
# Credentials — reuse the Forms service-account key
# ──────────────────────────────────────────────
def _find_service_account(explicit_path: str = "") -> Path:
    """Locate the service-account JSON. Explicit path wins; otherwise auto-detect
    the first service_account key sitting in the project folder."""
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p
    for cand in sorted(BASE_DIR.glob("*.json")):
        try:
            d = json.loads(cand.read_text())
            if isinstance(d, dict) and d.get("type") == "service_account" and d.get("client_email"):
                return cand
        except (json.JSONDecodeError, OSError):
            continue
    raise FileNotFoundError(
        "No service-account key found. Place the same Google service-account JSON "
        "you use for Forms in the project folder, or set its path in Drive settings."
    )


def _service_account_email(path: Path) -> str:
    try:
        return json.loads(path.read_text()).get("client_email", "")
    except Exception:
        return ""


_DRIVE_CACHE: dict = {}   # keyed by (resolved credentials path, mtime)


def _build_drive(path: Path):
    # Cache the built client so we don't re-read the key file and re-do the
    # token exchange on every sync. Re-builds automatically if the key file
    # changes (path or modified-time differ).
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        key = (str(path), 0)
    cached = _DRIVE_CACHE.get("client")
    if cached and _DRIVE_CACHE.get("key") == key:
        return cached
    creds = service_account.Credentials.from_service_account_file(str(path), scopes=DRIVE_SCOPES)
    client = build("drive", "v3", credentials=creds, cache_discovery=False)
    _DRIVE_CACHE["client"] = client
    _DRIVE_CACHE["key"] = key
    return client


# ──────────────────────────────────────────────
# Drive helpers
# ──────────────────────────────────────────────
def _extract_folder_id(raw: str) -> str:
    """Accept a Drive folder URL or a bare folder ID and return the ID."""
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        return ""
    m = re.search(r"/folders/([a-zA-Z0-9_\-]+)", s)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_\-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_\-]{10,}", s):
        return s
    raise ValueError(
        "Could not read a folder ID from that input. Paste the Drive folder link "
        "(.../drive/folders/<ID>) or the bare folder ID."
    )


def _folder_info(service, folder_id: str) -> dict:
    meta = service.files().get(
        fileId=folder_id, fields="id,name,mimeType,driveId", supportsAllDrives=True
    ).execute()
    if meta.get("mimeType") != FOLDER_MIME:
        raise ValueError("That ID points to a file, not a folder. Use the folder's link/ID.")
    return {"id": meta["id"], "name": meta.get("name", "(folder)")}


def _list_pdfs(service, folder_id: str) -> list[dict]:
    out, token = [], None
    q = f"'{folder_id}' in parents and mimeType='{PDF_MIME}' and trashed=false"
    while True:
        # The CVs folder is a normal My Drive folder shared with the service
        # account, so we query the default 'user' corpus scoped to this folder.
        # (Previously this used corpora='allDrives' + includeItemsFromAllDrives,
        # which forced Google to scan the entire drive corpus on every sync —
        # the main cause of slow syncs even when nothing new had been added.)
        # If you ever move CVs onto a Shared Drive, re-add:
        #     corpora="allDrives", includeItemsFromAllDrives=True,
        #     supportsAllDrives=True
        resp = service.files().list(
            q=q,
            fields="nextPageToken, files(id,name,modifiedTime,size)",
            pageSize=1000,
            spaces="drive",
            pageToken=token,
        ).execute()
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    # Sort locally (cheap) instead of forcing a server-side orderBy.
    out.sort(key=lambda f: f.get("modifiedTime", ""), reverse=True)
    return out


def _download(service, file_id: str) -> bytes:
    buf = io.BytesIO()
    req = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


# ──────────────────────────────────────────────
# Parse one PDF (identical to the upload pipeline)
# ──────────────────────────────────────────────
def _parse_bytes(filename: str, data: bytes) -> CandidateRecord:
    with tempfile.TemporaryDirectory() as tmp:
        safe = re.sub(r"[^\w.\- ]", "_", filename) or "cv.pdf"
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        path = Path(tmp) / safe
        path.write_bytes(data)
        raw_text, method = extract_text(str(path))
        record = parse_resume(raw_text, source_file=filename)
        record.field_confidence["extraction_method"] = method
        record.field_confidence["source"] = "google_drive"
        return record


# ──────────────────────────────────────────────
# Merge with existing candidates & re-export
# ──────────────────────────────────────────────
_FIELDS = set(CandidateRecord().to_dict().keys())


def _record_from_dict(d: dict) -> CandidateRecord:
    """Rebuild a CandidateRecord from a stored dict so the merged set can be
    re-exported through the normal exporter (keeps json/csv/xlsx identical)."""
    return CandidateRecord(**{k: v for k, v in d.items() if k in _FIELDS})


def _load_candidate_dicts() -> list[dict]:
    if CANDIDATES_FILE.exists():
        try:
            data = json.loads(CANDIDATES_FILE.read_text())
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


# ──────────────────────────────────────────────
# Public operations
# ──────────────────────────────────────────────
def get_status() -> dict:
    s = _load_settings()
    st = _load_state()
    sa_email, creds_found = "", False
    try:
        sa = _find_service_account(s.get("credentials_path", ""))
        sa_email, creds_found = _service_account_email(sa), True
    except FileNotFoundError:
        pass
    return {
        "folder_id": s.get("folder_id", ""),
        "folder_name": s.get("folder_name", "") or st.get("folder_name", ""),
        "credentials_found": creds_found,
        "service_account_email": sa_email,
        "last_synced": st.get("last_synced", ""),
        "imported_count": len(st.get("imported", {})),
    }


def test_access(folder_id: Optional[str] = None) -> dict:
    s = _load_settings()
    fid = _extract_folder_id(folder_id or s.get("folder_id", ""))
    if not fid:
        raise ValueError("No Drive folder set. Paste the folder link or ID first.")
    sa = _find_service_account(s.get("credentials_path", ""))
    sa_email = _service_account_email(sa)
    service = _build_drive(sa)
    info = _folder_info(service, fid)
    files = _list_pdfs(service, fid)
    imported = _load_state().get("imported", {})
    new = [f for f in files if imported.get(f["id"], {}).get("status") != "imported"]
    # remember the resolved folder name
    _save_settings({"folder_id": fid, "folder_name": info["name"]})
    return {
        "ok": True,
        "folder_name": info["name"],
        "service_account_email": sa_email,
        "pdf_count": len(files),
        "new_count": len(new),
    }


def preview(folder_id: Optional[str] = None) -> dict:
    s = _load_settings()
    fid = _extract_folder_id(folder_id or s.get("folder_id", ""))
    if not fid:
        raise ValueError("No Drive folder set.")
    sa = _find_service_account(s.get("credentials_path", ""))
    service = _build_drive(sa)
    info = _folder_info(service, fid)
    files = _list_pdfs(service, fid)
    imported = _load_state().get("imported", {})
    new = [f for f in files if imported.get(f["id"], {}).get("status") != "imported"]
    return {
        "folder_name": info["name"],
        "total_pdfs": len(files),
        "already_imported": len(files) - len(new),
        "new": [{"id": f["id"], "name": f["name"], "modifiedTime": f.get("modifiedTime", "")} for f in new],
    }


def sync(folder_id: Optional[str] = None) -> dict:
    s = _load_settings()
    fid = _extract_folder_id(folder_id or s.get("folder_id", ""))
    if not fid:
        return {"success": False, "error": "No Drive folder set. Paste the folder link or ID first."}

    sa = _find_service_account(s.get("credentials_path", ""))
    service = _build_drive(sa)
    info = _folder_info(service, fid)
    files = _list_pdfs(service, fid)

    state = _load_state()
    imported = state.setdefault("imported", {})
    to_do = [f for f in files if imported.get(f["id"], {}).get("status") != "imported"]

    existing = [_record_from_dict(d) for d in _load_candidate_dicts()]
    seen = {(r.email or "").strip().lower() for r in existing if r.email}

    new_records, results = [], []
    added = duplicates = failed = 0

    for f in to_do:
        fid_, name = f["id"], f.get("name", "cv.pdf")
        try:
            data = _download(service, fid_)
            rec = _parse_bytes(name, data)
            email = (rec.email or "").strip().lower()
            if email and email in seen:
                duplicates += 1
                imported[fid_] = {"name": name, "status": "imported",
                                  "candidate_email": rec.email, "note": "duplicate — already in candidates",
                                  "imported_at": _now()}
                results.append({"name": name, "status": "duplicate", "email": rec.email})
                continue
            if email:
                seen.add(email)
            new_records.append(rec)
            added += 1
            imported[fid_] = {"name": name, "status": "imported",
                              "candidate_email": rec.email, "imported_at": _now()}
            results.append({"name": name, "status": "imported", "email": rec.email or ""})
            logger.info("Drive import: %s → %s <%s>", name, rec.full_name or "(none)", rec.email or "(none)")
        except Exception as exc:
            failed += 1
            imported[fid_] = {"name": name, "status": "failed", "error": str(exc), "at": _now()}
            results.append({"name": name, "status": "failed", "error": str(exc)})
            logger.error("Drive import failed for %s: %s", name, exc)

    if new_records:
        export_all(existing + new_records)          # re-export json/csv/xlsx (old + new)
        candidates_total = len(existing) + len(new_records)
    else:
        candidates_total = len(existing)

    state["folder_id"] = fid
    state["folder_name"] = info["name"]
    state["last_synced"] = _now()
    _save_state(state)

    return {
        "success": True,
        "folder_name": info["name"],
        "scanned": len(files),
        "already_imported": len(files) - len(to_do),
        "imported": added,
        "duplicates": duplicates,
        "failed": failed,
        "candidates_total": candidates_total,
        "last_synced": state["last_synced"],
        "results": results,
    }


# ──────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────
router = APIRouter(prefix="/api/drive", tags=["drive"])


class SettingsBody(BaseModel):
    folder: Optional[str] = None
    credentials_path: Optional[str] = None


class FolderBody(BaseModel):
    folder: Optional[str] = None


def _http(exc: Exception):
    """Translate Google/credential errors into clean HTTP errors."""
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None)
        sa = ""
        try:
            sa = _service_account_email(_find_service_account(_load_settings().get("credentials_path", "")))
        except Exception:
            pass
        if status == 404:
            return HTTPException(404, "Folder not found. Check the folder link/ID, and that it's "
                                      f"shared with the service account ({sa}).")
        if status == 403:
            return HTTPException(403, "Access denied. Share the Drive folder with the service-account "
                                      f"email ({sa}) as Viewer, and enable the Google Drive API in your "
                                      "Cloud project.")
        if status == 429:
            return HTTPException(429, "Rate limited by Google. Wait a minute and retry.")
        return HTTPException(502, f"Google Drive API error ({status}).")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(400, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, f"Drive sync error: {exc}")


@router.get("")
async def status():
    return get_status()


@router.get("/settings")
async def get_settings():
    return get_status()


@router.post("/settings")
async def post_settings(body: SettingsBody):
    patch = {}
    if body.folder is not None:
        patch["folder_id"] = _extract_folder_id(body.folder)
    if body.credentials_path is not None:
        patch["credentials_path"] = body.credentials_path.strip()
    _save_settings(patch)
    return get_status()


@router.post("/test")
async def post_test(body: FolderBody):
    try:
        return test_access(body.folder)
    except Exception as exc:
        raise _http(exc)


@router.get("/preview")
async def get_preview(folder: Optional[str] = None):
    try:
        return preview(folder)
    except Exception as exc:
        raise _http(exc)


@router.post("/sync")
async def post_sync(body: FolderBody):
    try:
        return sync(body.folder)
    except Exception as exc:
        raise _http(exc)
