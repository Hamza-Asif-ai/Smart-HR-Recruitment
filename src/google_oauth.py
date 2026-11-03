"""Google OAuth 2.0 helpers for Gmail and Calendar access.

The app never asks for a Gmail password. A recruiter completes a one-time OAuth
consent flow (``python -m src.google_oauth``), and the resulting refresh token is
stored in a git-ignored file or supplied as a deployment secret. Runtime code
only ever exchanges/refreshes short-lived access tokens.

Scopes are deliberately read-only: Gmail messages and Calendar free/busy. There
is no scope that allows the LLM or the screening pipeline to send mail, edit a
calendar, or delete data.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken

from . import config
from .security import scrub_secrets


class OAuthError(RuntimeError):
    """Base class for OAuth failures that are safe to show to the user."""


class OAuthNotConfigured(OAuthError):
    """The OAuth client credentials are missing."""


class OAuthTokenError(OAuthError):
    """The token endpoint rejected or could not refresh a token."""


class OAuthStateError(OAuthError):
    """The OAuth state is missing, expired, or already used."""


class OAuthSessionError(OAuthError):
    """The encrypted server-side session store is unavailable."""


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0
    scope: str = ""
    token_type: str = "Bearer"

    @property
    def expired(self) -> bool:
        return not self.access_token or self.expires_at <= time.time() + 60

    def as_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "token_type": self.token_type,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2)


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    auth_uri: str = config.GOOGLE_OAUTH_AUTH_URI
    token_uri: str = config.GOOGLE_OAUTH_TOKEN_URI
    revoke_uri: str = config.GOOGLE_OAUTH_REVOKE_URI
    scopes: Tuple[str, ...] = config.GOOGLE_OAUTH_SCOPES
    token_file: str = config.GOOGLE_OAUTH_TOKEN_FILE

    @classmethod
    def from_env(cls) -> "OAuthConfig":
        return cls(
            client_id=config.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=config.GOOGLE_OAUTH_CLIENT_SECRET,
            redirect_uri=config.GOOGLE_OAUTH_REDIRECT_URI,
            auth_uri=config.GOOGLE_OAUTH_AUTH_URI,
            token_uri=config.GOOGLE_OAUTH_TOKEN_URI,
            revoke_uri=config.GOOGLE_OAUTH_REVOKE_URI,
            scopes=config.GOOGLE_OAUTH_SCOPES,
            token_file=config.GOOGLE_OAUTH_TOKEN_FILE,
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


def _token_from_payload(payload: Mapping[str, Any], previous_refresh: str = "") -> OAuthToken:
    access = str(payload.get("access_token", "") or "")
    if not access:
        raise OAuthTokenError("Google did not return an access token.")
    expires_in = float(payload.get("expires_in", 0) or 0)
    return OAuthToken(
        access_token=access,
        refresh_token=str(payload.get("refresh_token", "") or previous_refresh),
        expires_at=time.time() + expires_in if expires_in else 0.0,
        scope=str(payload.get("scope", "") or ""),
        token_type=str(payload.get("token_type", "Bearer") or "Bearer"),
    )


def _error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
        return str(payload.get("error_description") or payload.get("error") or "")
    except Exception:
        return ""


def _require_config(config_value: OAuthConfig) -> None:
    if not config_value.configured:
        raise OAuthNotConfigured(
            "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET."
        )


def build_authorization_url(
    config_value: Optional[OAuthConfig] = None,
    *,
    state: Optional[str] = None,
    code_verifier: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Return ``(url, state, verifier)`` for a one-time consent redirect."""
    cfg = config_value or OAuthConfig.from_env()
    _require_config(cfg)
    state = state or secrets.token_urlsafe(24)
    verifier = code_verifier or secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": " ".join(cfg.scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{cfg.auth_uri}?{urlencode(params)}", state, verifier


def exchange_code(
    code: str,
    config_value: Optional[OAuthConfig] = None,
    *,
    code_verifier: str = "",
    session: Optional[requests.Session] = None,
) -> OAuthToken:
    cfg = config_value or OAuthConfig.from_env()
    _require_config(cfg)
    data = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "code": str(code),
        "grant_type": "authorization_code",
        "redirect_uri": cfg.redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    try:
        response = (session or requests).post(cfg.token_uri, data=data, timeout=30)
    except requests.RequestException as exc:
        raise OAuthTokenError(f"Could not reach Google's token endpoint: {type(exc).__name__}") from exc
    if response.status_code != 200:
        detail = _error_detail(response)
        raise OAuthTokenError(f"Google rejected the authorization code ({response.status_code}). {detail}".strip())
    return _token_from_payload(response.json())


