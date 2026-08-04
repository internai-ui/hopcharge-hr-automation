"""
gmail_oauth.py — Gmail OAuth 2.0 "Connect Gmail" integration.

Replaces (optionally — App Password stays as a fallback, see emailer.py and
app.py's /api/send-emails) raw SMTP + Gmail App Password with a real OAuth
user-consent flow: the HR user clicks "Connect Gmail", goes through Google's
own consent screen, and this app is granted a refresh token scoped to
gmail.send + gmail.readonly. No password is ever typed into this app.

Mirrors scoring/config_store.py's pattern exactly:
  • Persist config to output/gmail_oauth.json
  • Encrypt the OAuth client secret + refresh/access tokens at rest with a
    machine-local Fernet key (output/.gmail_oauth_secret.key, chmod 600) —
    a separate key from the AI-settings one, so the two secrets don't share
    a blast radius.
  • NEVER return a decrypted secret through any API — only internal getters
    used by this module itself read them back.

Two OAuth client configs, not one
----------------------------------
Google's own guidance: a packaged desktop app is a "public" (non-confidential)
client and should use a **Desktop app**-type OAuth client, while a real
server deployment uses a **Web application**-type client with a genuinely
confidential secret. This module stores BOTH configs (namespaced under
"web" / "desktop" in the JSON) and picks one automatically via
app_paths._is_frozen():
  • Frozen (packaged desktop build)     → "desktop" client (Google-issued
    secret is not treated as confidential; PKCE — already the default in
    google-auth-oauthlib's Flow — is the real protection here). Desktop-type
    clients don't need a pre-registered redirect URI in Cloud Console;
    Google auto-allows any http://127.0.0.1:<port>/... loopback address.
  • Script / hosted server (not frozen) → "web" client with a real secret
    and a pre-registered https://<domain>/api/gmail-oauth/callback redirect.
Tokens are namespaced the same way so a refresh token issued under one
client is never replayed against the other's credentials.

Scopes are deliberately narrow: gmail.send + gmail.readonly only. There is
NO gmail.modify — this app never archives, labels, or marks anything read
in the user's actual Gmail account. "Marking a reply handled" (see
email_replies.py) is purely an in-app database flag.

Known limitation (Google policy, not something to engineer around): while
the Google Cloud OAuth consent screen stays in "Testing" publishing status,
granted refresh tokens expire after ~7 days, requiring a one-click
reconnect. public_status() is designed so a lapsed connection is an
obvious, easily-fixed state in the UI rather than a silent failure.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from pydantic import BaseModel

from config import OUTPUT_DIR
from app_paths import _is_frozen

logger = logging.getLogger("volt_cv.gmail_oauth")

CONFIG_FILE: Path = OUTPUT_DIR / "gmail_oauth.json"
SECRET_KEY_FILE: Path = OUTPUT_DIR / ".gmail_oauth_secret.key"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"


def _client_mode() -> str:
    """Which OAuth client config this running instance should use.
    "desktop" for a packaged PyInstaller build, "web" for everything else
    (dev script or a real hosted server) — matches app_paths._is_frozen(),
    the same signal the rest of the codebase already uses to tell the two
    deployment targets apart."""
    return "desktop" if _is_frozen() else "web"


class GmailNotConnectedError(RuntimeError):
    """Raised by get_credentials()/get_gmail_service() when there's no usable token."""


# ──────────────────────────────────────────────
# Encryption key handling (own key, separate from scoring/config_store.py's)
# ──────────────────────────────────────────────

