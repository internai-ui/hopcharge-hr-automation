"""
auth.py — optional sign-in protection for the dashboard (Google Sign-In).

Design goals (in order):
  1. NOTHING breaks when auth is off (the default). Local/desktop bundles keep
     opening straight into the dashboard exactly as before.
  2. No separate account system: signing in with Google is the ONLY way in,
     and access is restricted to email addresses already registered in the
     Employee Database (employee_db.py) — no passwords, no invite emails, no
     separate user list to keep in sync.
  3. Cookie-based sessions, so the existing frontend (static/index.html) works
     unmodified: the browser attaches the session cookie to every fetch()
     automatically. No Authorization headers to wire into the SPA.

How to turn it on (e.g. on the AWS server):
    In neon.env add:      DASHBOARD_AUTH=on
    (also auto-enables when ENVIRONMENT=production is set)

Google Cloud Console setup (one-time):
    This reuses the SAME OAuth client already configured for "Connect Google
    Account" (gmail_oauth.py) — no second client to create. You only need to
    add one more Authorized redirect URI to that existing Web application
    client:
        https://<your-domain>/api/auth/google/callback
    (alongside the existing .../api/gmail-oauth/callback one). The desktop
    build needs no extra Console setup — Desktop-type OAuth clients auto-
    allow any http://127.0.0.1:<port>/... loopback redirect.
    Requested scopes are identity-only: openid + userinfo.email. No refresh
    token is requested or stored for login — this flow never touches Gmail
    sending or Forms sync, which remain the separate, single shared
    connection made via "Connect Google Account".

Who can sign in:
    Anyone whose Google account email matches the "email" or
    "email_official" field of an employee record in the Employee Database.
    Not in the database → sign-in is rejected with a clear message, even if
    the Google login itself succeeded.

Admin access:
    Determined by the "is_admin" field on that same employee record (a
    checkbox in the Employee Database edit form). Checked fresh on every
    request (not baked into the session), so revoking admin — or removing
    the employee entirely — takes effect on their very next request.

Files (kept OUTSIDE output/, so wipe_all_data.py never deletes them):
    DATA_HOME/auth_secret.key   — HMAC key for session cookies (auto-generated)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets as _secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token
from google_auth_oauthlib.flow import Flow

from app_paths import DATA_HOME, STATIC_DIR

logger = logging.getLogger("volt_cv.auth")


# Load neon.env when used standalone — same pattern as database.py, so
# DASHBOARD_AUTH is honoured everywhere. Inside the app this is a no-op
# because app.py already loaded it.
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
SECRET_FILE = DATA_HOME / "auth_secret.key"
COOKIE_NAME = "hc_session"

# Login-only scopes — identity, nothing else. No gmail.send/readonly, no
# Forms scopes: this flow never sends mail or reads forms, and never
# requests/stores a refresh token (access_type="online" in /authorize).
LOGIN_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]

# Requests that must work WITHOUT a login session (see module docstring).
PUBLIC_EXACT = {
    "/login", "/logout",
    "/api/logout",
    "/api/auth/google/authorize", "/api/auth/google/callback",
    "/api/health",
    "/status",
    "/favicon.ico",
    "/static/login.html",
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


def _session_max_age() -> int:
    try:
        days = float(os.environ.get("SESSION_DAYS", "7"))
    except ValueError:
        days = 7.0
    return int(days * 86400)


def _base_url(request: Request) -> str:
    """Public URL used to build the OAuth redirect/callback URL.
    DASHBOARD_BASE_URL wins so it can't be poisoned by a spoofed Host header;
    otherwise derive it from the request (fine behind our own nginx)."""
    env = os.environ.get("DASHBOARD_BASE_URL", "").strip().rstrip("/")
    if env:
        return env
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


# ──────────────────────────────────────────────
# Secret key (signs session cookies)
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
# Signed session tokens
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


def _make_session(email: str) -> str:
    return _sign({"e": email, "t": "sess", "x": int(time.time() + _session_max_age())})


def current_user(request: Request) -> Optional[str]:
    """The signed-in email, or None. Re-verifies the employee record still
    exists on every call (not just at login) — removing someone from the
    Employee Database revokes their session on their very next request,
    without needing a separate token-invalidation mechanism."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = _unsign(token)
    if not payload or payload.get("t") != "sess":
        return None
    email = payload.get("e", "")
    import employee_db
    if not employee_db.find_employee_by_email(email):
        return None
    return email


def _require_admin(request: Request) -> str:
    email = current_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    import employee_db
    if not employee_db.is_admin_email(email):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return email


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
# Google Sign-In OAuth flow
#
# Reuses the SAME OAuth client already configured for "Connect Google
# Account" (gmail_oauth.py) — see module docstring for the one Console setup
# step this needs. Deliberately separate from gmail_oauth.py's own
# authorize/callback: different scopes (identity-only), a different redirect
# URI, and access_type="online" (no refresh token requested or stored — this
# flow only needs a one-time identity check, not ongoing API access).
# ──────────────────────────────────────────────

_pending_login_states: dict[str, tuple[float, str, str]] = {}
_LOGIN_STATE_TTL = 600  # seconds


def _cleanup_login_states() -> None:
    now = time.time()
    for k in [k for k, (exp, _cv, _nx) in _pending_login_states.items() if exp < now]:
        _pending_login_states.pop(k, None)


