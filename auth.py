"""
auth.py — optional sign-in protection for the dashboard (email + password).

Design goals (in order):
  1. NOTHING breaks when auth is off (the default). Local/desktop bundles keep
     opening straight into the dashboard exactly as before.
  2. Zero new pip dependencies — everything here is Python standard library
     (hashlib / hmac / secrets / json / smtplib), so the Windows & macOS
     installers and the PyInstaller spec need no changes.
  3. Cookie-based sessions, so the existing frontend (static/index.html) works
     unmodified: the browser attaches the session cookie to every fetch()
     automatically. No Authorization headers to wire into the SPA.

How to turn it on (e.g. on the AWS server):
    In neon.env add:      DASHBOARD_AUTH=on
    Optional first admin: DASHBOARD_ADMIN_PASSWORD=<strong password>
    (also auto-enables when ENVIRONMENT=production is set)

On first enabled start, a default admin is created:
    admin@hopcharge.com  /  DASHBOARD_ADMIN_PASSWORD  (or "hopcharge@123")
Change it immediately:  python3 auth.py set-password admin@hopcharge.com

Forgot password (self-service):
    "Forgot password?" on the login page → /reset → the user receives an email
    with a one-hour reset link. Needs the server's own Gmail in neon.env:
        SMTP_GMAIL=hr@hopcharge.com
        SMTP_APP_PASSWORD=<16-char Gmail app password>
    Without SMTP configured, an admin can still hand out reset links from the
    /users page (the link is shown for copying) or use the CLI.

User management (admins only):  open  /users  on the dashboard
    Add a teammate → they get an invite email (or a copyable link) to set
    their own password. Also: change roles, resend reset links, remove users.

Profile sync between installations (optional):
    By default users live in a local file, so each installation (each desktop
    copy, the web server) has its own accounts. Set
        AUTH_SYNC=on
    to store accounts in the shared Neon Postgres database instead — then every
    installation that points at the same DATABASE_URL sees the same users, and
    adding someone on the hosted dashboard also grants them access everywhere.
    First enable seeds the table from the local file, so nobody is lost.

Manage users from a terminal (no server needed):
    python3 auth.py list
    python3 auth.py add someone@hopcharge.com [admin]
    python3 auth.py set-password someone@hopcharge.com
    python3 auth.py remove someone@hopcharge.com

What stays PUBLIC even with auth on (candidate-facing / infrastructure):
    /login, /api/login, /logout, /api/logout      — the sign-in flow itself
    /reset, /api/auth/forgot, /api/auth/reset     — the forgot-password flow
    /t/{token}                                    — form-tracking redirect in candidate emails
    /status, /status/{token}                      — candidate status pages (already
                                                    token-gated + rate-limited internally)
    /api/health                                   — load-balancer liveness probe
    /static/login.html, /static/reset.html,
    /static/hopcharge-white.png                   — assets the public pages need

Files (kept OUTSIDE output/, so wipe_all_data.py never deletes them):
    DATA_HOME/auth_users.json   — user list (salted PBKDF2 password hashes)
    DATA_HOME/auth_secret.key   — HMAC key for session cookies (auto-generated)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets as _secrets
import ssl
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app_paths import DATA_HOME, STATIC_DIR

logger = logging.getLogger("volt_cv.auth")


# Load neon.env when used standalone (the CLI, tests) — same pattern as
# database.py, so DASHBOARD_AUTH / AUTH_SYNC / SMTP_* are honoured everywhere.
# Inside the app this is a no-op because app.py already loaded it.
def _load_neon_env() -> None:
    try:
        from app_paths import NEON_ENV as env_path
    except Exception:
        env_path = Path(__file__).resolve().parent / "neon.env"
    if not env_path.exists():
        env_path = Path(__file__).resolve().parent / "neon.env"
        if not env_path.exists():
            return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass  # a malformed file must never stop startup


_load_neon_env()

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
USERS_FILE = DATA_HOME / "auth_users.json"
SECRET_FILE = DATA_HOME / "auth_secret.key"
COOKIE_NAME = "hc_session"
PBKDF2_ITERATIONS = 240_000
DEFAULT_ADMIN_EMAIL = "admin@hopcharge.com"
RESET_LINK_HOURS = 1        # forgot-password links
INVITE_LINK_HOURS = 72      # links sent to newly added users
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Requests that must work WITHOUT a login session (see module docstring).
PUBLIC_EXACT = {
    "/login", "/logout",
    "/api/login", "/api/logout",
    "/reset", "/api/auth/forgot", "/api/auth/reset",
    "/api/health",
    "/status",
    "/favicon.ico",
    "/static/login.html",
    "/static/reset.html",
    "/static/hopcharge-white.png",
}
PUBLIC_PREFIXES = (
    "/t/",        # tracking links inside candidate emails
    "/status/",   # personal candidate status pages
)


def auth_enabled() -> bool:
    """Read the switch at request time so neon.env fully controls behaviour.

    DASHBOARD_AUTH=on|off wins; otherwise ENVIRONMENT=production enables it.
    Anything else (including unset) means OFF — identical to the app today.
    """
    flag = os.environ.get("DASHBOARD_AUTH", "").strip().lower()
    if flag in ("on", "true", "1", "yes"):
        return True
    if flag in ("off", "false", "0", "no"):
        return False
    return os.environ.get("ENVIRONMENT", "").strip().lower() == "production"


def _sync_enabled() -> bool:
    """AUTH_SYNC=on stores accounts in the shared Neon DB instead of the local
    file, so every installation pointing at the same DATABASE_URL shares them."""
    return os.environ.get("AUTH_SYNC", "").strip().lower() in ("on", "true", "1", "yes")


def _smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_GMAIL", "").strip()
                and os.environ.get("SMTP_APP_PASSWORD", "").strip())


def _session_max_age() -> int:
    try:
        days = float(os.environ.get("SESSION_DAYS", "7"))
    except ValueError:
        days = 7.0
    return int(days * 86400)


def _base_url(request: Request) -> str:
    """Public URL used inside reset/invite emails. DASHBOARD_BASE_URL wins so
    links can't be poisoned by a spoofed Host header; otherwise derive it from
    the request (fine behind our own nginx)."""
    env = os.environ.get("DASHBOARD_BASE_URL", "").strip().rstrip("/")
    if env:
        return env
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


# ──────────────────────────────────────────────
# Secret key (signs session cookies + reset links)
# ──────────────────────────────────────────────
def _secret_key() -> bytes:
    env = os.environ.get("AUTH_SECRET_KEY", "").strip()
    if env:
        return env.encode("utf-8")
    if SECRET_FILE.exists():
        key = SECRET_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key.encode("utf-8")
    key = _secrets.token_hex(32)
    SECRET_FILE.write_text(key, encoding="utf-8")
    try:
        SECRET_FILE.chmod(0o600)
    except Exception:
        pass
    return key.encode("utf-8")


# ──────────────────────────────────────────────
# User store — local file (default) or shared Neon DB (AUTH_SYNC=on).
# Record shape either way:
#   {email: {"salt": hex, "hash": hex, "role": "admin"|"user", "created": iso}}
# ──────────────────────────────────────────────
_file_cache: dict = {}
_file_mtime: float = -1.0


def _file_load() -> dict:
    global _file_cache, _file_mtime
    if not USERS_FILE.exists():
        _file_cache, _file_mtime = {}, -1.0
        return {}
    mtime = USERS_FILE.stat().st_mtime
    if mtime != _file_mtime:
        try:
            _file_cache = json.loads(USERS_FILE.read_text(encoding="utf-8")).get("users", {})
        except Exception:
            logger.exception("auth_users.json is unreadable — treating as empty")
            _file_cache = {}
        _file_mtime = mtime
    return _file_cache


def _file_save(users: dict) -> None:
    USERS_FILE.write_text(json.dumps({"users": users}, indent=2), encoding="utf-8")
    try:
        USERS_FILE.chmod(0o600)
    except Exception:
        pass


# ── DB mode (lazy: nothing DB-related runs unless AUTH_SYNC=on) ──
_db_ready = False
_db_cache: dict = {}
_db_cache_at: float = 0.0
_DB_CACHE_TTL = 5.0   # seconds — keeps login checks cheap without going stale


def _db_init() -> None:
    """Create the shared table once; seed it from the local file on first use
    so turning sync on never loses existing accounts."""
    global _db_ready
    if _db_ready:
        return
    from sqlalchemy import text
    from database import get_engine
    with get_engine().begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_users (
                email   TEXT PRIMARY KEY,
                salt    TEXT NOT NULL,
                hash    TEXT NOT NULL,
                role    TEXT NOT NULL DEFAULT 'user',
                created TEXT,
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """))
        count = c.execute(text("SELECT count(*) FROM dashboard_users")).scalar() or 0
        if count == 0:
            for email, rec in _file_load().items():
                c.execute(text("""
                    INSERT INTO dashboard_users (email, salt, hash, role, created)
                    VALUES (:email, :salt, :hash, :role, :created)
                    ON CONFLICT (email) DO NOTHING
                """), {"email": email, "salt": rec["salt"], "hash": rec["hash"],
                       "role": rec.get("role", "user"), "created": rec.get("created", "")})
    _db_ready = True