def _get_fernet() -> Fernet:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_KEY_FILE.exists():
        key = Fernet.generate_key()
        SECRET_KEY_FILE.write_bytes(key)
        try:
            os.chmod(SECRET_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600
        except OSError:
            pass  # best-effort on platforms that don't support chmod
        logger.info("Generated new Gmail OAuth secret key")
    return Fernet(SECRET_KEY_FILE.read_bytes())


def _encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


# ──────────────────────────────────────────────
# Config persistence
# ──────────────────────────────────────────────

def _read() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read gmail_oauth config: %s", exc)
        return {}


def _write(cfg: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    tmp.replace(CONFIG_FILE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mode_cfg(cfg: dict, mode: Optional[str] = None) -> dict:
    """The sub-dict for one client mode ("web"/"desktop"), created on demand."""
    mode = mode or _client_mode()
    return cfg.setdefault(mode, {})


def save_client_config(client_id: str, client_secret: str, mode: Optional[str] = None) -> dict:
    """Persist the OAuth Client ID/Secret the user created in Google Cloud
    Console for the CURRENT deployment mode (web or desktop — see
    _client_mode()). Does not touch any existing token, and does not touch
    the other mode's client config — just this instance's app-level client
    credentials needed to run the OAuth dance at all."""
    client_id = (client_id or "").strip()
    client_secret = (client_secret or "").strip()
    if not client_id or not client_secret:
        raise ValueError("Both Client ID and Client Secret are required.")
    mode = mode or _client_mode()
    cfg = _read()
    mcfg = _mode_cfg(cfg, mode)
    mcfg["client_id"] = client_id
    mcfg["client_secret_encrypted"] = _encrypt(client_secret)
    mcfg["client_configured_at"] = _now_iso()
    _write(cfg)
    logger.info("Gmail OAuth client config saved (mode=%s, client_id=%s...)", mode, client_id[:12])
    return public_status()


def get_decrypted_client_secret(mode: Optional[str] = None) -> Optional[str]:
    cfg = _read()
    token = _mode_cfg(cfg, mode).get("client_secret_encrypted")
    if not token:
        return None
    try:
        return _decrypt(token)
    except InvalidToken:
        logger.error("Stored Gmail client secret could not be decrypted (secret key changed?).")
        return None


def get_decrypted_refresh_token(mode: Optional[str] = None) -> Optional[str]:
    cfg = _read()
    token = _mode_cfg(cfg, mode).get("refresh_token_encrypted")
    if not token:
        return None
    try:
        return _decrypt(token)
    except InvalidToken:
        logger.error("Stored Gmail refresh token could not be decrypted (secret key changed?).")
        return None


def public_status() -> dict:
    """Frontend-safe view for the CURRENT deployment mode. NEVER includes
    client_secret or any token."""
    mode = _client_mode()
    cfg = _read()
    mcfg = _mode_cfg(cfg, mode)
    return {
        "deployment_mode": mode,
        "client_configured": bool(mcfg.get("client_id") and mcfg.get("client_secret_encrypted")),
        "client_id": mcfg.get("client_id"),
        "connected": bool(mcfg.get("refresh_token_encrypted")),
        "connected_email": mcfg.get("connected_email"),
        "connected_at": mcfg.get("connected_at"),
        "scopes": mcfg.get("granted_scopes", []),
    }


def is_connected() -> bool:
    return public_status()["connected"]


# ──────────────────────────────────────────────
# CSRF state for the authorize/callback round trip
#
# Simple in-memory store (this app is a single process). A signed cookie or
# DB row would also work, but a short-lived dict matches the scale of
# everything else here (auth.py's own rate-limit buckets are similarly
# in-memory) and the window between authorize and callback is seconds.
# ──────────────────────────────────────────────

_pending_states: dict[str, float] = {}
_STATE_TTL = 600  # seconds


def _cleanup_states() -> None:
    now = time.time()
    for k in [k for k, exp in _pending_states.items() if exp < now]:
        _pending_states.pop(k, None)


def _new_state() -> str:
    _cleanup_states()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = time.time() + _STATE_TTL
    return state


def _consume_state(state: str) -> bool:
    _cleanup_states()
    return _pending_states.pop(state, None) is not None


# ──────────────────────────────────────────────
# OAuth flow
# ──────────────────────────────────────────────

def _redirect_uri(request: Request) -> str:
    """Same trust-boundary logic as auth.py's _base_url(): DASHBOARD_BASE_URL
    wins (can't be poisoned by a spoofed Host header), else derive from the
    request (fine behind our own nginx). This one function is what makes the
    desktop app (fixed 127.0.0.1:8000, see launcher.py) and the hosted
    server (real HTTPS domain) both work from the same code path."""
    env = os.environ.get("DASHBOARD_BASE_URL", "").strip().rstrip("/")
    if env:
        base = env
    else:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("host") or request.url.netloc
        base = f"{proto}://{host}"
    return f"{base}/api/gmail-oauth/callback"


def build_flow(request: Request) -> Flow:
    mode = _client_mode()
    cfg = _read()
    client_id = _mode_cfg(cfg, mode).get("client_id")
    client_secret = get_decrypted_client_secret(mode)
    if not client_id or not client_secret:
        raise GmailNotConnectedError(
            "Gmail OAuth Client ID/Secret not configured yet. "
            "Paste them into Gmail OAuth settings first."
        )
    redirect_uri = _redirect_uri(request)
    # Google's own client-secrets JSON uses "web" for Web application clients
    # and "installed" for Desktop app clients; google-auth-oauthlib's Flow
    # understands both natively and applies PKCE (autogenerate_code_verifier,
    # on by default) either way — real protection for the desktop path,
    # where the client_secret is not confidential.
    top_key = "installed" if mode == "desktop" else "web"
    client_config = {
        top_key: {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [redirect_uri],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)


def get_credentials() -> Credentials:
    """INTERNAL — builds a live, freshly-refreshed Credentials object from
    the stored refresh token. Always refreshes rather than caching the last
    access token: simpler and avoids subtle "stale token" bugs, and Google's
    token endpoint easily absorbs this app's call volume (a handful of sends
    /polls every few minutes, nowhere near any rate limit)."""
    mode = _client_mode()
    cfg = _read()
    mcfg = _mode_cfg(cfg, mode)
    refresh_token = get_decrypted_refresh_token(mode)
    client_secret = get_decrypted_client_secret(mode)
    client_id = mcfg.get("client_id")
    if not (refresh_token and client_secret and client_id):
        raise GmailNotConnectedError("Gmail is not connected. Connect it on the Send Emails page first.")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(GoogleAuthRequest())
    return creds


def get_gmail_service():
    """Same build() shape as forms_retriever.build_service()/drive_sync.py's
    _build_drive() — just a different API name and a user-OAuth Credentials
    object instead of a service account."""
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


def disconnect() -> dict:
    """Best-effort revoke on Google's side, then clear the CURRENT mode's
    token (but keep client_id/secret so reconnecting doesn't require
    re-pasting them). Never touches the other mode's connection."""
    mode = _client_mode()
    refresh_token = get_decrypted_refresh_token(mode)
    if refresh_token:
        try:
            data = urllib.parse.urlencode({"token": refresh_token}).encode()
            req = urllib.request.Request(REVOKE_URI, data=data, method="POST")
            urllib.request.urlopen(req, timeout=10).close()
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("Gmail token revoke call failed (clearing locally anyway): %s", exc)

    cfg = _read()
    mcfg = _mode_cfg(cfg, mode)
    for k in ("refresh_token_encrypted", "access_token_encrypted", "connected_email",
              "connected_at", "granted_scopes", "history_id"):
        mcfg.pop(k, None)
    _write(cfg)
    logger.info("Gmail disconnected (mode=%s)", mode)
    return public_status()


# ──────────────────────────────────────────────
# FastAPI router
# ──────────────────────────────────────────────

router = APIRouter(prefix="/api/gmail-oauth", tags=["gmail-oauth"])


class ClientConfigBody(BaseModel):
    client_id: str
    client_secret: str


@router.get("/status")
async def get_status():
    return {"success": True, **public_status()}


@router.post("/client-config")
async def post_client_config(body: ClientConfigBody):
    try:
        status = save_client_config(body.client_id, body.client_secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, **status}


@router.get("/authorize")
async def authorize(request: Request):
    try:
        flow = build_flow(request)
    except GmailNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    state = _new_state()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",           # force a refresh_token every time — important
                                     # since a Testing-mode grant expires in ~7 days
                                     # and the user will reconnect periodically
        include_granted_scopes="true",
        state=state,
    )
    return RedirectResponse(auth_url, status_code=302)


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    base = os.environ.get("DASHBOARD_BASE_URL", "").strip().rstrip("/")
    if not base:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("host") or request.url.netloc
        base = f"{proto}://{host}"

    def _err(reason: str) -> RedirectResponse:
        return RedirectResponse(f"{base}/?gmail=error&reason={urllib.parse.quote(reason)}", status_code=302)

    if error:
        return _err(f"Google denied access: {error}")
    if not code or not state or not _consume_state(state):
        return _err("Invalid or expired authorization attempt. Please try connecting again.")

    try:
        flow = build_flow(request)
        flow.fetch_token(code=code)
        creds = flow.credentials
        if not creds.refresh_token:
            return _err("Google did not grant a refresh token. Please try connecting again.")

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()

        mode = _client_mode()
        cfg = _read()
        mcfg = _mode_cfg(cfg, mode)
        mcfg["refresh_token_encrypted"] = _encrypt(creds.refresh_token)
        mcfg["connected_email"] = profile.get("emailAddress")
        mcfg["history_id"] = profile.get("historyId")
        mcfg["granted_scopes"] = list(creds.scopes or SCOPES)
        mcfg["connected_at"] = _now_iso()
        _write(cfg)
        logger.info("Gmail connected (mode=%s): %s", mode, mcfg["connected_email"])
    except Exception as exc:
        logger.error("Gmail OAuth callback failed: %s", exc, exc_info=True)
        return _err(f"Could not complete Gmail connection: {exc}")

    return RedirectResponse(f"{base}/?gmail=connected", status_code=302)


@router.post("/disconnect")
async def post_disconnect():
    return {"success": True, **disconnect()}