def refresh_token(
    token: OAuthToken,
    config_value: Optional[OAuthConfig] = None,
    *,
    session: Optional[requests.Session] = None,
) -> OAuthToken:
    if not token.refresh_token:
        raise OAuthTokenError("No refresh token is available; reconnect Google.")
    cfg = config_value or OAuthConfig.from_env()
    _require_config(cfg)
    data = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "refresh_token": token.refresh_token,
        "grant_type": "refresh_token",
    }
    try:
        response = (session or requests).post(cfg.token_uri, data=data, timeout=30)
    except requests.RequestException as exc:
        raise OAuthTokenError(f"Could not reach Google's token endpoint: {type(exc).__name__}") from exc
    if response.status_code != 200:
        detail = _error_detail(response)
        raise OAuthTokenError(f"Google could not refresh the token ({response.status_code}). {detail}".strip())
    return _token_from_payload(response.json(), previous_refresh=token.refresh_token)


def load_token(path: Optional[str] = None) -> Optional[OAuthToken]:
    token_path = Path(path or config.GOOGLE_OAUTH_TOKEN_FILE)
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return OAuthToken(
            access_token=str(payload.get("access_token", "") or ""),
            refresh_token=str(payload.get("refresh_token", "") or ""),
            expires_at=float(payload.get("expires_at", 0) or 0),
            scope=str(payload.get("scope", "") or ""),
            token_type=str(payload.get("token_type", "Bearer") or "Bearer"),
        )
    except (TypeError, ValueError):
        return None


def save_token(token: OAuthToken, path: Optional[str] = None) -> str:
    token_path = Path(path or config.GOOGLE_OAUTH_TOKEN_FILE)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token.to_json() + "\n", encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    return str(token_path)


def userinfo(access_token: str, *, session: Optional[requests.Session] = None) -> Dict[str, Any]:
    """Return OpenID user info (email/name) for a live access token."""
    try:
        response = (session or requests).get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise OAuthTokenError(f"Could not read Google account info: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise OAuthTokenError("Could not read Google account info.")
    return response.json()


class GoogleOAuth:
    """Thread-safe token provider with refresh and optional on-disk persistence."""

    def __init__(self, config_value: Optional[OAuthConfig] = None,
                 *, session: Optional[requests.Session] = None):
        self.config = config_value or OAuthConfig.from_env()
        self.session = session
        self._lock = threading.Lock()
        self._token: Optional[OAuthToken] = None
        self._env_access_token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "").strip()
        self._env_refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()

    @property
    def configured(self) -> bool:
        return bool(
            (self.config.client_id and self.config.client_secret)
            or self._env_access_token
            or self._env_refresh_token
        )

    def status(self) -> Dict[str, Any]:
        token = self._cached_token()
        return {
            "configured": self.configured,
            "token_present": bool(token and (token.access_token or token.refresh_token)),
            "token_file": self.config.token_file,
        }

    def _cached_token(self) -> Optional[OAuthToken]:
        with self._lock:
            if self._token is None:
                self._token = load_token(self.config.token_file)
            return self._token

    def reload(self) -> None:
        """Drop the in-memory token so the next call reloads it from disk."""
        with self._lock:
            self._token = None

    def _store(self, token: OAuthToken) -> OAuthToken:
        with self._lock:
            self._token = token
        if token.refresh_token and self.config.token_file:
            try:
                save_token(token, self.config.token_file)
            except OSError:
                pass
        return token

    def access_token(self, *, force_refresh: bool = False) -> str:
        """Return a live access token, refreshing it when needed."""
        if self._env_access_token and not force_refresh:
            return self._env_access_token

        with self._lock:
            token = self._token or load_token(self.config.token_file)
            if token is None and self._env_refresh_token:
                token = OAuthToken(
                    access_token="",
                    refresh_token=self._env_refresh_token,
                    expires_at=0.0,
                )
            if token is None:
                raise OAuthNotConfigured(
                    "Gmail is not connected. Run `python -m src.google_oauth` "
                    "or set GOOGLE_OAUTH_REFRESH_TOKEN."
                )
            self._token = token

        if not force_refresh and not token.expired:
            return token.access_token
        refreshed = refresh_token(token, self.config, session=self.session)
        self._store(refreshed)
        return refreshed.access_token