def _db_load(force: bool = False) -> dict:
    global _db_cache, _db_cache_at
    if not force and (time.time() - _db_cache_at) < _DB_CACHE_TTL:
        return _db_cache
    from sqlalchemy import text
    from database import get_engine
    _db_init()
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT email, salt, hash, role, created FROM dashboard_users")).mappings().all()
    _db_cache = {r["email"]: {"salt": r["salt"], "hash": r["hash"],
                              "role": r["role"], "created": r["created"]} for r in rows}
    _db_cache_at = time.time()
    return _db_cache


def _db_upsert(email: str, rec: dict) -> None:
    from sqlalchemy import text
    from database import get_engine
    _db_init()
    with get_engine().begin() as c:
        c.execute(text("""
            INSERT INTO dashboard_users (email, salt, hash, role, created, updated_at)
            VALUES (:email, :salt, :hash, :role, :created, now())
            ON CONFLICT (email) DO UPDATE SET
                salt = EXCLUDED.salt, hash = EXCLUDED.hash,
                role = EXCLUDED.role, updated_at = now()
        """), {"email": email, "salt": rec["salt"], "hash": rec["hash"],
               "role": rec.get("role", "user"), "created": rec.get("created", "")})
    global _db_cache_at
    _db_cache_at = 0.0   # drop the cache so the change is visible immediately


