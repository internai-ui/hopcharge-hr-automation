"""
analytics_hr.py — HR operations analytics across time windows.

Powers the "Analytics" section of the dashboard. It answers: how much work is
the platform doing for HR (CVs parsed, forms/emails sent, responses received,
candidates scored, accepted vs rejected) over today / 7d / 30d / 90d / 180d /
1y / all — broken down role-wise, stage/round-wise, and by recommendation band,
plus a time-series trend for charts.

Two data sources are combined:

  1. An append-only EVENT LOG (output/hr_events.log, one JSON object per line)
     for actions that otherwise store no timestamp:
        {"ts":"2026-06-29T10:00:00Z","type":"cv_parsed","meta":{"count":5}}
        {"ts":"2026-06-29T10:05:00Z","type":"email_sent","meta":{"count":12,"kind":"recruitment"}}
     log_event() is best-effort and must NEVER raise into a caller's flow.

  2. Records the app already timestamps:
        form_responses.json      -> submitted_at      (responses + scoring/bands)
        accepted_candidates.json -> accepted_at, history[].at
        rejected_candidates.json -> rejected_at
        calendly_invite_log.json -> at / timestamp     (interview invites)

Everything here is read-only except log_event(), which appends one line.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.analytics_hr")

EVENTS_FILE: Path = OUTPUT_DIR / "hr_events.log"
RESPONSES_FILE: Path = OUTPUT_DIR / "form_responses.json"
ACCEPTED_FILE: Path = OUTPUT_DIR / "accepted_candidates.json"
REJECTED_FILE: Path = OUTPUT_DIR / "rejected_candidates.json"
CALENDLY_LOG: Path = OUTPUT_DIR / "calendly_invite_log.json"

# Supported time windows → number of days back (None = all time).
WINDOWS = {
    "today": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "180d": 180,
    "1y": 365,
    "all": None,
}


# ──────────────────────────────────────────────
# Event logging (best-effort, append-only JSONL)
# ──────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_event(event_type: str, meta: dict | None = None) -> None:
    """Append one event to the HR event log. Never raises.

    Call sites (all wrapped so a logging failure can't break the real action):
      • CV parsing      → log_event("cv_parsed",  {"count": n, "source": "upload"|"drive"})
      • Email campaigns → log_event("email_sent", {"count": n, "kind": "recruitment"|"onboarding"})
    """
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"ts": _now_iso(), "type": str(event_type), "meta": meta or {}}
        with EVENTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover - logging must never break flow
        logger.debug("log_event skipped (%s): %s", event_type, exc)


def _read_events() -> list[dict]:
    if not EVENTS_FILE.exists():
        return []
    out = []
    try:
        for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


# ──────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────

def _parse_ts(s) -> datetime | None:
    """Parse the app's ISO timestamps (…Z or with offset) into aware UTC."""
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    try:
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _responses() -> list[dict]:
    data = _load_json(RESPONSES_FILE, {"responses": []})
    return data.get("responses", []) if isinstance(data, dict) else []


def _accepted() -> list[dict]:
    data = _load_json(ACCEPTED_FILE, {"accepted": []})
    return data.get("accepted", []) if isinstance(data, dict) else []


def _rejected() -> list[dict]:
    data = _load_json(REJECTED_FILE, {"rejected": []})
    return data.get("rejected", []) if isinstance(data, dict) else []


def _calendly() -> list[dict]:
    data = _load_json(CALENDLY_LOG, [])
    return data if isinstance(data, list) else []


def _role_of(rec: dict) -> str:
    """Best-effort role label for a record (response / accepted / rejected)."""
    r = rec.get("role") or rec.get("role_name") or rec.get("scored_role")
    if r:
        return str(r).strip()
    for a in rec.get("answers", []) or []:
        q = (a.get("question") or "").lower()
        if "role applying" in q or q.strip() == "role" or "position applying" in q:
            v = (a.get("answer") or "").strip()
            if v:
                return v
    return "Unspecified"


def _band(rec: dict) -> str | None:
    """Recommendation band of a scored response, if present."""
    rec_rec = rec.get("recommendation")
    if rec_rec:
        return str(rec_rec)
    return None


def _cutoff(window: str) -> datetime | None:
    days = WINDOWS.get(window, 30)
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _in_window(dt: datetime | None, cutoff: datetime | None) -> bool:
    if dt is None:
        return False
    if cutoff is None:
        return True
    return dt >= cutoff


# ──────────────────────────────────────────────
# Time-series bucketing
# ──────────────────────────────────────────────

def _bucket_plan(window: str):
    """Return (granularity, n_buckets) for the trend chart given the window."""
    days = WINDOWS.get(window, 30)
    if days is None:
        return "month", 12          # all-time → last 12 months
    if days <= 1:
        return "day", 1
    if days <= 31:
        return "day", days
    if days <= 180:
        return "week", max(1, days // 7)
    return "month", max(1, days // 30)


def _bucket_key(dt: datetime, gran: str) -> str:
    if gran == "day":
        return dt.strftime("%Y-%m-%d")
    if gran == "week":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return dt.strftime("%Y-%m")


def _bucket_label(dt: datetime, gran: str) -> str:
    if gran == "day":
        return dt.strftime("%d %b")
    if gran == "week":
        return dt.strftime("%d %b")
    return dt.strftime("%b %Y")


def _series(window: str, events: list[dict]) -> list[dict]:
    """Per-bucket counts of parsed / emails / responses / accepted / rejected."""
    gran, n = _bucket_plan(window)
    now = datetime.now(timezone.utc)

    # Build ordered, empty buckets from oldest → newest.
    buckets: dict[str, dict] = {}
    order: list[str] = []
    cursor = now
    for _ in range(n):
        key = _bucket_key(cursor, gran)
        if key not in buckets:
            buckets[key] = {
                "label": _bucket_label(cursor, gran),
                "parsed": 0, "emails": 0, "responses": 0,
                "accepted": 0, "rejected": 0,
            }
            order.append(key)
        if gran == "day":
            cursor = cursor - timedelta(days=1)
        elif gran == "week":
            cursor = cursor - timedelta(days=7)
        else:
            cursor = cursor - timedelta(days=30)
    order.reverse()

    def add(dt: datetime | None, field: str, count: int = 1):
        if dt is None:
            return
        key = _bucket_key(dt, gran)
        if key in buckets:
            buckets[key][field] += count

    for ev in events:
        dt = _parse_ts(ev.get("ts"))
        cnt = int((ev.get("meta") or {}).get("count", 1) or 0)
        if ev.get("type") == "cv_parsed":
            add(dt, "parsed", cnt)
        elif ev.get("type") == "email_sent":
            add(dt, "emails", cnt)
    for r in _responses():
        add(_parse_ts(r.get("submitted_at")), "responses")
    for a in _accepted():
        add(_parse_ts(a.get("accepted_at")), "accepted")
    for r in _rejected():
        add(_parse_ts(r.get("rejected_at")), "rejected")

    return [buckets[k] for k in order]


# ──────────────────────────────────────────────
# Public: overview for one window
# ──────────────────────────────────────────────

def overview(window: str = "30d") -> dict:
    if window not in WINDOWS:
        window = "30d"
    cutoff = _cutoff(window)
    events = _read_events()

    # ── Event-log totals (CVs parsed, emails sent) ──
    cvs_parsed = 0
    emails_sent = 0
    emails_by_kind: dict[str, int] = {}
    for ev in events:
        dt = _parse_ts(ev.get("ts"))
        if not _in_window(dt, cutoff):
            continue
        meta = ev.get("meta") or {}
        cnt = int(meta.get("count", 1) or 0)
        if ev.get("type") == "cv_parsed":
            cvs_parsed += cnt
        elif ev.get("type") == "email_sent":
            emails_sent += cnt
            kind = str(meta.get("kind", "recruitment"))
            emails_by_kind[kind] = emails_by_kind.get(kind, 0) + cnt

    # ── Responses received + scored + band distribution (role-wise too) ──
    responses_received = 0
    scored = 0
    band_counts: dict[str, int] = {}
    role_responses: dict[str, int] = {}
    for r in _responses():
        if not _in_window(_parse_ts(r.get("submitted_at")), cutoff):
            continue
        responses_received += 1
        role_responses[_role_of(r)] = role_responses.get(_role_of(r), 0) + 1
        if r.get("total_score") is not None and not r.get("scoring_error"):
            scored += 1
            b = _band(r)
            if b:
                band_counts[b] = band_counts.get(b, 0) + 1

    # ── Accepted / rejected within window, role-wise + round/stage-wise ──
    accepted_total = 0
    role_accepted: dict[str, int] = {}
    stage_accepted: dict[str, int] = {}
    for a in _accepted():
        if not _in_window(_parse_ts(a.get("accepted_at")), cutoff):
            continue
        accepted_total += 1
        role_accepted[_role_of(a)] = role_accepted.get(_role_of(a), 0) + 1
        st = a.get("stage") or "hr"
        stage_accepted[st] = stage_accepted.get(st, 0) + 1

    rejected_total = 0
    role_rejected: dict[str, int] = {}
    round_rejected: dict[str, int] = {}
    for r in _rejected():
        if not _in_window(_parse_ts(r.get("rejected_at")), cutoff):
            continue
        rejected_total += 1
        role_rejected[_role_of(r)] = role_rejected.get(_role_of(r), 0) + 1
        rd = r.get("rejected_round") or "Unspecified"
        round_rejected[rd] = round_rejected.get(rd, 0) + 1

    # ── Interview invites (Calendly) within window ──
    invites = 0
    for entry in _calendly():
        dt = _parse_ts(entry.get("at") or entry.get("timestamp") or entry.get("sent_at"))
        if _in_window(dt, cutoff):
            invites += int(entry.get("sent", entry.get("count", 1)) or 0)

    # ── Role-wise combined table (processed = accepted + rejected) ──
    all_roles = set(role_responses) | set(role_accepted) | set(role_rejected)
    role_breakdown = []
    for role in sorted(all_roles):
        acc = role_accepted.get(role, 0)
        rej = role_rejected.get(role, 0)
        role_breakdown.append({
            "role": role,
            "responses": role_responses.get(role, 0),
            "accepted": acc,
            "rejected": rej,
            "processed": acc + rej,
        })
    role_breakdown.sort(key=lambda x: x["processed"], reverse=True)

    decided = accepted_total + rejected_total
    accept_rate = round(100.0 * accepted_total / decided, 1) if decided else 0.0

    return {
        "window": window,
        "generated_at": _now_iso(),
        "cards": {
            "cvs_parsed": cvs_parsed,
            "emails_sent": emails_sent,
            "responses_received": responses_received,
            "candidates_scored": scored,
            "accepted": accepted_total,
            "rejected": rejected_total,
            "interviews_invited": invites,
            "accept_rate": accept_rate,
        },
        "emails_by_kind": emails_by_kind,
        "band_distribution": band_counts,
        "role_breakdown": role_breakdown,
        "stage_accepted": stage_accepted,
        "round_rejected": round_rejected,
        "series": _series(window, events),
        # Note which metrics only exist from the time event-logging began.
        "notes": {
            "event_log_metrics": ["cvs_parsed", "emails_sent"],
            "message": "CVs parsed & emails sent are counted from the day this "
                       "version was installed (older actions had no timestamp). "
                       "All other metrics use existing records.",
        },
    }


def windows() -> list[str]:
    return list(WINDOWS.keys())