def _new_login_state(next_path: str = "") -> tuple[str, str]:
    """Returns (state, code_verifier). Both must survive the redirect round
    trip to Google and back — stored together here, keyed by state, since
    /authorize and /callback are separate requests (mirrors gmail_oauth.py's
    identical PKCE-state pattern; see that module's docstring for why a
    freshly-autogenerated verifier on callback would cause Google to reject
    the exchange with "invalid_grant: Missing code verifier")."""
    _cleanup_login_states()
    state = _secrets.token_urlsafe(24)
    code_verifier = _secrets.token_urlsafe(96)
    _pending_login_states[state] = (time.time() + _LOGIN_STATE_TTL, code_verifier, next_path or "")
    return state, code_verifier


def _consume_login_state(state: str) -> Optional[tuple[str, str]]:
    _cleanup_login_states()
    entry = _pending_login_states.pop(state, None)
    return (entry[1], entry[2]) if entry else None


def _google_redirect_uri(request: Request) -> str:
    return f"{_base_url(request)}/api/auth/google/callback"


def _build_login_flow(request: Request, code_verifier: Optional[str] = None) -> Flow:
    import gmail_oauth

    mode = gmail_oauth.client_mode()
    active = gmail_oauth.get_active_client(mode)
    if not active:
        raise RuntimeError(
            "Google Sign-In isn't set up yet — the OAuth client hasn't been configured. "
            "Set it up via “Connect Google Account” on the Send Emails page first; "
            "sign-in reuses that same client."
        )
    redirect_uri = _google_redirect_uri(request)
    top_key = "installed" if mode == "desktop" else "web"
    client_config = {
        top_key: {
            "client_id": active["client_id"],
            "client_secret": active["client_secret"],
            "auth_uri": gmail_oauth.AUTH_URI,
            "token_uri": gmail_oauth.TOKEN_URI,
            "redirect_uris": [redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config, scopes=LOGIN_SCOPES, redirect_uri=redirect_uri,
        code_verifier=code_verifier, autogenerate_code_verifier=(code_verifier is None),
    )


def _safe_next(path: str) -> str:
    """Only same-site paths are allowed as a post-login redirect target — no
    open redirect via a crafted next= value."""
    if not path or path[0] != "/" or path[:2] == "//":
        return "/"
    return path


# ──────────────────────────────────────────────
# Routes — sign in / out
# ──────────────────────────────────────────────
router = APIRouter(tags=["auth"])


@router.get("/login", include_in_schema=False)
async def login_page(request: Request):
    if not auth_enabled() or current_user(request):
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(STATIC_DIR / "login.html"))


@router.get("/api/auth/google/authorize")
async def google_login_authorize(request: Request, next: str = ""):
    if not auth_enabled():
        return RedirectResponse("/", status_code=302)
    state, code_verifier = _new_login_state(next_path=_safe_next(next))
    try:
        flow = _build_login_flow(request, code_verifier=code_verifier)
    except RuntimeError as exc:
        return RedirectResponse(f"/login?error={quote(str(exc), safe='')}", status_code=302)
    auth_url, _ = flow.authorization_url(
        access_type="online",          # identity only — no refresh token needed or stored
        prompt="select_account",
        state=state,
    )
    return RedirectResponse(auth_url, status_code=302)


@router.get("/api/auth/google/callback")
async def google_login_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    def _err(reason: str) -> RedirectResponse:
        return RedirectResponse(f"/login?error={quote(reason, safe='')}", status_code=302)

    if error:
        return _err("Google sign-in was cancelled or denied.")

    consumed = _consume_login_state(state) if state else None
    code_verifier, next_path = consumed if consumed else (None, "")
    if not code or not code_verifier:
        return _err("Invalid or expired sign-in attempt. Please try again.")

    try:
        import gmail_oauth
        flow = _build_login_flow(request, code_verifier=code_verifier)
        flow.fetch_token(code=code)
        creds = flow.credentials
        if not creds.id_token:
            return _err("Google did not return an identity token. Please try again.")
        active = gmail_oauth.get_active_client(gmail_oauth.client_mode())
        claims = google_id_token.verify_oauth2_token(
            creds.id_token, GoogleAuthRequest(), audience=active["client_id"])
    except Exception:
        logger.exception("Google sign-in failed during token exchange")
        return _err("Could not complete Google sign-in. Please try again.")

    if not claims.get("email_verified"):
        return _err("Your Google account's email is not verified.")
    email = (claims.get("email") or "").strip().lower()

    import employee_db
    if not employee_db.find_employee_by_email(email):
        logger.warning("Google sign-in rejected — %s is not a registered employee", email)
        return _err(f"{email} is not registered in the Employee Database. Contact HR/admin for access.")

    resp = RedirectResponse(_safe_next(next_path), status_code=302)
    resp.set_cookie(
        COOKIE_NAME, _make_session(email),
        max_age=_session_max_age(), httponly=True, samesite="lax", path="/",
        secure=(request.url.scheme == "https"
                or request.headers.get("x-forwarded-proto", "") == "https"),
    )
    logger.info("Auth: %s signed in via Google", email)
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
    import employee_db
    role = "admin" if employee_db.is_admin_email(email) else "user"
    return {"email": email, "role": role}
