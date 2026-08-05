"""
forms_retriever.py — Google Forms response retrieval and structured storage.

Pulls candidate answers from a Google Form via the Forms API v1, maps
question IDs to human-readable text, and stores everything in a schema
that is directly consumable by the AI evaluation pipeline (next step).

──────────────────────────────────────────────
AUTHENTICATION  (service account — recommended)
──────────────────────────────────────────────
1.  Go to console.cloud.google.com → select / create a project.
2.  Enable both:
      • Google Forms API
      • Google Drive API  (needed to read form metadata)
3.  IAM & Admin → Service Accounts → Create Service Account.
    Name it anything (e.g. "hopcharge-forms-reader").
4.  Click the account → Keys → Add Key → JSON.
    Download the JSON file.  Keep it private — treat it like a password.
5.  Open your Google Form → Share icon → paste the service-account email
    (looks like:  name@project.iam.gserviceaccount.com)  with Viewer access.
6.  Paste the full JSON content into the UI field, or save it as
    credentials/service_account.json in the project folder.

──────────────────────────────────────────────
STORAGE SCHEMA  (output/form_responses.json)
──────────────────────────────────────────────
{
  "form_id":    "1FAIpQL...",
  "form_title": "HopCharge Candidate Assessment",
  "questions":  [{"id": "q01", "text": "...", "type": "textQuestion"}],
  "last_synced": "2025-06-03T12:00:00Z",
  "total":       42,
  "responses": [
    {
      "response_id":   "ACYDBNh...",
      "email":         "candidate@example.com",
      "submitted_at":  "2025-06-03T08:17:05Z",
      "answers": [
        {"question": "Why do you want to join HopCharge?", "type": "textQuestion", "answer": "..."}
      ]
    }
  ]
}
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import re

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.forms")

RESPONSES_FILE = OUTPUT_DIR / "form_responses.json"

SCOPES = [
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/forms.body.readonly",
]


# ──────────────────────────────────────────────
# Form ID extraction
# ──────────────────────────────────────────────

def extract_form_id(raw: str) -> str:
    """
    Accept anything the user pastes and return a usable Form ID.

    Handles:
      • bare ID:           1AbCdEf...                         → 1AbCdEf...
      • edit URL:          .../forms/d/1AbCdEf.../edit        → 1AbCdEf...
      • viewform URL:      .../forms/d/1AbCdEf.../viewform    → 1AbCdEf...
      • prefilled/other:   .../forms/d/1AbCdEf.../...?usp=... → 1AbCdEf...

    The PUBLISHED responder link (/forms/d/e/<ID>/viewform) uses a DIFFERENT
    id ("e/..." form) that the Forms API cannot query. We detect it and raise
    a clear, actionable error instead of a cryptic API 400.
    """
    s = (raw or "").strip().strip('"').strip("'").strip()
    if not s:
        raise ValueError("Form ID is empty. Paste your Google Form ID or edit URL.")

    # Reject the published responder link explicitly — it is NOT API-usable.
    if "/forms/d/e/" in s or re.search(r"/d/e/", s):
        raise ValueError(
            "That looks like the *published* form link (/forms/d/e/...).\n"
            "The API needs the EDIT id instead.\n"
            "  1. Open your form for editing in Google Forms.\n"
            "  2. Copy the URL — it looks like:\n"
            "       https://docs.google.com/forms/d/<FORM_ID>/edit\n"
            "  3. Paste that <FORM_ID> (the part between /d/ and /edit)."
        )

    # Pull the id out of an edit/viewform URL: /forms/d/<ID>/...
    m = re.search(r"/forms/d/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)

    # If it's already a bare id (letters, digits, _ and - only), accept it.
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", s):
        return s

    raise ValueError(
        "Could not read a Form ID from that input.\n"
        "Paste either the bare Form ID, or the edit URL:\n"
        "  https://docs.google.com/forms/d/<FORM_ID>/edit"
    )

# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

def build_service(credentials_json: str):
    """
    Build the Google Forms API client.

    Parameters
    ----------
    credentials_json : str
        Either the full JSON string of a service-account key, or an
        absolute filesystem path to the JSON key file.
    """
    # Accept file path OR raw JSON string
    raw = credentials_json.strip()
    if raw.startswith("{"):
        try:
            creds_dict = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "The Service Account JSON is not valid JSON. "
                "Copy the ENTIRE contents of the downloaded key file "
                f"(including the outer braces). Parser said: {exc}"
            ) from exc
    else:
        path = Path(raw)
        if not path.exists():
            raise ValueError(
                "Credentials must be the full JSON key contents (starting with '{') "
                f"or a valid path to the key file. Got a path that does not exist: {path}"
            )
        with open(path) as f:
            try:
                creds_dict = json.load(f)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Credentials file is not valid JSON: {exc}") from exc

    # Sanity-check the key has the fields a service account needs
    required = {"type", "client_email", "private_key", "token_uri"}
    missing = required - creds_dict.keys()
    if missing:
        raise ValueError(
            "This JSON does not look like a service-account key "
            f"(missing fields: {', '.join(sorted(missing))}). "
            "Make sure you created a SERVICE ACCOUNT key, not an OAuth client."
        )
    if creds_dict.get("type") != "service_account":
        raise ValueError(
            f"Expected a service-account key but got type='{creds_dict.get('type')}'. "
            "In Google Cloud → IAM → Service Accounts → Keys → Add Key → JSON."
        )

    try:
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=SCOPES
        )
    except Exception as exc:
        raise ValueError(
            f"Could not load the service-account key: {exc}. "
            "The private_key field may be corrupted — re-copy the whole JSON file."
        ) from exc

    return build("forms", "v1", credentials=credentials, cache_discovery=False)


def build_service_from_oauth():
    """Build the Forms API client using the SAME Google OAuth connection the
    Send Emails page uses (see gmail_oauth.py) — no separate credential to
    share the form with; the connected account itself must own or have edit
    access to the form. Raises ValueError with an actionable message,
    matching build_service()'s error type, so sync_form_responses() doesn't
    need auth_mode-specific exception handling for this step."""
    import gmail_oauth
    if not gmail_oauth.is_connected():
        raise ValueError(
            "Google account not connected. Connect it on the Send Emails page first."
        )
    try:
        credentials = gmail_oauth.get_credentials()
    except gmail_oauth.GmailNotConnectedError as exc:
        raise ValueError(str(exc)) from exc
    return build("forms", "v1", credentials=credentials, cache_discovery=False)


# ──────────────────────────────────────────────
# Form structure  (questions)
# ──────────────────────────────────────────────

def _question_type(question: dict) -> str:
    """Infer the question type from the question dict keys."""
    for key in ("textQuestion", "choiceQuestion", "scaleQuestion",
                "dateQuestion", "timeQuestion", "ratingQuestion",
                "fileUploadQuestion", "rowQuestion"):
        if key in question:
            return key
    return "unknown"


def fetch_form_structure(service, form_id: str) -> tuple[str, list[dict]]:
    """
    Returns (form_title, questions_list).
    questions_list is ordered as they appear in the form:
        [{"id": "q01", "text": "Full Name", "type": "textQuestion"}, ...]
    """
    form = service.forms().get(formId=form_id).execute()
    title = form.get("info", {}).get("title", "Untitled Form")

    questions = []
    for item in form.get("items", []):
        if "questionItem" not in item:
            continue                            # section headers etc.
        q = item["questionItem"]["question"]
        questions.append({
            "id":       q["questionId"],
            "text":     item.get("title", ""),
            "type":     _question_type(q),
            "required": q.get("required", False),
        })

    return title, questions


# ──────────────────────────────────────────────
# Response parsing
# ──────────────────────────────────────────────

def _extract_answer(answer_block: dict) -> str:
    """Pull the human-readable answer out of an answer block."""
    # Text / scale / choice / date responses all come through textAnswers
    text_ans = answer_block.get("textAnswers", {}).get("answers", [])
    if text_ans:
        return " | ".join(v.get("value", "") for v in text_ans)

    # File-upload responses return drive file IDs — return a placeholder
    file_ans = answer_block.get("fileUploadAnswers", {}).get("answers", [])
    if file_ans:
        return "[file upload: " + ", ".join(f.get("fileId", "") for f in file_ans) + "]"

    return ""


def parse_response(raw: dict, q_index: dict) -> dict:
    """
    Convert one raw API response dict into the structured storage format.

    Parameters
    ----------
    raw     : one item from forms.responses().list()["responses"]
    q_index : {questionId: {text, type}} built from fetch_form_structure
    """
    answers = []
    for q_id, q_meta in q_index.items():
        raw_answer = raw.get("answers", {}).get(q_id)
        answers.append({
            "question": q_meta["text"],
            "type":     q_meta["type"],
            "answer":   _extract_answer(raw_answer) if raw_answer else "",
        })

    return {
        "response_id":  raw["responseId"],
        "email":        raw.get("respondentEmail", ""),
        "submitted_at": raw.get("lastSubmittedTime", raw.get("createTime", "")),
        "answers":      answers,
    }


# ──────────────────────────────────────────────
# Fetch all responses
# ──────────────────────────────────────────────

def fetch_all_responses(service, form_id: str) -> list[dict]:
    """
    Page through the responses list endpoint and return all raw responses.
    Handles pagination automatically.
    """
    raw_responses = []
    page_token = None

    while True:
        kwargs = {"formId": form_id, "pageSize": 500}
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.forms().responses().list(**kwargs).execute()
        batch = result.get("responses", [])
        raw_responses.extend(batch)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return raw_responses


# ──────────────────────────────────────────────
# Storage helpers
# ──────────────────────────────────────────────

def load_store() -> dict:
    """Load existing form_responses.json, or return a fresh empty store."""
    if RESPONSES_FILE.exists():
        with open(RESPONSES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"form_id": "", "form_title": "", "questions": [],
            "last_synced": "", "total": 0, "responses": []}


def save_store(store: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESPONSES_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)


def upsert_responses(store: dict, new_responses: list[dict]) -> int:
    """
    Merge new_responses into the store without duplicating existing ones.
    Returns the count of genuinely new records added.
    """
    existing_ids = {r["response_id"] for r in store["responses"]}
    added = 0
    for r in new_responses:
        if r["response_id"] not in existing_ids:
            store["responses"].append(r)
            added += 1
    store["total"] = len(store["responses"])
    return added


# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

def sync_form_responses(form_id: str, credentials_json: str = "", auth_mode: str = "service_account") -> dict:
    """
    Full pipeline:
      1. Authenticate — via OAuth (auth_mode="oauth", same connection as Send
         Emails) or a service-account key (auth_mode="service_account",
         fallback; credentials_json required in that case)
      2. Fetch form structure (questions)
      3. Fetch all responses
      4. Parse + upsert into the local store
      5. Return a summary

    Returns
    -------
    dict with keys: total, new_added, form_title, last_synced, responses
    """
    # 0. Normalise whatever the user pasted into a real Form ID
    form_id = extract_form_id(form_id)

    # 1. Authenticate (both builders raise ValueError with clear messages)
    if auth_mode == "oauth":
        service = build_service_from_oauth()
    else:
        service = build_service(credentials_json)

    # 2. Fetch the form structure
    try:
        form_title, questions = fetch_form_structure(service, form_id)
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        if status == 404:
            raise ValueError(
                f"Form not found (id: {form_id}).\n"
                "  • Double-check the Form ID is the EDIT id (/forms/d/<ID>/edit)." + (
                    "\n  • Your connected Google account must own or have edit access to this form."
                    if auth_mode == "oauth" else
                    "\n  • Make sure you shared the form with the service-account email (Viewer access)."
                )
            ) from exc
        if status == 403:
            if auth_mode == "oauth":
                raise ValueError(
                    "Permission denied (403).\n"
                    "  • Your connected Google account needs edit access to this form — it must "
                    "be the form owner or a collaborator with edit rights.\n"
                    "  • If you connected before Forms access was added to this app, reconnect "
                    "Gmail on the Send Emails page so the new permission takes effect."
                ) from exc
            raise ValueError(
                "Permission denied (403).\n"
                "  • Enable the Google Forms API AND Google Drive API in your "
                "Cloud project.\n"
                "  • Share the form with the service-account email (Viewer)."
            ) from exc
        if status == 429:
            raise RuntimeError("Rate limited by Google (429). Wait a minute and retry.") from exc
        raise RuntimeError(f"Forms API error ({status}): {exc}") from exc

    # 3. Build question index and fetch responses
    q_index = {q["id"]: {"text": q["text"], "type": q["type"]} for q in questions}

    try:
        raw = fetch_all_responses(service, form_id)
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        raise RuntimeError(f"Could not fetch responses ({status}): {exc}") from exc

    parsed = [parse_response(r, q_index) for r in raw]

    # 4. Upsert into persistent store
    store = load_store()
    store["form_id"]    = form_id
    store["form_title"] = form_title
    store["questions"]  = questions
    store["last_synced"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_added = upsert_responses(store, parsed)
    save_store(store)

    logger.info(
        "Form sync complete — title=%r total=%d new=%d",
        form_title, store["total"], new_added,
    )

    return {
        "form_title":  form_title,
        "total":       store["total"],
        "new_added":   new_added,
        "last_synced": store["last_synced"],
        "questions":   questions,
        "responses":   store["responses"],
    }


def get_stored_responses() -> Optional[dict]:
    """Load and return the stored response data, or None if not yet synced."""
    if not RESPONSES_FILE.exists():
        return None
    return load_store()
