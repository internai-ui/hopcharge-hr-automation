"""
form_tracking.py — Genuine form completion time tracking via redirect links.

HOW IT WORKS (the real, working mechanism)
───────────────────────────────────────────────────────────────────────────
Google Forms is hosted by Google, so we cannot inject timers into the form.
But we CAN measure total completion time using a redirect-link technique:

  1. Instead of emailing the raw Google Form URL, we email a TRACKING LINK
     that points to OUR server:  http://our-server/t/<token>

  2. Each candidate gets a unique token tied to their email. When they click
     the link, our /t/<token> endpoint records the CLICK TIME (the moment
     they opened the form) and redirects them to the real Google Form —
     with their email pre-filled so we can match the submission later.

  3. When the candidate submits the Google Form, the Forms API gives us the
     SUBMIT TIME (response.createTime) and their email.

  4. TIME TAKEN = submit_time − click_time.

This is a genuine, industry-standard approach (the same idea email marketers
use for click tracking). It measures real elapsed time from opening the form
to submitting it.

Edge cases handled:
  • Candidate clicks multiple times → we keep the FIRST click (earliest open).
    (Configurable: see RECLICK_POLICY.)
  • Candidate never clicks but somehow submits → time taken is unknown (None).
  • Candidate clicks but never submits → tracked as "opened, not submitted".
  • Multiple candidates → each has a unique token + email match.

Storage: output/form_tracking.json
───────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.tracking")

TRACKING_FILE = OUTPUT_DIR / "form_tracking.json"

# If a candidate clicks the link again, should we reset the clock?
#   "first"  → keep earliest click (measures total time incl. breaks) [default]
#   "last"   → reset to latest click (measures last continuous attempt)
# NOTE: "first" is the robust choice. "last" lets a re-open AFTER submission
# push click_time past submit_time, producing a negative elapsed that used to
# be silently floored to a fake "0s". Keep "first" unless you specifically
# want to measure only the final continuous attempt.
RECLICK_POLICY = "first"

_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════
# Storage helpers
# ══════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if not TRACKING_FILE.exists():
        return {"tokens": {}, "by_email": {}}
    try:
        return json.loads(TRACKING_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Tracking file corrupt — starting fresh.")
        return {"tokens": {}, "by_email": {}}


def _save(data: dict) -> None:
    TRACKING_FILE.write_text(json.dumps(data, indent=2))


# ══════════════════════════════════════════════════════════════════════════
# 1. Issue a tracking token for a candidate (called when sending emails)
# ══════════════════════════════════════════════════════════════════════════

def issue_token(email: str, name: str = "") -> str:
    """
    Create (or reuse) a unique tracking token for this candidate.
    Returns the token string used to build the tracking URL.
    """
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("Email is required to issue a tracking token.")

    with _lock:
        data = _load()

        # Reuse an existing token for this email if one exists (idempotent
        # across repeated campaigns — keeps history consistent).
        existing = data["by_email"].get(email)
        if existing:
            return existing

        token = secrets.token_urlsafe(12)   # ~16-char URL-safe token
        data["tokens"][token] = {
            "email": email,
            "name": name,
            "issued_at": _now_iso(),
            "click_time": None,        # first time they opened the form
            "click_count": 0,
            "last_click_time": None,
            "submit_time": None,       # filled in from Google Forms API
            "time_taken_seconds": None,
            "status": "issued",        # issued → opened → submitted
        }
        data["by_email"][email] = token
        _save(data)
        logger.info("Issued tracking token for %s", email)

    # Outside the lock -- this is a network call (see the "no network calls
    # under the lock" convention shared with email_replies.py's poller).
    push_token_to_cloudflare(token, email, name)
    return token


# ══════════════════════════════════════════════════════════════════════════
# 2. Record a click + build the redirect target (called by /t/<token>)
# ══════════════════════════════════════════════════════════════════════════

def record_click(token: str) -> Optional[dict]:
    """
    Record that the candidate opened the form (clicked the tracking link).
    Returns the token record (incl. email) so the caller can build the
    Google Form redirect URL. Returns None if the token is unknown.
    """
    with _lock:
        data = _load()
        rec = data["tokens"].get(token)
        if not rec:
            logger.warning("Unknown tracking token clicked: %s", token)
            return None

        now = _now_iso()
        rec["click_count"] += 1
        rec["last_click_time"] = now

        if rec["click_time"] is None:
            rec["click_time"] = now
            rec["status"] = "opened"
            logger.info("First open recorded for %s", rec["email"])
        elif RECLICK_POLICY == "last":
            rec["click_time"] = now
            logger.info("Re-click reset clock for %s", rec["email"])

        _save(data)
        return dict(rec)


def build_form_redirect(base_form_url: str, email: str,
                        email_entry_id: Optional[str] = None) -> str:
    """
    Build the Google Form URL to redirect the candidate to.

    If email_entry_id is provided (the form's email field entry ID, like
    'entry.123456789'), we pre-fill the candidate's email so we can match
    their submission back to their click. If not provided, matching relies
    on the form collecting emails automatically (respondentEmail).

    To find the entry ID: open the form → "Get pre-filled link" → fill the
    email field with a test value → copy link → the entry.XXXX before your
    test value is the email_entry_id.
    """
    url = base_form_url.strip()
    if not email_entry_id:
        return url

    sep = "&" if "?" in url else "?"
    prefill = urllib.parse.urlencode({email_entry_id: email})
    return f"{url}{sep}{prefill}"


# ══════════════════════════════════════════════════════════════════════════
# 3. Match a Google Forms submission → compute time taken
# ══════════════════════════════════════════════════════════════════════════

def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO timestamp (handles trailing Z)."""
    if not ts:
        return None
    try:
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def record_submission(email: str, submit_time_iso: str) -> Optional[dict]:
    """
    Record a Google Form submission and compute time taken.

    Parameters
    ----------
    email           : candidate's email (from respondentEmail or prefilled field)
    submit_time_iso : response.createTime from the Forms API (ISO 8601)

    Returns the updated token record with time_taken_seconds, or None if we
    have no click record for this email (so we can't compute elapsed time).
    """
    email = (email or "").strip().lower()
    with _lock:
        data = _load()
        token = data["by_email"].get(email)
        if not token:
            logger.info("Submission from untracked email: %s", email)
            return None

        rec = data["tokens"][token]
        rec["submit_time"] = submit_time_iso
        rec["status"] = "submitted"

        click_dt = _parse_iso(rec["click_time"])
        submit_dt = _parse_iso(submit_time_iso)

        if click_dt and submit_dt:
            elapsed = (submit_dt - click_dt).total_seconds()
            if elapsed < 0:
                # Submit BEFORE click is impossible for a real completion. It
                # means the data is inconsistent (e.g. the form was reopened
                # after submitting and RECLICK reset the clock, or the click
                # was never genuinely recorded). Store None so the UI shows
                # "—" rather than a misleading "0s".
                rec["time_taken_seconds"] = None
                logger.warning(
                    "Submit before click for %s (submit=%s, click=%s) — "
                    "elapsed=%.0fs is impossible; recording None.",
                    email, submit_time_iso, rec["click_time"], elapsed)
            else:
                rec["time_taken_seconds"] = round(elapsed, 1)
                logger.info("Time taken for %s: %.0f sec", email,
                            rec["time_taken_seconds"])
        else:
            rec["time_taken_seconds"] = None
            logger.info("Submission recorded for %s but no click time to "
                       "compute elapsed.", email)

        _save(data)
        return dict(rec)


