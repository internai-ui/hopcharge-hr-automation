# Hopcharge HR BAU Automation Dashboard (VOLT.CV)

Internal FastAPI dashboard that automates HopCharge's recruitment operations end to end: parsing resumes, dispatching recruitment emails, tracking and classifying candidate replies, syncing Google Form responses, managing the accept/reject pipeline, college outreach, and the employee master sheet.

## What it does

- **CV Parser** — upload resume PDFs (or sync from a Google Drive folder); text is extracted via pdfplumber → PyMuPDF → Tesseract OCR fallback, then parsed into structured fields (name, email, phone, work experience, education, skills, etc.) with a confidence score per field.
- **Send Emails** — dispatches personalised recruitment emails via your connected Google account (OAuth — see "Connect Google Account" below), embedding your Google Form assessment link. Optional click-tracking (off by default — see below), which can run on your own server or on Cloudflare Workers' free tier (see `cloudflare/README.md`) if you don't want a public URL of your own.
- **Replies** — tracks candidate replies to campaign emails via Gmail, and classifies each one (not interested / auto-reply / question / interested / neutral) using either keyword matching (default, offline) or the same LLM provider used for CV parsing, if enabled.
- **Form Responses** — pulls candidate submissions from the Google Forms API.
- **Accepted / Rejected** — a four-stage pipeline (HR Round → Round 1 → Round 2 → Onboarded) with restore-from-rejected support.
- **Analytics** — operational metrics across configurable time windows.
- **College Outreach** — a separate pipeline for discovering, prioritizing, and tracking outreach to college placement cells.
- **Employee Database** — the post-hire master sheet, mirrored to a backup Google Sheet.

Data persists to Postgres (Neon) when `DATABASE_URL` is configured, with a JSON mirror under `output/` as an offline fallback (see `dual_writer.py`). Without `neon.env`, the app runs entirely in JSON-only mode.

## Setup

```bash
git clone <this repo>
cd webhosting
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m spacy download en_core_web_sm
```

Then add a `neon.env` file in this folder (see the commented template it expects — `DATABASE_URL`, `EMPLOYEE_FIELD_KEY`, optionally `DASHBOARD_AUTH`). Without it, the app still runs, just in JSON-only mode with no login requirement.

Google integrations (Forms/Drive/Sheets) need a service-account JSON — see `README-Windows-Setup.md` and the in-app "Form Responses" page for where to paste it.

## Running for development

```bash
cd webhosting
source .venv/bin/activate
python3 -m uvicorn app:app --reload --port 8000
```

Open `http://localhost:8000`. If `DASHBOARD_AUTH` isn't set (or is commented out) in `neon.env`, the dashboard opens directly with no login.

When `DASHBOARD_AUTH=on`, sign-in is Google-only, restricted to email addresses already present in the Employee Database (matched against the `email`/`email_official` fields) — there are no separate accounts or passwords. Admin access is the `is_admin` checkbox on an employee's record. It reuses the same OAuth client already configured for "Connect Google Account" (Send Emails page); see `auth.py`'s module docstring for the one extra Google Cloud Console step (adding `/api/auth/google/callback` as a second Authorized redirect URI on that client).

> If `python3 -m uvicorn` still can't find the module even inside an activated venv, your shell likely has a stale command-hash pointing at a different Python (common with pyenv). Run `hash -r` (zsh/bash) or just call `./.venv/bin/python -m uvicorn app:app --reload --port 8000` directly, which always resolves correctly regardless of shell state.

## Two feature toggles, both off by default

**AI-based CV parsing & reply-intent classification** (Admin Settings → "CV Parsing Mode + Intent Analysis"): a master switch that gates all external LLM provider access (Claude / OpenAI / Gemini / Groq / Hugging Face) for both features. Off by default — CV parsing runs on the offline regex + spaCy parser and replies are classified by keyword matching only, no API key fields shown, no external calls possible even if a key was previously saved. Turning it on reveals provider configuration; once a key is set, AI-based is the selected mode by default (Hugging Face's free-tier router needs no billing, so it's the default provider). Both CV parsing and reply-intent classification fall back to their offline equivalent automatically on any failure — a resume or reply is never left unparsed/unclassified.

**Click-tracking** (Send Emails page): off by default — campaign emails link straight to your Google Form, no public server required. Turning it on measures per-candidate form-completion time via a `/t/<token>` redirect (self-hosted, or via Cloudflare Workers — see `cloudflare/README.md`), which requires that redirect endpoint to be reachable at a public HTTPS URL for real candidates to hit it.

Both are persisted server-side (not per-browser), so the choice survives restarts and is shared across anyone hitting the same server.

## Desktop packaging (alternative to a server deployment)

`launcher.py` / `launcher_win.py` + `hopcharge.spec` (PyInstaller) build a one-click macOS/Windows app that runs the same FastAPI backend locally and opens it in the default browser — see `README-Windows-Setup.md`.

## Production deployment

See `deploy/DEPLOY_GUIDE.md` — nginx + Let's Encrypt in front of a `systemd`-managed `uvicorn` process, with `DASHBOARD_AUTH=on`.

## What's intentionally not in this repo

`.gitignore` excludes everything that isn't source: `neon.env`, service-account JSON files, `auth_users.json`, encryption keys, and the entire `output/` directory (candidate/employee data — this is live business data and PII, not code).