def _db_delete(email: str) -> None:
    from sqlalchemy import text
    from database import get_engine
    _db_init()
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM dashboard_users WHERE email = :email"), {"email": email})
    global _db_cache_at
    _db_cache_at = 0.0


def _load_users() -> dict:
    """Single read path used everywhere (login checks, role checks, tokens)."""
    if _sync_enabled():
        try:
            return _db_load()
        except Exception:
            # Neon briefly unreachable must never lock everyone out — fall back
            # to the last good cache, then the local file.
            logger.exception("AUTH_SYNC: database unreachable — using local fallback")
            return _db_cache or _file_load()
    return _file_load()


def _hash_password(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return dk.hex()


def set_user_password(email: str, password: str, role: str = "user") -> None:
    """Create the user if missing, otherwise update the password in place."""
    email = email.strip().lower()
    existing = _load_users().get(email, {})
    salt = _secrets.token_hex(16)
    rec = {
        "salt": salt,
        "hash": _hash_password(password, salt),
        "role": existing.get("role") or role,
        "created": existing.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if _sync_enabled():
        _db_upsert(email, rec)   # errors surface to the caller — a failed write
    else:                        # must not look like success
        users = _file_load().copy()
        users[email] = rec
        _file_save(users)


def set_user_role(email: str, role: str) -> None:
    email = email.strip().lower()
    rec = _load_users().get(email)
    if not rec:
        raise KeyError(email)
    rec = {**rec, "role": role}
    if _sync_enabled():
        _db_upsert(email, rec)
    else:
        users = _file_load().copy()
        users[email] = rec
        _file_save(users)


def delete_user(email: str) -> bool:
    email = email.strip().lower()
    if email not in _load_users():
        return False
    if _sync_enabled():
        _db_delete(email)
    else:
        users = _file_load().copy()
        users.pop(email, None)
        _file_save(users)
    return True


def verify_user(email: str, password: str) -> bool:
    rec = _load_users().get(email.strip().lower())
    if not rec:
        # Burn comparable CPU so a missing account isn't detectable by timing.
        _hash_password(password, "00" * 16)
        return False
    return hmac.compare_digest(rec["hash"], _hash_password(password, rec["salt"]))


def _bootstrap_default_admin() -> None:
    """First enabled start with no users: create the default admin so the
    person deploying isn't locked out of their own dashboard."""
    if _load_users():
        return
    password = os.environ.get("DASHBOARD_ADMIN_PASSWORD", "").strip() or "hopcharge@123"
    set_user_password(DEFAULT_ADMIN_EMAIL, password, role="admin")
    source = ("DASHBOARD_ADMIN_PASSWORD" if os.environ.get("DASHBOARD_ADMIN_PASSWORD")
              else 'the default "hopcharge@123" — CHANGE IT NOW: '
                   "python3 auth.py set-password " + DEFAULT_ADMIN_EMAIL)
    logger.warning("Auth: created first user %s with password from %s",
                   DEFAULT_ADMIN_EMAIL, source)


# ──────────────────────────────────────────────
# Signed tokens — sessions (cookie) and reset links share one signer.
# Both embed a slice of the password hash ("v"), so changing a password
# invalidates every old session AND makes reset links single-use.
# ──────────────────────────────────────────────
def _sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    sig = hmac.new(_secret_key(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _unsign(token: str) -> Optional[dict]:
    try:
        raw, sig = token.split(".", 1)
        expected = hmac.new(_secret_key(), raw.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
        if int(payload.get("x", 0)) < time.time():
            return None
        return payload
    except Exception:
        return None


def _token_for(email: str, kind: str, hours: float) -> str:
    rec = _load_users().get(email)
    return _sign({"e": email, "t": kind, "x": int(time.time() + hours * 3600),
                  "v": (rec or {}).get("hash", "")[:8]})


def _email_from_token(token: str, kind: str) -> Optional[str]:
    payload = _unsign(token)
    if not payload or payload.get("t") != kind:
        return None
    email = payload.get("e", "")
    rec = _load_users().get(email)
    if not rec or rec["hash"][:8] != payload.get("v"):
        return None   # user removed, or password changed since issuing
    return email


def _make_session(email: str) -> str:
    return _token_for(email, "sess", _session_max_age() / 3600)


def current_user(request: Request) -> Optional[str]:
    token = request.cookies.get(COOKIE_NAME)
    return _email_from_token(token, "sess") if token else None


def _require_admin(request: Request) -> str:
    email = current_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if (_load_users().get(email) or {}).get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return email


def _admin_count(users: dict) -> int:
    return sum(1 for r in users.values() if r.get("role") == "admin")


# ──────────────────────────────────────────────
# Reset / invite emails — sent with the dashboard's own Gmail (SMTP_GMAIL +
# SMTP_APP_PASSWORD in neon.env). Reuses the campaign mailer's IPv4-forced
# SMTP class so it works on macOS too.
# ──────────────────────────────────────────────
_AUTH_EMAIL_HTML = """\
<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#eef1f6;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef1f6;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border:1px solid #e6e9f0;border-radius:12px;overflow:hidden;">
<tr><td style="background:#1F2D59;padding:26px 36px;">
  <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:0.5px;">HopCharge</div>
  <div style="color:#9FB0D8;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin-top:6px;">HR Dashboard</div>
</td></tr>
<tr><td style="padding:30px 36px;">
  <div style="color:#1F2D59;font-size:19px;font-weight:700;margin-bottom:14px;">{title}</div>
  <p style="margin:0 0 20px;font-size:14.5px;line-height:1.7;color:#5b6472;">{body}</p>
  <div style="margin:0 0 20px;">
    <a href="{link}" style="display:inline-block;background:#2F5BEA;color:#ffffff;font-size:14px;font-weight:700;text-decoration:none;padding:13px 30px;border-radius:26px;">{button}</a>
  </div>
  <p style="margin:0 0 6px;font-size:12px;line-height:1.6;color:#9aa3b6;">Button not working? Copy this link into your browser:<br>
  <a href="{link}" style="color:#2F5BEA;word-break:break-all;">{link}</a></p>
  <p style="margin:14px 0 0;font-size:12px;line-height:1.6;color:#9aa3b6;">{footer}</p>
</td></tr>
</table>
</td></tr></table>
</body></html>
"""


def _send_auth_email(to: str, subject: str, title: str, body: str,
                     button: str, link: str, footer: str) -> None:
    """Raises on failure — callers decide whether that is fatal."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from emailer import SMTP_IPv4

    gmail = os.environ["SMTP_GMAIL"].strip()
    app_password = os.environ["SMTP_APP_PASSWORD"].strip().replace(" ", "")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"HopCharge HR Dashboard <{gmail}>"
    msg["To"] = to
    msg.attach(MIMEText(f"{title}\n\n{body}\n\n{button}: {link}\n\n{footer}\n", "plain"))
    msg.attach(MIMEText(_AUTH_EMAIL_HTML.format(
        title=title, body=body, button=button, link=link, footer=footer), "html"))

    context = ssl.create_default_context()
    with SMTP_IPv4("smtp.gmail.com", 587, timeout=15) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(gmail, app_password)
        server.sendmail(gmail, [to], msg.as_string())
    logger.info("Auth: sent %r email to %s", subject, to)


def _send_reset_email(request: Request, email: str, invite: bool) -> str:
    """Create a reset/invite link; email it if SMTP is configured.
    Returns the link so admin flows can also display it for copying."""
    hours = INVITE_LINK_HOURS if invite else RESET_LINK_HOURS
    link = f"{_base_url(request)}/reset?token={quote(_token_for(email, 'reset', hours), safe='')}"
    if _smtp_configured():
        if invite:
            _send_auth_email(
                email, "You've been added to the HopCharge HR Dashboard",
                "Welcome aboard!",
                "An administrator has given you access to the HopCharge HR dashboard. "
                "Click below to choose your password and sign in.",
                "Set my password", link,
                f"This link is valid for {INVITE_LINK_HOURS} hours. "
                "If you weren't expecting this, you can ignore this email.")
        else:
            _send_auth_email(
                email, "Reset your HopCharge dashboard password",
                "Password reset",
                "We received a request to reset the password for this account. "
                "Click below to choose a new one.",
                "Reset my password", link,
                f"This link is valid for {RESET_LINK_HOURS} hour(s). "
                "If you didn't request this, you can safely ignore this email — "
                "your password will not change.")
    return link


# ──────────────────────────────────────────────
# Rate limiting (in-memory, per client IP)
# ──────────────────────────────────────────────
_fails: dict = {}          # ip -> [timestamps of failed logins]
_forgots: dict = {}        # ip -> [timestamps of forgot-password requests]
_FAIL_LIMIT, _FAIL_WINDOW = 8, 600
_FORGOT_LIMIT, _FORGOT_WINDOW = 5, 600


def _hit(bucket: dict, ip: str, limit: int, window: int, record: bool) -> bool:
    """True if this IP is over the limit. Optionally record a new hit."""
    now = time.time()
    hits = [t for t in bucket.get(ip, []) if now - t < window]
    if record:
        hits.append(now)
    bucket[ip] = hits
    return len(hits) > limit if record else len(hits) >= limit


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ──────────────────────────────────────────────
# Middleware — registered from app.py; a no-op while auth is off.
# ──────────────────────────────────────────────
def _is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


async def auth_middleware(request: Request, call_next):
    if not auth_enabled():
        return await call_next(request)

    path = request.url.path
    # CORS preflights carry no cookies; blocking them would break the public
    # status-portal widget on the company website.
    if request.method == "OPTIONS" or _is_public(path):
        return await call_next(request)

    if current_user(request):
        return await call_next(request)

    # Browser page navigation → send to the login page and come back after.
    if "text/html" in request.headers.get("accept", ""):
        nxt = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=302)
    return JSONResponse(status_code=401, content={"detail": "Not authenticated"})


# ──────────────────────────────────────────────
# Routes — sign in / out
# ──────────────────────────────────────────────
router = APIRouter(tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


class EmailBody(BaseModel):
    email: str


class AddUserBody(BaseModel):
    email: str
    role: str = "user"


class ResetBody(BaseModel):
    token: str
    new_password: str


@router.get("/login", include_in_schema=False)
async def login_page(request: Request):
    if not auth_enabled() or current_user(request):
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(STATIC_DIR / "login.html"))


@router.post("/api/login")
async def api_login(body: LoginBody, request: Request):
    if not auth_enabled():
        # Nothing to do when auth is off; behave as already signed in.
        return JSONResponse({"success": True, "note": "auth disabled"})

    ip = _client_ip(request)
    if _hit(_fails, ip, _FAIL_LIMIT, _FAIL_WINDOW, record=False):
        raise HTTPException(status_code=429,
                            detail="Too many failed attempts. Try again in a few minutes.")

    _bootstrap_default_admin()
    email = body.email.strip().lower()
    if not verify_user(email, body.password):
        _hit(_fails, ip, _FAIL_LIMIT, _FAIL_WINDOW, record=True)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    resp = JSONResponse({"success": True})
    resp.set_cookie(
        COOKIE_NAME, _make_session(email),
        max_age=_session_max_age(), httponly=True, samesite="lax", path="/",
        secure=(request.url.scheme == "https"
                or request.headers.get("x-forwarded-proto", "") == "https"),
    )
    logger.info("Auth: %s signed in", email)
    return resp


@router.get("/logout", include_in_schema=False)
async def logout_redirect():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"success": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/api/auth/me")
async def api_me(request: Request):
    if not auth_enabled():
        return {"auth": "disabled"}
    email = current_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    rec = _load_users().get(email, {})
    return {"email": email, "role": rec.get("role", "user")}


@router.post("/api/auth/change-password")
async def api_change_password(body: ChangePasswordBody, request: Request):
    email = current_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not verify_user(email, body.old_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400,
                            detail="New password must be at least 8 characters.")
    set_user_password(email, body.new_password)
    resp = JSONResponse({"success": True, "note": "Password changed. Please sign in again."})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# ──────────────────────────────────────────────
# Routes — forgot / reset password (public)
# ──────────────────────────────────────────────
@router.get("/reset", include_in_schema=False)
async def reset_page():
    if not auth_enabled():
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(STATIC_DIR / "reset.html"))


@router.post("/api/auth/forgot")
async def api_forgot(body: EmailBody, request: Request):
    if not auth_enabled():
        return {"success": True, "note": "auth disabled"}
    if not _smtp_configured():
        raise HTTPException(
            status_code=503,
            detail="Password reset by email isn't set up on this server. "
                   "Please ask your administrator to reset your password.")
    ip = _client_ip(request)
    if _hit(_forgots, ip, _FORGOT_LIMIT, _FORGOT_WINDOW, record=True):
        raise HTTPException(status_code=429,
                            detail="Too many reset requests. Try again in a few minutes.")
    email = body.email.strip().lower()
    if email in _load_users():
        try:
            _send_reset_email(request, email, invite=False)
        except Exception:
            logger.exception("Auth: failed to send reset email to %s", email)
            raise HTTPException(status_code=502,
                                detail="Could not send the email. Please try again later.")
    # Same answer whether or not the account exists (no enumeration).
    return {"success": True,
            "note": "If that email has an account, a reset link is on its way."}


@router.post("/api/auth/reset")
async def api_reset(body: ResetBody):
    if not auth_enabled():
        return {"success": True, "note": "auth disabled"}
    email = _email_from_token(body.token.strip(), "reset")
    if not email:
        raise HTTPException(status_code=400,
                            detail="This link is invalid or has expired. Request a new one.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400,
                            detail="Password must be at least 8 characters.")
    set_user_password(email, body.new_password)
    logger.info("Auth: password reset completed for %s", email)
    return {"success": True, "note": "Password set. You can sign in now."}


# ──────────────────────────────────────────────
# Routes — user management (admins only; open /users on the dashboard)
# ──────────────────────────────────────────────
@router.get("/users", include_in_schema=False)
async def users_page(request: Request):
    if not auth_enabled():
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(STATIC_DIR / "users.html"))


@router.get("/api/auth/users")
async def api_users_list(request: Request):
    you = _require_admin(request)
    users = _load_users()
    return {
        "users": [{"email": e, "role": r.get("role", "user"),
                   "created": r.get("created", "")}
                  for e, r in sorted(users.items())],
        "you": you,
        "sync": "db" if _sync_enabled() else "file",
        "smtp_configured": _smtp_configured(),
    }


@router.post("/api/auth/users")
async def api_users_add(body: AddUserBody, request: Request):
    _require_admin(request)
    email = body.email.strip().lower()
    role = body.role.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="That doesn't look like a valid email.")
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'admin'.")

    users = _load_users()
    if email in users:
        # Existing account → this is a role change; never touches their password.
        if (users[email].get("role") == "admin" and role != "admin"
                and _admin_count(users) <= 1):
            raise HTTPException(status_code=400,
                                detail="Cannot demote the last remaining admin.")
        set_user_role(email, role)
        return {"success": True, "created": False, "role_updated": True, "email": email}

    # New account: random placeholder password, then an invite link so the
    # person chooses their own. The link is returned for copying either way.
    set_user_password(email, _secrets.token_urlsafe(24), role=role)
    try:
        link = _send_reset_email(request, email, invite=True)
        emailed = _smtp_configured()
    except Exception:
        logger.exception("Auth: invite email to %s failed", email)
        link = _send_reset_email_link_only(request, email)
        emailed = False
    return {"success": True, "created": True, "email": email,
            "email_sent": emailed, "invite_link": link}


def _send_reset_email_link_only(request: Request, email: str) -> str:
    return (f"{_base_url(request)}/reset?token="
            f"{quote(_token_for(email, 'reset', INVITE_LINK_HOURS), safe='')}")


@router.post("/api/auth/users/reset-link")
async def api_users_reset_link(body: EmailBody, request: Request):
    _require_admin(request)
    email = body.email.strip().lower()
    if email not in _load_users():
        raise HTTPException(status_code=404, detail="No such user.")
    try:
        link = _send_reset_email(request, email, invite=False)
        emailed = _smtp_configured()
    except Exception:
        logger.exception("Auth: reset email to %s failed", email)
        link = _send_reset_email_link_only(request, email)
        emailed = False
    return {"success": True, "email": email, "email_sent": emailed, "reset_link": link}


@router.post("/api/auth/users/delete")
async def api_users_delete(body: EmailBody, request: Request):
    you = _require_admin(request)
    email = body.email.strip().lower()
    users = _load_users()
    if email not in users:
        raise HTTPException(status_code=404, detail="No such user.")
    if email == you:
        raise HTTPException(status_code=400,
                            detail="You cannot remove your own account while signed in.")
    if users[email].get("role") == "admin" and _admin_count(users) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last admin.")
    delete_user(email)
    logger.info("Auth: %s removed user %s", you, email)
    return {"success": True, "removed": email}


# Create the first admin at import time too, so the login page works on the
# very first request after enabling auth (harmless no-op when disabled).
if auth_enabled():
    try:
        _bootstrap_default_admin()
    except Exception:
        logger.exception("Auth: could not bootstrap the default admin user")


# ──────────────────────────────────────────────
# CLI — manage users without the server running
# ──────────────────────────────────────────────
def _cli() -> None:
    import getpass
    import sys

    usage = ("usage:\n"
             "  python3 auth.py list\n"
             "  python3 auth.py add <email> [admin]\n"
             "  python3 auth.py set-password <email>\n"
             "  python3 auth.py remove <email>\n")
    args = sys.argv[1:]
    if not args:
        print(usage)
        return
    cmd = args[0]

    if cmd == "list":
        print(f"(accounts stored in: {'shared database' if _sync_enabled() else USERS_FILE})")
        users = _load_users()
        if not users:
            print("No users yet. (First enabled start creates admin@hopcharge.com.)")
        for email, rec in sorted(users.items()):
            print(f"  {email:40s} role={rec.get('role','user')} created={rec.get('created','?')}")
    elif cmd in ("add", "set-password") and len(args) >= 2:
        email = args[1].strip().lower()
        role = "admin" if (len(args) > 2 and args[2] == "admin") else "user"
        pw = getpass.getpass(f"New password for {email}: ")
        if len(pw) < 8:
            print("Password must be at least 8 characters.")
            return
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            return
        set_user_password(email, pw, role=role)
        where = "shared database" if _sync_enabled() else str(USERS_FILE)
        print(f"OK — password set for {email} (stored in {where})")
    elif cmd == "remove" and len(args) == 2:
        email = args[1].strip().lower()
        if not delete_user(email):
            print(f"No such user: {email}")
            return
        print(f"OK — removed {email}")
    else:
        print(usage)


if __name__ == "__main__":
    _cli()