def _fernet_from_secret(secret: str) -> Fernet:
    """Derive a stable 32-byte Fernet key from an arbitrary secret string."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """Check an S256 PKCE verifier against the challenge sent to Google."""
    if not code_verifier or not code_challenge:
        return False
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(expected, code_challenge)


def revoke_token(
    token: OAuthToken,
    config_value: Optional[OAuthConfig] = None,
    *,
    session: Optional[requests.Session] = None,
) -> bool:
    """Revoke a token at Google. Never logs or returns the token itself."""
    target = token.refresh_token or token.access_token
    if not target:
        return False
    cfg = config_value or OAuthConfig.from_env()
    try:
        response = (session or requests).post(
            cfg.revoke_uri, data={"token": target}, timeout=15
        )
    except requests.RequestException:
        return False
    return response.status_code in (200, 204)


class OAuthStateStore:
    """Single-use, short-lived OAuth state values bound to a browser session."""

    def __init__(self, *, ttl_seconds: Optional[int] = None, clock=time.time):
        self.ttl_seconds = int(
            ttl_seconds if ttl_seconds is not None else config.OAUTH_STATE_TTL_SECONDS
        )
        self.clock = clock
        self._lock = threading.Lock()
        self._states: Dict[str, Tuple[str, str, float]] = {}

    def _purge(self) -> None:
        now = self.clock()
        for key in [k for k, (_, _, exp) in self._states.items() if exp <= now]:
            self._states.pop(key, None)

    def issue(self, session_id: str, code_verifier: str) -> str:
        if not session_id or not code_verifier:
            raise OAuthStateError("Cannot start Google sign-in without a session.")
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._states[state] = (
                session_id,
                code_verifier,
                self.clock() + self.ttl_seconds,
            )
        return state

    def consume(self, state: str) -> Tuple[str, str]:
        if not state:
            raise OAuthStateError("Missing OAuth state.")
        with self._lock:
            self._purge()
            record = self._states.pop(state, None)
        if record is None:
            raise OAuthStateError("OAuth state is invalid, expired, or already used.")
        session_id, verifier, _expires = record
        return session_id, verifier


class TokenSessionStore:
    """Fernet-encrypted, in-memory OAuth tokens keyed by a random session id."""

    def __init__(
        self,
        encryption_key: Optional[str] = None,
        *,
        ttl_seconds: Optional[int] = None,
        clock=time.time,
        config_value: Optional[OAuthConfig] = None,
        http_session: Optional[requests.Session] = None,
    ):
        secret = encryption_key if encryption_key is not None else config.TOKEN_ENC_KEY
        self._fernet = _fernet_from_secret(secret) if secret else None
        self.ttl_seconds = int(
            ttl_seconds if ttl_seconds is not None else config.OAUTH_SESSION_TTL_SECONDS
        )
        self.clock = clock
        self.config = config_value or OAuthConfig.from_env()
        self.http_session = http_session
        self._lock = threading.RLock()
        self._sessions: Dict[str, Tuple[bytes, float]] = {}

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def _purge(self) -> None:
        now = self.clock()
        for key in [k for k, (_, exp) in self._sessions.items() if exp <= now]:
            self._sessions.pop(key, None)

    def create(self, token: OAuthToken) -> str:
        if self._fernet is None:
            raise OAuthSessionError("TOKEN_ENC_KEY is not configured.")
        session_id = secrets.token_urlsafe(32)
        payload = self._fernet.encrypt(token.to_json().encode("utf-8"))
        with self._lock:
            self._purge()
            self._sessions[session_id] = (
                payload,
                self.clock() + self.ttl_seconds,
            )
        return session_id

    def update(self, session_id: str, token: OAuthToken) -> None:
        """Store or replace the token for ``session_id`` (used by the callback)."""
        if not session_id or self._fernet is None:
            return
        payload = self._fernet.encrypt(token.to_json().encode("utf-8"))
        with self._lock:
            self._purge()
            self._sessions[session_id] = (
                payload,
                self.clock() + self.ttl_seconds,
            )

    def get(self, session_id: Optional[str]) -> Optional[OAuthToken]:
        if not session_id or self._fernet is None:
            return None
        with self._lock:
            self._purge()
            record = self._sessions.get(session_id)
        if record is None:
            return None
        payload, _expires = record
        try:
            raw = self._fernet.decrypt(payload).decode("utf-8")
            data = json.loads(raw)
            return OAuthToken(
                access_token=str(data.get("access_token", "") or ""),
                refresh_token=str(data.get("refresh_token", "") or ""),
                expires_at=float(data.get("expires_at", 0) or 0),
                scope=str(data.get("scope", "") or ""),
                token_type=str(data.get("token_type", "Bearer") or "Bearer"),
            )
        except (InvalidToken, ValueError, TypeError):
            return None

    def get_access_token(self, session_id: Optional[str]) -> Optional[str]:
        token = self.get(session_id)
        if token is None:
            return None
        if not token.expired:
            return token.access_token
        if not token.refresh_token:
            return None
        try:
            refreshed = refresh_token(token, self.config, session=self.http_session)
        except OAuthError:
            return None
        self.update(session_id, refreshed)
        return refreshed.access_token

    def pop(self, session_id: Optional[str]) -> Optional[OAuthToken]:
        if not session_id:
            return None
        token = self.get(session_id)
        with self._lock:
            self._sessions.pop(session_id, None)
        return token

    def delete(self, session_id: Optional[str]) -> Optional[OAuthToken]:
        return self.pop(session_id)


_DEFAULT_OAUTH: Optional[GoogleOAuth] = None
_DEFAULT_LOCK = threading.Lock()


def default_oauth() -> GoogleOAuth:
    global _DEFAULT_OAUTH
    with _DEFAULT_LOCK:
        if _DEFAULT_OAUTH is None:
            _DEFAULT_OAUTH = GoogleOAuth()
        return _DEFAULT_OAUTH


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "SmartHROAuth/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        error = (params.get("error") or [""])[0]
        code = (params.get("code") or [""])[0]
        state = (params.get("state") or [""])[0]
        if error:
            self.server.result = {"error": error}
        elif not code:
            self.server.result = {"error": "missing_code"}
        else:
            self.server.result = {"code": code, "state": state}
        body = b"Google connection received. You can close this tab."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def run_local_flow(*, open_browser: bool = True, timeout_seconds: int = 300) -> str:
    """Run a localhost OAuth callback, save the token, and return its path."""
    cfg = OAuthConfig.from_env()
    _require_config(cfg)
    parsed = urlparse(cfg.redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise OAuthError(
            "Local OAuth needs an http://localhost redirect URI. "
            "Set GOOGLE_OAUTH_REDIRECT_URI accordingly, or use a deployed callback."
        )
    port = parsed.port or 80
    url, state, verifier = build_authorization_url(cfg)
    server = HTTPServer((parsed.hostname, port), _CallbackHandler)
    server.result = {}
    server.timeout = 1.0
    print("Open this URL and approve Gmail read-only + Calendar free/busy access:")
    print(url)
    if open_browser:
        webbrowser.open(url)
    deadline = time.time() + max(30, int(timeout_seconds))
    try:
        while time.time() < deadline and not server.result:
            server.handle_request()
    finally:
        server.server_close()
    result = server.result or {}
    if result.get("error"):
        raise OAuthError(f"Google returned an error: {result['error']}")
    if result.get("state") != state:
        raise OAuthError("OAuth state did not match; the callback was ignored.")
    token = exchange_code(result["code"], cfg, code_verifier=verifier)
    saved = save_token(token, cfg.token_file)
    print(f"Token saved to {saved} (git-ignored).")
    try:
        info = userinfo(token.access_token)
        email = scrub_secrets(str(info.get("email", "")))
        if email:
            print(f"Connected Google account: {email}")
    except OAuthError:
        pass
    return saved


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Connect Gmail/Calendar with Google OAuth.")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL only.")
    parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait for the callback.")
    args = parser.parse_args()
    try:
        run_local_flow(open_browser=not args.no_browser, timeout_seconds=args.timeout)
    except OAuthError as exc:
        print(f"OAuth failed: {exc}")
        return 1
    return 0


__all__ = [
    "GoogleOAuth",
    "OAuthConfig",
    "OAuthError",
    "OAuthNotConfigured",
    "OAuthSessionError",
    "OAuthStateError",
    "OAuthStateStore",
    "OAuthToken",
    "OAuthTokenError",
    "TokenSessionStore",
    "build_authorization_url",
    "default_oauth",
    "exchange_code",
    "load_token",
    "refresh_token",
    "revoke_token",
    "run_local_flow",
    "save_token",
    "userinfo",
    "verify_pkce",
]


if __name__ == "__main__":
    raise SystemExit(main())