def sync_submissions_from_responses(responses: list[dict],
                                   email_key: str = "email",
                                   time_key: str = "submitted_at") -> dict:
    """
    Bulk-match a list of Google Form responses against tracking records.

    Pass the responses you already pull in forms_retriever (which stores each
    response with an "email" and a "submitted_at" ISO timestamp). For each
    response we read the candidate's email and submission time and compute
    time-taken = submit_time − click_time.

    Returns a summary: {matched, unmatched, no_click_time}.
    """
    matched = unmatched = no_click = 0
    for r in responses:
        email = (r.get(email_key) or r.get("respondentEmail") or "").strip()
        submit_time = (r.get(time_key) or r.get("createTime")
                      or r.get("submit_time") or r.get("timestamp"))
        if not email or not submit_time:
            unmatched += 1
            continue
        result = record_submission(email, submit_time)
        if result is None:
            unmatched += 1
        elif result.get("time_taken_seconds") is None:
            no_click += 1
        else:
            matched += 1

    return {"matched": matched, "unmatched": unmatched, "no_click_time": no_click}


# ══════════════════════════════════════════════════════════════════════════
# 4. Read tracking data for the dashboard
# ══════════════════════════════════════════════════════════════════════════

def _humanize(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def get_tracking_for_email(email: str) -> Optional[dict]:
    """Return tracking info for one candidate (used in the candidate modal)."""
    email = (email or "").strip().lower()
    data = _load()
    token = data["by_email"].get(email)
    if not token:
        return None
    rec = dict(data["tokens"][token])
    rec["time_taken_human"] = _humanize(rec.get("time_taken_seconds"))
    return rec


def get_all_tracking() -> list[dict]:
    """Return all tracking records, newest first — for the analytics table."""
    data = _load()
    out = []
    for token, rec in data["tokens"].items():
        row = dict(rec)
        row["token"] = token
        row["time_taken_human"] = _humanize(rec.get("time_taken_seconds"))
        out.append(row)
    out.sort(key=lambda r: r.get("issued_at") or "", reverse=True)
    return out


def tracking_summary() -> dict:
    """Aggregate statistics for the dashboard."""
    rows = get_all_tracking()
    completed = [r for r in rows if r.get("time_taken_seconds") is not None]
    times = [r["time_taken_seconds"] for r in completed]

    issued    = len(rows)
    opened    = sum(1 for r in rows if r["status"] in ("opened", "submitted"))
    submitted = sum(1 for r in rows if r["status"] == "submitted")

    summary = {
        "issued": issued,
        "opened": opened,
        "submitted": submitted,
        "completed_with_time": len(completed),
        "open_rate": round(opened / issued * 100, 1) if issued else 0,
        "completion_rate": round(submitted / issued * 100, 1) if issued else 0,
    }

    if times:
        times_sorted = sorted(times)
        n = len(times_sorted)
        median = (times_sorted[n // 2] if n % 2
                 else (times_sorted[n // 2 - 1] + times_sorted[n // 2]) / 2)
        summary.update({
            "avg_seconds": round(sum(times) / n, 1),
            "avg_human": _humanize(sum(times) / n),
            "median_seconds": round(median, 1),
            "median_human": _humanize(median),
            "fastest_seconds": round(min(times), 1),
            "fastest_human": _humanize(min(times)),
            "slowest_seconds": round(max(times), 1),
            "slowest_human": _humanize(max(times)),
        })
    else:
        summary.update({
            "avg_seconds": None, "avg_human": None,
            "median_seconds": None, "median_human": None,
            "fastest_seconds": None, "fastest_human": None,
            "slowest_seconds": None, "slowest_human": None,
        })

    return summary


# ══════════════════════════════════════════════════════════════════════════
# 5. Optional: Cloudflare Workers as the public redirect endpoint
#
# If you don't want your own server publicly reachable, deploy the Worker
# in cloudflare/worker.js instead — it does the exact same click-time
# recording + redirect as track_and_redirect() in app.py, but runs on
# Cloudflare's free tier (100k requests/day) instead of your own machine.
# See cloudflare/README.md for setup.
#
# Wire-up: issue_token() pushes each new token to the Worker's KV store
# (so it's ready the moment the campaign email goes out); app.py's
# _save_tracking_config() pushes the form URL/email-entry-id config the
# same way. A periodic sync (mirrors dual_writer.py's background-refresh
# and email_replies.py's poller) pulls click updates back into this file.
#
# Every function here is a no-op if the three CLOUDFLARE_* env vars below
# aren't set — purely additive. The existing self-hosted /t/<token> route
# in app.py keeps working with or without this.
# ══════════════════════════════════════════════════════════════════════════

_CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
_CF_NAMESPACE_ID = os.environ.get("CLOUDFLARE_KV_NAMESPACE_ID", "").strip()
_CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()


def cloudflare_configured() -> bool:
    return bool(_CF_ACCOUNT_ID and _CF_NAMESPACE_ID and _CF_API_TOKEN)


def _cf_kv_url(key: str = "") -> str:
    base = (f"https://api.cloudflare.com/client/v4/accounts/{_CF_ACCOUNT_ID}"
            f"/storage/kv/namespaces/{_CF_NAMESPACE_ID}")
    return f"{base}/values/{urllib.parse.quote(key, safe='')}" if key else base


def _cf_request(url: str, method: str = "GET", data: Optional[bytes] = None):
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {_CF_API_TOKEN}", "Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def push_token_to_cloudflare(token: str, email: str, name: str = "") -> None:
    """Push a newly-issued token to the Worker's KV store. Best-effort —
    failures are logged, never raised, since a campaign send must not fail
    just because Cloudflare sync hiccuped (the token still works fine with
    the self-hosted /t/<token> route if that's what's configured)."""
    if not cloudflare_configured():
        return
    record = {
        "email": email, "name": name, "issued_at": _now_iso(),
        "click_time": None, "click_count": 0, "last_click_time": None,
    }
    try:
        _cf_request(_cf_kv_url(f"token:{token}"), method="PUT",
                    data=json.dumps(record).encode("utf-8"))
    except Exception as exc:
        logger.warning("Could not push token %s to Cloudflare KV: %s", token, exc)


def push_tracking_config_to_cloudflare(base_form_url: str, email_entry_id: Optional[str] = None) -> None:
    """Push the form URL / email-entry-id config the Worker needs at
    click-time. Call this wherever _save_tracking_config() is called
    (app.py). Best-effort, same reasoning as push_token_to_cloudflare."""
    if not cloudflare_configured():
        return
    config = {"base_form_url": base_form_url, "email_entry_id": email_entry_id}
    try:
        _cf_request(_cf_kv_url("config:tracking"), method="PUT",
                    data=json.dumps(config).encode("utf-8"))
    except Exception as exc:
        logger.warning("Could not push tracking config to Cloudflare KV: %s", exc)


def sync_clicks_from_cloudflare() -> dict:
    """Pull click_time/click_count updates for every token from the
    Worker's KV store and merge them into local storage. All network calls
    happen OUTSIDE _lock (same principle as email_replies.py's poller) —
    only the final merge+save is locked. Returns {checked, updated}."""
    if not cloudflare_configured():
        return {"checked": 0, "updated": 0, "skipped": "not_configured"}

    remote_records: dict[str, dict] = {}
    cursor = None
    while True:
        list_url = _cf_kv_url() + "/keys?prefix=token%3A"
        if cursor:
            list_url += f"&cursor={cursor}"
        try:
            listing = json.loads(_cf_request(list_url).decode("utf-8"))
        except Exception as exc:
            logger.warning("Cloudflare KV list failed: %s", exc)
            break

        for key_info in listing.get("result", []):
            key_name = key_info.get("name", "")
            token = key_name.split("token:", 1)[-1]
            try:
                remote_records[token] = json.loads(_cf_request(_cf_kv_url(key_name)).decode("utf-8"))
            except Exception as exc:
                logger.warning("Cloudflare KV read failed for %s: %s", token, exc)

        cursor = listing.get("result_info", {}).get("cursor") or None
        if not cursor:
            break

    checked = len(remote_records)
    updated = 0
    with _lock:
        data = _load()
        changed = False
        for token, remote in remote_records.items():
            local = data["tokens"].get(token)
            if local is None:
                continue
            if remote.get("click_time") and not local.get("click_time"):
                local["click_time"] = remote["click_time"]
                local["status"] = "opened"
                changed = True
                updated += 1
            if (remote.get("click_count") or 0) > (local.get("click_count") or 0):
                local["click_count"] = remote["click_count"]
                local["last_click_time"] = remote.get("last_click_time")
                changed = True
        if changed:
            _save(data)

    return {"checked": checked, "updated": updated}


_CF_SYNC_INTERVAL = 300  # seconds — matches email_replies.py's poll cadence


def _cf_poller_loop() -> None:
    """Daemon thread, started at import — same pattern as email_replies.py's
    _poller_loop(). Does nothing every cycle until CLOUDFLARE_* is
    configured, at which point it starts pulling click updates."""
    import time as _time
    while True:
        _time.sleep(_CF_SYNC_INTERVAL)
        if not cloudflare_configured():
            continue
        try:
            sync_clicks_from_cloudflare()
        except Exception as exc:
            logger.error("Cloudflare click sync failed: %s", exc, exc_info=True)


_cf_poller_thread = threading.Thread(target=_cf_poller_loop, daemon=True)
_cf_poller_thread.start()
