"""
app.py — FastAPI backend for the VOLT.CV web interface.

Endpoints:
  GET  /                    — serve the frontend SPA
  POST /api/parse           — upload PDFs, return structured JSON
  GET  /api/download/{fmt}  — download results as JSON / CSV / XLSX
  POST /api/send-emails     — send personalised Gmail campaign to all candidates

Run with:
    python app.py
"""

# ──────────────────────────────────────────────
# Load neon.env FIRST — before any module that reads environment variables
# (database.py reads DATABASE_URL, crypto.py reads EMPLOYEE_FIELD_KEY) is
# imported. This makes the Postgres connection work on every run without
# needing to `export` the variables in each new terminal session.
#
# No external dependency required: this reads a neon.env file in the project
# folder and sets any variable that isn't already set in the environment.
# (Variables already exported in the shell take precedence, so you can still
# override.)
# ──────────────────────────────────────────────
import os as _os
from pathlib import Path as _Path


def _load_dotenv():
    # Read secrets from the stable data folder (works as script OR bundle),
    # falling back to a neon.env next to the code for older dev setups.
    try:
        from app_paths import NEON_ENV as env_path
    except Exception:
        env_path = _Path(__file__).resolve().parent / "neon.env"
    if not env_path.exists():
        alt = _Path(__file__).resolve().parent / "neon.env"
        if alt.exists():
            env_path = alt
        else:
            return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")  # tolerate quoted values
            if key and key not in _os.environ:
                _os.environ[key] = val
    except Exception:
        pass  # a malformed .env must never stop the app from starting


_load_dotenv()

import sys
import logging
import tempfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from extractor import extract_text
from parser import parse_resume
from exporter import export_all
from config import OUTPUT_DIR
import form_tracking

from rejected_store import router as rejected_router
from accepted_store import router as accepted_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("volt_cv")

