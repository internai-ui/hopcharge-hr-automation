"""
employee_db.py — Employee master database ("master sheet") for current employees.

• Local JSON store is the source of truth (output/employees.json) — durable on disk.
• Mirrored to a Google Sheet on every change (write-through) so data is never lost
  and HR has an off-box backup. Reuses the same service account as Forms/Drive.
• Import employees from a Google Form (auto-maps questions → fields), or add/edit
  manually. Two views in the UI: candidate tiles and a standard sheet table.

Mount in app.py:
    from employee_db import router as employee_router
    app.include_router(employee_router)

No new dependencies — google-api-python-client / google-auth (already present).
Share the backup Google Sheet with the service-account email (Editor), and the
source Google Form with it (Viewer). Sensitive fields are masked in the UI.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import urllib.request
import urllib.error
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import OUTPUT_DIR
from drive_sync import _find_service_account, _service_account_email

logger = logging.getLogger("volt_cv.employee_db")

EMPLOYEES_FILE = OUTPUT_DIR / "employees.json"
SHEET_SETTINGS_FILE = OUTPUT_DIR / "employee_sheet_settings.json"

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
FORMS_SCOPES = ["https://www.googleapis.com/auth/forms.responses.readonly",
                "https://www.googleapis.com/auth/forms.body.readonly"]

# ──────────────────────────────────────────────
# Field schema — single source of truth (order = sheet column order)
#   key, label, derived?, sensitive?, aliases (for Google-Form auto-mapping)
# ──────────────────────────────────────────────
FIELDS = [
    ("employee_status", "Employee Status", False, False, ["status", "employee status", "active"]),
    ("employee_id", "Our Employee ID", False, False, ["our employee id", "employee id", "emp id", "hopcharge id"]),
    ("division", "Division", False, False, ["division"]),
    ("department", "Department", False, False, ["department", "dept"]),
    ("name", "Name", False, False, ["full name", "employee name", "name"]),
    ("designation", "Designation", False, False, ["designation", "title", "role", "position"]),
    ("dob", "D.O.B (As per Aadhar)", False, False, ["date of birth", "dob", "d.o.b", "birth"]),
    ("age", "Employee Age", True, False, ["age"]),
    ("old_joining_date_tpa", "OLD Joining Date (TPA)", False, False, ["old joining", "tpa joining", "old joining date"]),
    ("tpa_employee_code", "TPA Employee Code", False, False, ["tpa employee code", "tpa code"]),
    ("doj", "DOJ", False, False, ["date of joining", "doj", "joining date"]),
    ("service_duration", "Service Duration", True, False, ["service duration", "tenure"]),
    ("years_of_service_achievement", "Years of Service Achievement", True, False, ["years of service", "service achievement"]),
    ("last_date", "Last Date", False, False, ["last date", "last working", "date of leaving", "exit date"]),
    ("gender", "Gender", False, False, ["gender", "sex"]),
    ("fathers_name", "Father's Name", False, False, ["father", "father's name", "fathers name"]),
    ("mothers_name", "Mother's Name", False, False, ["mother", "mother's name", "mothers name"]),
    ("reporting_manager", "Reporting Manager Name", False, False, ["reporting manager", "manager", "reports to"]),
    ("source_of_employment", "Source of Employment / Referred By", False, False, ["source of employment", "referred by", "referral", "source"]),
    ("email", "Email Address", False, False, ["personal email", "email address", "email"]),
    ("email_official", "Email Address (Official)", False, False, ["official email", "company email", "work email"]),
    ("permanent_address", "Permanent Address", False, False, ["permanent address"]),
    ("correspondence_address", "Correspondence Address", False, False, ["correspondence address", "current address", "communication address"]),
    ("contact_no", "Contact No", False, False, ["contact no", "contact number", "mobile", "phone"]),
    ("emergency_contact", "Emergency Contact Detail", False, False, ["emergency contact", "emergency number"]),
    ("emergency_relation", "Relation With Emergency Contact", False, False, ["relation with emergency", "emergency relation", "relationship"]),
    ("pan", "PAN Number", False, True, ["pan number", "pan"]),
    ("aadhaar", "Aadhaar Number", False, True, ["aadhaar number", "aadhar number", "aadhaar", "aadhar"]),
    ("uan", "UAN No", False, True, ["uan no", "uan"]),
    ("esi", "ESI No", False, False, ["esi no", "esi"]),
    ("id_card_given", "ID Card Given", False, False, ["id card given", "id card"]),
    ("bank_name", "Bank Name", False, False, ["bank name", "bank"]),
    ("account_number", "Account Number", False, True, ["account number", "account no", "bank account"]),
    ("ifsc", "IFSC Code", False, False, ["ifsc code", "ifsc"]),
    ("salary_ctc", "Monthly Salary (CTC)", False, True, ["monthly salary", "ctc", "salary"]),
]

FIELD_KEYS = [f[0] for f in FIELDS]
LABELS = {f[0]: f[1] for f in FIELDS}
DERIVED = {f[0] for f in FIELDS if f[2]}
SENSITIVE = {f[0] for f in FIELDS if f[3]}


def field_schema() -> list:
    return [{"key": k, "label": lbl, "derived": d, "sensitive": s} for (k, lbl, d, s, _a) in FIELDS]


# ──────────────────────────────────────────────
# Store
# ──────────────────────────────────────────────
def _read(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    # Reads through the dual-write layer: Postgres when reachable, JSON mirror
    # otherwise. Falls back to the local file if the layer is unavailable.
    try:
        import dual_writer
        d = dual_writer.read_dataset("employees")
    except Exception:
        d = _read(EMPLOYEES_FILE, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("employees", [])
    return d


def _save(store: dict) -> None:
    # Dual-write: Postgres (encrypting sensitive fields) + the JSON mirror.
    # If Postgres is unreachable the layer degrades to JSON-only and flags
    # drift for the next sync. If the layer itself can't load, write the file.
    try:
        import dual_writer
        dual_writer.write_dataset("employees", store)
    except Exception:
        _write(EMPLOYEES_FILE, store)


# ──────────────────────────────────────────────
# Derived fields (age, service duration, milestone)
# ──────────────────────────────────────────────
def _parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.search(r"(\d{4})", s)               # last resort: a bare year
    return date(int(m.group(1)), 1, 1) if m else None


def _years_between(start: date, end: date) -> float:
    return max(0.0, (end - start).days / 365.25)


def _compute_derived(emp: dict) -> dict:
    today = date.today()
    dob = _parse_date(emp.get("dob"))
    emp["age"] = str(int(_years_between(dob, today))) if dob else ""

    doj = _parse_date(emp.get("doj")) or _parse_date(emp.get("old_joining_date_tpa"))
    end = _parse_date(emp.get("last_date")) or today
    if doj:
        yrs = _years_between(doj, end)
        whole = int(yrs)
        months = int(round((yrs - whole) * 12))
        if months == 12:
            whole, months = whole + 1, 0
        emp["service_duration"] = f"{whole}y {months}m"
        emp["years_of_service_achievement"] = f"{whole} year{'s' if whole != 1 else ''}" if whole >= 1 else "—"
    else:
        emp["service_duration"] = ""
        emp["years_of_service_achievement"] = ""
    return emp


def _normalize(emp: dict) -> dict:
    clean = {k: ("" if emp.get(k) is None else str(emp.get(k)).strip()) for k in FIELD_KEYS}
    return _compute_derived(clean)


# ──────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────
def list_employees() -> list:
    return _load()["employees"]


def _dedupe_key(emp: dict) -> str:
    for k in ("employee_id", "email_official", "email", "aadhaar", "pan"):
        v = (emp.get(k) or "").strip().lower()
        if v:
            return f"{k}:{v}"
    return f"name:{(emp.get('name') or '').strip().lower()}"


def create_employee(data: dict) -> dict:
    store = _load()
    emp = _normalize(data)
    emp["_id"] = uuid.uuid4().hex
    emp["created_at"] = emp["updated_at"] = _now()
    store["employees"].append(emp)
    _save(store)
    _sheet_sync_safe(store["employees"])
    return emp


def update_employee(emp_id: str, data: dict) -> dict:
    store = _load()
    for i, e in enumerate(store["employees"]):
        if e.get("_id") == emp_id:
            merged = {**e, **_normalize({**e, **data})}
            merged["_id"] = emp_id
            merged["created_at"] = e.get("created_at", _now())
            merged["updated_at"] = _now()
            store["employees"][i] = merged
            _save(store)
            _sheet_sync_safe(store["employees"])
            return merged
    raise KeyError(emp_id)


def delete_employee(emp_id: str) -> bool:
    store = _load()
    before = len(store["employees"])
    store["employees"] = [e for e in store["employees"] if e.get("_id") != emp_id]
    if len(store["employees"]) == before:
        return False
    _save(store)
    _sheet_sync_safe(store["employees"])
    return True


# ──────────────────────────────────────────────
# Google Sheet mirror (write-through backup)
# ──────────────────────────────────────────────
def _load_sheet_settings() -> dict:
    d = _read(SHEET_SETTINGS_FILE, {})
    return {"spreadsheet_id": d.get("spreadsheet_id", ""),
            "worksheet": d.get("worksheet", "Master"),
            "credentials_path": d.get("credentials_path", ""),
            "last_sync": d.get("last_sync", ""),
            "last_error": d.get("last_error", "")}


def _save_sheet_settings(patch: dict) -> dict:
    cur = _load_sheet_settings()
    cur.update({k: v for k, v in (patch or {}).items() if v is not None})
    _write(SHEET_SETTINGS_FILE, cur)
    return cur


def _extract_sheet_id(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_\-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_\-]{20,}", s):
        return s
    raise ValueError("Could not read a spreadsheet ID. Paste the Google Sheet link or its ID.")


def _build_sheets(creds_path: Path):
    creds = service_account.Credentials.from_service_account_file(str(creds_path), scopes=SHEETS_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _rows_payload(employees: list) -> list:
    header = [LABELS[k] for k in FIELD_KEYS]
    rows = [header]
    for e in employees:
        rows.append([e.get(k, "") for k in FIELD_KEYS])
    return rows


def sheet_sync(employees: Optional[list] = None) -> dict:
    """Overwrite the backup worksheet with the full current master sheet."""
    s = _load_sheet_settings()
    if not s["spreadsheet_id"]:
        return {"ok": False, "error": "No backup Google Sheet configured."}
    sa = _find_service_account(s.get("credentials_path", ""))
    svc = _build_sheets(sa)
    employees = employees if employees is not None else list_employees()
    body_rows = _rows_payload(employees)
    ws = s["worksheet"] or "Master"
    try:
        svc.spreadsheets().values().clear(
            spreadsheetId=s["spreadsheet_id"], range=f"{ws}").execute()
    except HttpError:
        # worksheet may not exist yet — create it, then continue
        try:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=s["spreadsheet_id"],
                body={"requests": [{"addSheet": {"properties": {"title": ws}}}]}).execute()
        except HttpError:
            pass
    svc.spreadsheets().values().update(
        spreadsheetId=s["spreadsheet_id"], range=f"{ws}!A1",
        valueInputOption="RAW", body={"values": body_rows}).execute()
    _save_sheet_settings({"last_sync": _now(), "last_error": ""})
    return {"ok": True, "rows": len(employees), "worksheet": ws}


def _sheet_sync_safe(employees: list) -> None:
    """Best-effort write-through: never blocks a local save if Sheets is down."""
    s = _load_sheet_settings()
    if not s["spreadsheet_id"]:
        return
    try:
        sheet_sync(employees)
    except Exception as exc:                       # noqa: BLE001 — record, don't crash
        _save_sheet_settings({"last_error": str(exc)})
        logger.warning("Employee sheet mirror failed: %s", exc)


def sheet_test() -> dict:
    s = _load_sheet_settings()
    if not s["spreadsheet_id"]:
        raise ValueError("Paste the backup Google Sheet link or ID first.")
    sa = _find_service_account(s.get("credentials_path", ""))
    svc = _build_sheets(sa)
    meta = svc.spreadsheets().get(spreadsheetId=s["spreadsheet_id"],
                                  fields="properties.title,sheets.properties.title").execute()
    tabs = [sh["properties"]["title"] for sh in meta.get("sheets", [])]
    return {"ok": True, "title": meta.get("properties", {}).get("title", ""),
            "worksheets": tabs, "service_account_email": _service_account_email(sa)}


# ──────────────────────────────────────────────
# Google Form import (auto-map questions → fields)
# ──────────────────────────────────────────────
def _build_forms(creds_path: Path):
    creds = service_account.Credentials.from_service_account_file(str(creds_path), scopes=FORMS_SCOPES)
    return build("forms", "v1", credentials=creds, cache_discovery=False)


def _norm_label(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Pre-compute alias → field-key lookup for fast matching
_ALIAS_MAP = {}
for _k, _lbl, _d, _s, _aliases in FIELDS:
    for _al in [_lbl] + list(_aliases):
        _ALIAS_MAP[_norm_label(_al)] = _k


def _match_field(question_title: str) -> Optional[str]:
    q = _norm_label(question_title)
    if not q:
        return None
    if q in _ALIAS_MAP:
        return _ALIAS_MAP[q]
    # substring match (question contains an alias, or alias contains the question)
    for alias_norm, key in _ALIAS_MAP.items():
        if len(alias_norm) >= 4 and (alias_norm in q or q in alias_norm):
            return key
    return None


def _form_question_map(svc, form_id: str) -> tuple:
    form = svc.forms().get(formId=form_id).execute()
    qid_to_field, unmapped = {}, []
    for item in form.get("items", []):
        q = item.get("questionItem", {}).get("question", {})
        qid = q.get("questionId")
        title = item.get("title", "")
        if not qid:
            continue
        field = _match_field(title)
        if field:
            qid_to_field[qid] = field
        else:
            unmapped.append(title)
    return qid_to_field, unmapped, form.get("info", {}).get("title", "")


def _responses_to_employees(svc, form_id: str, qid_to_field: dict) -> list:
    out, token = [], None
    while True:
        resp = svc.forms().responses().list(formId=form_id, pageToken=token).execute()
        for r in resp.get("responses", []):
            emp = {}
            for qid, ans in (r.get("answers") or {}).items():
                field = qid_to_field.get(qid)
                if not field:
                    continue
                vals = [a.get("value", "") for a in ans.get("textAnswers", {}).get("answers", [])]
                if vals:
                    emp[field] = ", ".join(v for v in vals if v)
            if any(emp.get(k) for k in FIELD_KEYS):
                out.append(emp)
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def form_preview(form_id: str) -> dict:
    sa = _find_service_account(_load_sheet_settings().get("credentials_path", ""))
    svc = _build_forms(sa)
    qmap, unmapped, title = _form_question_map(svc, form_id)
    emps = _responses_to_employees(svc, form_id, qmap)
    mapped_labels = sorted({LABELS[k] for k in qmap.values()})
    return {"form_title": title, "mapped_fields": mapped_labels, "unmapped_questions": unmapped,
            "response_count": len(emps),
            "sample": [{k: e.get(k, "") for k in ("name", "employee_id", "email", "designation")} for e in emps[:5]]}


# ──────────────────────────────────────────────
# Import from a PUBLIC Google Sheet (no auth) via its CSV export
# ──────────────────────────────────────────────
# Build the alias → field-key lookup once. Each FIELDS entry's last element is a
# list of header aliases; we also accept the canonical label and key themselves.
def _build_alias_index() -> dict:
    idx = {}
    for key, label, _derived, _sensitive, aliases in FIELDS:
        for name in [key, label, *aliases]:
            idx[_norm_header(name)] = key
    return idx


def _norm_header(s: str) -> str:
    """Normalise a header for fuzzy matching: lowercase, strip, collapse spaces,
    drop punctuation so 'D.O.B (As per Aadhar)' ~ 'dob'."""
    s = (s or "").strip().lower()
    s = re.sub(r"[\(\)\[\]\.\,\:\/\\\-_]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_ALIAS_INDEX = None


def _resolve_headers(headers: list) -> tuple[dict, list]:
    """Map each CSV column header to a field key. Returns (col_index→key, unmapped_headers)."""
    global _ALIAS_INDEX
    if _ALIAS_INDEX is None:
        _ALIAS_INDEX = _build_alias_index()
    col_map, unmapped = {}, []
    for i, h in enumerate(headers):
        key = _ALIAS_INDEX.get(_norm_header(h))
        if key and key not in DERIVED:      # never import derived fields (age, tenure…)
            col_map[i] = key
        else:
            if (h or "").strip():
                unmapped.append(h.strip())
    return col_map, unmapped


def _csv_export_url(raw: str) -> str:
    """Turn a Google Sheet share URL (or ID) into a CSV export URL. Honours a
    specific tab via #gid=… if present."""
    sheet_id = _extract_sheet_id(raw)
    gid = None
    m = re.search(r"[#&]gid=(\d+)", raw or "")
    if m:
        gid = m.group(1)
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        url += f"&gid={gid}"
    return url