# ──────────────────────────────────────────────
# App setup
# ──────────────────────────────────────────────
app = FastAPI(
    title="VOLT.CV — Resume Intelligence API",
    version="1.0.0",
    docs_url="/api/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from app_paths import STATIC_DIR   # bundle-aware location of the web UI
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# College Outreach & Internship Partnership module (colleges/ sub-package)
from colleges import router as colleges_router
app.include_router(colleges_router)

# AI Provider Configuration & Candidate Scoring Engine (scoring/ sub-package)
from scoring import router as scoring_router
app.include_router(scoring_router)
app.include_router(rejected_router)
app.include_router(accepted_router)

from calendly_invite import router as calendly_invite_router
app.include_router(calendly_invite_router)

# Notes (per-candidate remarks timeline, keyed by response_id)
from notes_store import router as notes_router
app.include_router(notes_router)

# Admin settings (editable email text + score thresholds)
from admin_settings import router as admin_router
app.include_router(admin_router)

from drive_sync import router as drive_router
app.include_router(drive_router)

from status_portal import router as status_router
app.include_router(status_router)

from employee_db import router as employee_router
app.include_router(employee_router)

# Gmail OAuth "Connect Google Account" — required for every candidate-facing
# send (recruitment, onboarding, rejection, Round 1 Calendly) and for Forms
# sync's OAuth path (Service Account JSON remains a Forms-only fallback).
from gmail_oauth import router as gmail_oauth_router
app.include_router(gmail_oauth_router)

from email_replies import router as email_replies_router
app.include_router(email_replies_router)

# Sign-in protection (email + password, cookie sessions). OFF by default so
# local/desktop use is unchanged; set DASHBOARD_AUTH=on in neon.env to require
# a login when the dashboard is hosted on the web. See auth.py for details.
from auth import router as auth_router, auth_middleware
app.include_router(auth_router)
app.middleware("http")(auth_middleware)


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    """Serve the single-page frontend."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health", include_in_schema=False)
async def health():
    """Liveness probe for load balancers / uptime checks (never behind login)."""
    return {"status": "ok"}


# ──────────────────────────────────────────────
# Form completion time tracking
# ──────────────────────────────────────────────

@app.get("/t/{token}", include_in_schema=False)
async def track_and_redirect(token: str):
    """
    Tracking redirect. The recruitment email links here instead of directly to
    the Google Form. We record the moment the candidate opens the form, then
    redirect them straight to the real Google Form (email pre-filled so we can
    match their submission later). This is invisible to the candidate.
    """
    rec = form_tracking.record_click(token)
    if not rec:
        # Unknown token — fall back to the stored base form URL if we have one,
        # otherwise show a friendly message.
        cfg = _load_tracking_config()
        if cfg.get("base_form_url"):
            return RedirectResponse(cfg["base_form_url"], status_code=302)
        raise HTTPException(status_code=404, detail="Invalid or expired link.")

    cfg = _load_tracking_config()
    base_url = cfg.get("base_form_url", "")
    if not base_url:
        raise HTTPException(status_code=500,
                            detail="Form URL not configured on the server.")

    target = form_tracking.build_form_redirect(
        base_url, rec["email"], cfg.get("email_entry_id")
    )
    return RedirectResponse(target, status_code=302)


def _tracking_config_path() -> Path:
    return OUTPUT_DIR / "tracking_config.json"


def _load_tracking_config() -> dict:
    import json as _json
    p = _tracking_config_path()
    if p.exists():
        try:
            return _json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_tracking_config(cfg: dict) -> None:
    import json as _json
    _tracking_config_path().write_text(_json.dumps(cfg, indent=2))


class TrackingConfig(BaseModel):
    base_form_url: str
    email_entry_id: Optional[str] = None
    public_base_url: Optional[str] = None   # e.g. https://hr.hopcharge.com


@app.post("/api/tracking/config")
async def set_tracking_config(cfg: TrackingConfig):
    """Save the Google Form URL + email entry id used to build redirect links."""
    if not cfg.base_form_url.startswith("http"):
        raise HTTPException(status_code=400, detail="A valid form URL is required.")
    saved = {
        "base_form_url": cfg.base_form_url.strip(),
        "email_entry_id": (cfg.email_entry_id or "").strip() or None,
        "public_base_url": (cfg.public_base_url or "").strip().rstrip("/") or None,
    }
    _save_tracking_config(saved)
    return {"success": True, **saved}


@app.get("/api/tracking/config")
async def get_tracking_config():
    """Return the current tracking configuration."""
    return _load_tracking_config()


class TrackingEnabledBody(BaseModel):
    enabled: bool


@app.get("/api/tracking/enabled")
async def get_tracking_enabled():
    """
    Whether click/completion-time tracking is turned on. OFF by default —
    campaign emails then link straight to the Google Form, with no need for
    this server to be a publicly reachable HTTPS endpoint. Persisted so the
    choice survives restarts/reloads, same pattern as the AI-scoring toggle.
    """
    return {"success": True, "enabled": bool(_load_tracking_config().get("tracking_enabled", False))}


@app.post("/api/tracking/enabled")
async def set_tracking_enabled(body: TrackingEnabledBody):
    cfg = _load_tracking_config()
    cfg["tracking_enabled"] = bool(body.enabled)
    _save_tracking_config(cfg)
    return {"success": True, "enabled": cfg["tracking_enabled"]}


@app.get("/api/tracking/summary")
async def get_tracking_summary():
    """Aggregate completion-time statistics for the dashboard."""
    return form_tracking.tracking_summary()


@app.get("/api/tracking/all")
async def get_tracking_all():
    """All per-candidate tracking rows for the analytics table."""
    return {"records": form_tracking.get_all_tracking()}


@app.get("/api/tracking/candidate")
async def get_tracking_candidate(email: str):
    """Tracking info for a single candidate (used in the candidate modal)."""
    rec = form_tracking.get_tracking_for_email(email)
    if not rec:
        return {"found": False}
    return {"found": True, **rec}


@app.post("/api/tracking/sync")
async def sync_tracking():
    """
    Match stored Google Form responses against tracking records to compute
    time-taken for everyone who has both a click and a submission on file.
    """
    import json as _json
    responses_file = OUTPUT_DIR / "form_responses.json"
    if not responses_file.exists():
        raise HTTPException(status_code=404, detail="No form responses found yet.")

    data = _json.loads(responses_file.read_text())
    responses = data.get("responses", [])
    summary = form_tracking.sync_submissions_from_responses(responses)
    return {"success": True, **summary}


# ──────────────────────────────────────────────
# HR operations analytics
# ──────────────────────────────────────────────

@app.get("/api/analytics/overview")
async def analytics_overview(window: str = "30d"):
    """Aggregate HR-operations metrics for one time window (today/7d/30d/90d/180d/1y/all)."""
    import analytics_hr
    return analytics_hr.overview(window)


@app.get("/api/analytics/windows")
async def analytics_windows():
    """Available time-window keys for the Analytics section."""
    import analytics_hr
    return {"windows": analytics_hr.windows()}


@app.post("/api/parse")
async def parse_cvs(files: List[UploadFile] = File(...)):
    """Accept PDF uploads, run the resume parser, export results, return JSON."""
    records = []
    errors  = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for upload in files:
            if not upload.filename.lower().endswith(".pdf"):
                errors.append({"file": upload.filename, "error": "Not a PDF file"})
                continue

            pdf_path = Path(tmpdir) / upload.filename
            pdf_path.write_bytes(await upload.read())

            try:
                raw_text, method = extract_text(str(pdf_path))

                # Prefer AI-based structured extraction (handles arbitrary resume
                # layouts far more consistently) when the AI-scoring feature is
                # enabled and a provider is configured; silently fall back to the
                # free, fully offline regex+spaCy parser on ANY failure (feature
                # off, no credits, bad response, network error, etc.) so parsing
                # always succeeds either way.
                parse_method = "regex"
                record = None
                try:
                    import ai_resume_parser
                    if ai_resume_parser.is_available():
                        record = ai_resume_parser.parse_resume_ai(raw_text, source_file=upload.filename)
                        parse_method = "ai"
                except Exception as ai_exc:
                    logger.warning("AI resume parse failed for %s, falling back to regex parser: %s",
                                   upload.filename, ai_exc)
                    record = None

                if record is None:
                    record = parse_resume(raw_text, source_file=upload.filename)

                record.field_confidence["extraction_method"] = method
                record.field_confidence["parse_method"] = parse_method
                records.append(record)
                logger.info(
                    "Parsed (%s): %s → name=%s email=%s skills=%d exp=%d",
                    parse_method, upload.filename,
                    record.full_name or "(none)",
                    record.email or "(none)",
                    len(record.skills),
                    len(record.work_experience),
                )
            except Exception as exc:
                logger.error("Failed on %s: %s", upload.filename, exc, exc_info=True)
                errors.append({"file": upload.filename, "error": str(exc)})

    if records:
        export_all(records)
        try:
            import analytics_hr
            analytics_hr.log_event("cv_parsed", {"count": len(records), "source": "upload"})
        except Exception:
            pass

    return JSONResponse(content={
        "success":    True,
        "count":      len(records),
        "errors":     errors,
        "candidates": [r.to_dict() for r in records],
        "downloads":  {
            "json": "/api/download/json",
            "csv":  "/api/download/csv",
            "xlsx": "/api/download/xlsx",
        } if records else {},
    })


@app.get("/api/download/{fmt}")
async def download_results(fmt: str):
    """Serve the most recently parsed export file."""
    FORMAT_MAP = {
        "json": ("candidates.json", "application/json"),
        "csv":  ("candidates.csv",  "text/csv"),
        "xlsx": (
            "candidates.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    }
    if fmt not in FORMAT_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown format '{fmt}'")

    filename, media_type = FORMAT_MAP[fmt]
    file_path = OUTPUT_DIR / filename

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail="No results available yet. Parse some CVs first.",
        )

    return FileResponse(path=str(file_path), media_type=media_type, filename=filename)


# ──────────────────────────────────────────────
# Email campaign endpoint
# ──────────────────────────────────────────────

class EmailRequest(BaseModel):
    form_link:         Optional[str] = None   # falls back to Admin Settings' saved recruitment form link
    tracking_base_url: Optional[str] = None   # e.g. http://localhost:8000
    email_entry_id:    Optional[str] = None   # e.g. entry.123456789 (form email field)
    manual_emails:     Optional[str] = None   # newline/comma-separated — send without parsed CVs


@app.post("/api/send-emails")
async def send_emails(req: EmailRequest):
    """
    Send a personalised HopCharge recruitment email to every parsed candidate
    that has a valid email address via the connected Google account (Gmail OAuth).
    """
    from emailer import send_campaign
    import gmail_oauth
    import admin_settings

    form_link = (req.form_link or "").strip() or admin_settings.get_recruitment_form().get("form_link", "")
    if not form_link or not form_link.startswith("http"):
        raise HTTPException(status_code=400, detail="No recruitment form link configured. Set it in Admin Settings first.")

    if not gmail_oauth.is_connected():
        raise HTTPException(
            status_code=400,
            detail="Google account is not connected. Click 'Connect Google Account' above first."
        )
    try:
        gmail_service = gmail_oauth.get_gmail_service()
    except gmail_oauth.GmailNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    manual_candidates = None
    if req.manual_emails and req.manual_emails.strip():
        import re as _re
        raw = _re.split(r"[\n,\s]+", req.manual_emails.strip())
        manual_candidates = [
            {"name": e.split("@")[0].replace(".", " ").title(), "email": e}
            for e in raw if "@" in e
        ]
        if not manual_candidates:
            raise HTTPException(status_code=400, detail="No valid email addresses found in manual list.")

    try:
        if req.tracking_base_url:
            _save_tracking_config({
                "base_form_url": form_link,
                "email_entry_id": (req.email_entry_id or "").strip() or None,
                "public_base_url": req.tracking_base_url.strip().rstrip("/"),
            })

        summary = send_campaign(
            form_link=form_link,
            tracking_base_url=req.tracking_base_url,
            candidates=manual_candidates,
            gmail_service=gmail_service,
        )
        try:
            import analytics_hr
            analytics_hr.log_event("email_sent", {"count": int(summary.get("sent", 0)), "kind": "recruitment"})
        except Exception:
            pass
        logger.info(
            "Email campaign complete — sent: %d, failed: %d, skipped: %d, tracked: %d",
            summary["sent"], summary["failed"], summary["skipped_no_email"],
            summary.get("tracked", 0),
        )
        return JSONResponse(content={"success": True, **summary})

    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("Starting VOLT.CV server → http://localhost:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)


# ──────────────────────────────────────────────
# Google Forms endpoints
# ──────────────────────────────────────────────

class FormsRequest(BaseModel):
    form_id:          Optional[str] = None      # falls back to Admin Settings' saved recruitment form ID
    auth_mode:        str = "service_account"   # "oauth" (same connection as Send Emails) or "service_account" (fallback)
    credentials_json: Optional[str] = None      # required only when auth_mode == "service_account"


@app.post("/api/forms/fetch")
async def fetch_form_responses(req: FormsRequest):
    """
    Authenticate with Google, pull all form responses, parse them, upsert
    into output/form_responses.json, and return the full structured dataset.
    """
    from forms_retriever import sync_form_responses
    import admin_settings

    form_id = (req.form_id or "").strip() or admin_settings.get_recruitment_form().get("form_id", "")
    if not form_id:
        raise HTTPException(status_code=400, detail="No recruitment form ID configured. Set it in Admin Settings first.")
    if req.auth_mode == "oauth":
        import gmail_oauth
        if not gmail_oauth.is_connected():
            raise HTTPException(
                status_code=400,
                detail="Google account is not connected. Connect it on the Send Emails page first."
            )
    else:
        if not req.credentials_json or not req.credentials_json.strip():
            raise HTTPException(status_code=400, detail="credentials_json is required.")

    try:
        result = sync_form_responses(
            form_id=form_id,
            credentials_json=(req.credentials_json or "").strip(),
            auth_mode=req.auth_mode,
        )

        # Auto-match the freshly fetched responses against tracking records so
        # each candidate's form-completion time is computed immediately.
        try:
            responses = result.get("responses") or []
            if responses:
                track = form_tracking.sync_submissions_from_responses(responses)
                result["tracking_sync"] = track
                logger.info("Tracking auto-sync — matched: %d, no-click: %d, "
                           "unmatched: %d", track["matched"],
                           track["no_click_time"], track["unmatched"])
        except Exception as exc:
            logger.warning("Tracking auto-sync skipped: %s", exc)

        return JSONResponse(content={"success": True, **result})

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/forms/responses")
async def get_form_responses():
    """Return the locally stored form responses (last synced snapshot)."""
    from forms_retriever import get_stored_responses

    data = get_stored_responses()
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No form responses stored yet. Run a sync first."
        )
    return JSONResponse(content={"success": True, **data})


@app.get("/api/forms/download")
async def download_form_responses():
    """Download the raw form_responses.json file."""
    from config import OUTPUT_DIR
    path = OUTPUT_DIR / "form_responses.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No responses stored yet.")
    return FileResponse(path=str(path), media_type="application/json",
                        filename="form_responses.json")


@app.delete("/api/forms/responses/{response_id}")
async def delete_form_response(response_id: str):
    """Remove a single candidate response from the local store by response_id."""
    from forms_retriever import load_store, save_store

    store = load_store()
    original_count = len(store["responses"])
    store["responses"] = [r for r in store["responses"] if r["response_id"] != response_id]

    if len(store["responses"]) == original_count:
        raise HTTPException(status_code=404, detail=f"Response '{response_id}' not found.")

    store["total"] = len(store["responses"])
    save_store(store)

    return JSONResponse(content={
        "success": True,
        "deleted": response_id,
        "remaining": store["total"],
    })


# ──────────────────────────────────────────────
# Onboarding email — send to finalised Round 2 candidates
# ──────────────────────────────────────────────

class OnboardingEmailRequest(BaseModel):
    form_link:     str
    response_ids:  Optional[List[str]] = None   # required: only these candidates are emailed


@app.post("/api/send-onboarding")
async def send_onboarding_emails(req: OnboardingEmailRequest):
    """
    Send the onboarding congratulations email to selected Round 2 candidates via connected Google account.
    """
    from emailer import send_onboarding
    from accepted_store import list_accepted
    import gmail_oauth

    if not gmail_oauth.is_connected():
        raise HTTPException(status_code=400, detail="Google account is not connected.")

    try:
        all_r2 = list_accepted(stage="round2")
        if not req.response_ids:
            raise HTTPException(
                status_code=400,
                detail="No candidates selected. Mark candidates as SELECTED before sending onboarding."
            )
        wanted = set(req.response_ids)
        candidates = [c for c in all_r2 if c.get("response_id") in wanted]

        if not candidates:
            raise HTTPException(
                status_code=400,
                detail="None of the selected candidates are in Round 2."
            )

        result = send_onboarding(
            form_link=req.form_link.strip(),
            candidates=candidates,
        )
        try:
            import analytics_hr
            analytics_hr.log_event("email_sent", {"count": int(result.get("sent", 0)), "kind": "onboarding"})
        except Exception:
            pass
        return JSONResponse(content={"success": True, **result})

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ──────────────────────────────────────────────
# Rejection email — sent to explicitly-selected rejected candidates
# ──────────────────────────────────────────────

class RejectionEmailRequest(BaseModel):
    response_ids:  Optional[List[str]] = None   # required: only these candidates are emailed


@app.post("/api/send-rejection")
async def send_rejection_emails(req: RejectionEmailRequest):
    """
    Send the rejection notice to explicitly-selected candidates from the
    Rejected tab. Mirrors /api/send-onboarding's safety pattern: an
    empty/missing response_ids list is rejected outright rather than
    defaulting to "everyone rejected" — sending mail is a one-way action
    that must be an explicit choice, not an accidental bulk default.
    """
    from emailer import send_rejection
    import rejected_store
    import gmail_oauth

    if not gmail_oauth.is_connected():
        raise HTTPException(status_code=400, detail="Google account is not connected.")

    if not req.response_ids:
        raise HTTPException(
            status_code=400,
            detail="No candidates selected. Check candidates in the Rejected tab before sending."
        )
    wanted = set(req.response_ids)
    candidates = [c for c in rejected_store.list_rejected() if c.get("response_id") in wanted]

    if not candidates:
        raise HTTPException(
            status_code=400,
            detail="None of the selected candidates are in the Rejected list."
        )

    try:
        result = send_rejection(candidates=candidates)
        # Only stamp candidates whose send genuinely succeeded — a failed
        # send must stay retryable, not silently marked as notified.
        for r in result.get("results", []):
            if r.get("status") == "sent" and r.get("response_id"):
                rejected_store.mark_rejection_emailed(r["response_id"])
        try:
            import analytics_hr
            analytics_hr.log_event("email_sent", {"count": int(result.get("sent", 0)), "kind": "rejection"})
        except Exception:
            pass
        return JSONResponse(content={"success": True, **result})

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