def _fetch_public_csv(raw_url: str) -> list:
    """Download a public sheet as CSV and return a list of row dicts keyed by
    field. Raises ValueError with a friendly message on common failures."""
    url = _csv_export_url(raw_url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (HopchargeHR)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            final_url = resp.geturl()
            # Google redirects un-shared sheets to a login/accounts page; detect that.
            if "accounts.google.com" in final_url or "ServiceLogin" in final_url:
                raise ValueError(
                    "That sheet isn't public. Open it → Share → General access → "
                    "'Anyone with the link' (Viewer), then try again."
                )
            data = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError(
                "Access denied — the sheet isn't shared publicly. Set General "
                "access to 'Anyone with the link' (Viewer)."
            )
        if exc.code == 404:
            raise ValueError("Sheet not found. Check the link is correct.")
        raise ValueError(f"Could not fetch the sheet (HTTP {exc.code}).")
    except urllib.error.URLError as exc:
        raise ValueError(f"Network error fetching the sheet: {exc.reason}")

    reader = csv.reader(io.StringIO(data))
    rows = list(reader)
    if not rows:
        raise ValueError("The sheet appears to be empty.")
    headers = rows[0]
    col_map, unmapped = _resolve_headers(headers)
    if not col_map:
        raise ValueError(
            "None of the sheet's column headers matched employee fields. "
            "Make sure the first row has headers like Name, Email, Designation, etc."
        )
    employees = []
    for r in rows[1:]:
        if not any((c or "").strip() for c in r):
            continue  # skip blank lines
        emp = {}
        for i, key in col_map.items():
            if i < len(r):
                emp[key] = (r[i] or "").strip()
        # Only keep rows that have at least a name or some identifier.
        if any(emp.get(k) for k in ("name", "employee_id", "email", "email_official")):
            employees.append(emp)
    return {"employees": employees, "mapped": sorted({LABELS[k] for k in col_map.values()}),
            "unmapped": unmapped, "header_count": len(headers)}


def sheet_csv_preview(raw_url: str) -> dict:
    """Dry run: show what WOULD be imported from a public sheet, without saving."""
    parsed = _fetch_public_csv(raw_url)
    incoming = parsed["employees"]
    store = _load()
    existing_keys = {_dedupe_key(e) for e in store["employees"]}
    new_count = dup_count = 0
    for raw in incoming:
        if _dedupe_key(_normalize(raw)) in existing_keys:
            dup_count += 1
        else:
            new_count += 1
    return {
        "ok": True,
        "total_rows": len(incoming),
        "new": new_count,
        "duplicates": dup_count,
        "mapped_fields": parsed["mapped"],
        "unmapped_columns": parsed["unmapped"],
        "sample": [{k: e.get(k, "") for k in ("name", "employee_id", "email", "designation")}
                   for e in incoming[:5]],
    }


def sheet_csv_import(raw_url: str) -> dict:
    """Import employees from a public sheet. Mirrors form_import: normalize,
    dedupe, encrypt-on-save (via _normalize + dual-write _save), sync to backup."""
    parsed = _fetch_public_csv(raw_url)
    incoming = parsed["employees"]

    store = _load()
    existing_keys = {_dedupe_key(e) for e in store["employees"]}
    added = skipped = 0
    for raw in incoming:
        emp = _normalize(raw)
        key = _dedupe_key(emp)
        if key in existing_keys:
            skipped += 1
            continue
        emp["_id"] = uuid.uuid4().hex
        emp["created_at"] = emp["updated_at"] = _now()
        if not emp.get("employee_status"):
            emp["employee_status"] = "Active"
        store["employees"].append(emp)
        existing_keys.add(key)
        added += 1
    _save(store)
    _sheet_sync_safe(store["employees"])
    logger.info("Sheet-CSV import: added %d, skipped %d duplicates", added, skipped)
    return {"ok": True, "imported": added, "skipped_duplicate": skipped,
            "total": len(store["employees"]),
            "mapped_fields": parsed["mapped"], "unmapped_columns": parsed["unmapped"]}


def form_import(form_id: str) -> dict:
    sa = _find_service_account(_load_sheet_settings().get("credentials_path", ""))
    svc = _build_forms(sa)
    qmap, _unmapped, _title = _form_question_map(svc, form_id)
    incoming = _responses_to_employees(svc, form_id, qmap)

    store = _load()
    existing_keys = {_dedupe_key(e) for e in store["employees"]}
    added = skipped = 0
    for raw in incoming:
        emp = _normalize(raw)
        key = _dedupe_key(emp)
        if key in existing_keys:
            skipped += 1
            continue
        emp["_id"] = uuid.uuid4().hex
        emp["created_at"] = emp["updated_at"] = _now()
        if not emp.get("employee_status"):
            emp["employee_status"] = "Active"
        store["employees"].append(emp)
        existing_keys.add(key)
        added += 1
    _save(store)
    _sheet_sync_safe(store["employees"])
    return {"ok": True, "imported": added, "skipped_duplicate": skipped, "total": len(store["employees"])}


# ──────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────
router = APIRouter(prefix="/api/employees", tags=["employees"])


class EmployeeBody(BaseModel):
    data: dict


class SheetSettingsBody(BaseModel):
    spreadsheet: Optional[str] = None
    worksheet: Optional[str] = None
    credentials_path: Optional[str] = None


class FormBody(BaseModel):
    form_id: str


class SheetCsvBody(BaseModel):
    sheet_url: str


def _form_id(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"/forms/d/(?:e/)?([a-zA-Z0-9_\-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_\-]{20,}", s):
        return s
    raise ValueError("Paste the Google Form edit link or its ID.")


def _http(exc: Exception):
    if isinstance(exc, HttpError):
        st = getattr(exc.resp, "status", None)
        sa = ""
        try:
            sa = _service_account_email(_find_service_account(_load_sheet_settings().get("credentials_path", "")))
        except Exception:
            pass
        if st == 403:
            return HTTPException(403, f"Access denied. Share the Sheet/Form with the service account ({sa}) "
                                      "as Editor/Viewer, and enable the Sheets & Forms APIs.")
        if st == 404:
            return HTTPException(404, "Not found — check the link/ID and that it's shared with the service account.")
        return HTTPException(502, f"Google API error ({st}).")
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return HTTPException(400, str(exc))
    return HTTPException(500, f"Employee DB error: {exc}")


@router.get("")
async def api_list():
    return {"fields": field_schema(), "employees": list_employees()}


@router.get("/schema")
async def api_schema():
    return {"fields": field_schema()}


@router.post("")
async def api_create(body: EmployeeBody):
    return create_employee(body.data)


@router.put("/{emp_id}")
async def api_update(emp_id: str, body: EmployeeBody):
    try:
        return update_employee(emp_id, body.data)
    except KeyError:
        raise HTTPException(404, "Employee not found.")


@router.delete("/{emp_id}")
async def api_delete(emp_id: str):
    if not delete_employee(emp_id):
        raise HTTPException(404, "Employee not found.")
    return {"ok": True}


@router.get("/sheet/settings")
async def api_sheet_settings():
    s = _load_sheet_settings()
    sa_email, found = "", False
    try:
        sa_email, found = _service_account_email(_find_service_account(s.get("credentials_path", ""))), True
    except FileNotFoundError:
        pass
    return {**s, "service_account_email": sa_email, "credentials_found": found}


@router.post("/sheet/settings")
async def api_sheet_settings_save(body: SheetSettingsBody):
    patch = {}
    if body.spreadsheet is not None:
        patch["spreadsheet_id"] = _extract_sheet_id(body.spreadsheet)
    if body.worksheet is not None:
        patch["worksheet"] = body.worksheet.strip() or "Master"
    if body.credentials_path is not None:
        patch["credentials_path"] = body.credentials_path.strip()
    _save_sheet_settings(patch)
    return await api_sheet_settings()


@router.post("/sheet/test")
async def api_sheet_test():
    try:
        return sheet_test()
    except Exception as exc:
        raise _http(exc)


@router.post("/sheet/sync")
async def api_sheet_sync():
    try:
        return sheet_sync()
    except Exception as exc:
        raise _http(exc)


@router.post("/form/preview")
async def api_form_preview(body: FormBody):
    try:
        return form_preview(_form_id(body.form_id))
    except Exception as exc:
        raise _http(exc)


@router.post("/form/import")
async def api_form_import(body: FormBody):
    try:
        return form_import(_form_id(body.form_id))
    except Exception as exc:
        raise _http(exc)


@router.post("/sheet-csv/preview")
async def api_sheet_csv_preview(body: SheetCsvBody):
    """Dry-run import from a PUBLIC Google Sheet (anyone-with-link). Shows how many
    rows would be added vs. skipped as duplicates, and which columns mapped."""
    try:
        return sheet_csv_preview(body.sheet_url)
    except Exception as exc:
        raise _http(exc)


@router.post("/sheet-csv/import")
async def api_sheet_csv_import(body: SheetCsvBody):
    """Import employees from a PUBLIC Google Sheet via its CSV export. No service
    account needed — the sheet must be shared 'Anyone with the link' (Viewer)."""
    try:
        return sheet_csv_import(body.sheet_url)
    except Exception as exc:
        raise _http(exc)
