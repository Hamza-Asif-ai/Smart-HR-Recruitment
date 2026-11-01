"""Phase 5: Google OAuth token handling."""

from __future__ import annotations

import json
import time

import pytest

from src import google_oauth
from src.google_oauth import (
    GoogleOAuth,
    OAuthConfig,
    OAuthNotConfigured,
    OAuthToken,
    OAuthTokenError,
    build_authorization_url,
    exchange_code,
    load_token,
    refresh_token,
    save_token,
    userinfo,
)


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _config(tmp_path=None) -> OAuthConfig:
    return OAuthConfig(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://localhost:8080/oauth2callback",
        token_file=str(tmp_path / "token.json") if tmp_path else "",
    )


def test_authorization_url_requires_credentials():
    with pytest.raises(OAuthNotConfigured):
        build_authorization_url(OAuthConfig(
            client_id="",
            client_secret="",
            redirect_uri="http://localhost:8080/oauth2callback",
        ))


def test_exchange_code_posts_verifier_and_returns_token():
    session = FakeSession(FakeResponse(200, {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "scope": "gmail.readonly",
        "token_type": "Bearer",
    }))
    token = exchange_code("code-1", _config(), code_verifier="verifier", session=session)
    assert token.access_token == "access-1"
    assert token.refresh_token == "refresh-1"
    assert token.expired is False
    url, kwargs = session.calls[0]
    assert kwargs["data"]["code"] == "code-1"
    assert kwargs["data"]["code_verifier"] == "verifier"


def test_exchange_code_maps_rejection_to_safe_error():
    session = FakeSession(FakeResponse(400, {"error_description": "bad code"}))
    with pytest.raises(OAuthTokenError, match="bad code"):
        exchange_code("code-1", _config(), session=session)


def test_refresh_requires_a_refresh_token():
    with pytest.raises(OAuthTokenError, match="No refresh token"):
        refresh_token(OAuthToken(access_token="", refresh_token=""), _config())


def test_refresh_keeps_existing_refresh_token_when_omitted():
    session = FakeSession(FakeResponse(200, {"access_token": "access-2", "expires_in": 60}))
    token = refresh_token(
        OAuthToken(access_token="old", refresh_token="refresh-1"), _config(), session=session
    )
    assert token.access_token == "access-2"
    assert token.refresh_token == "refresh-1"


def test_save_and_load_token_roundtrip(tmp_path):
    path = tmp_path / "nested" / "token.json"
    token = OAuthToken(
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at=time.time() + 3600,
        scope="scope-1",
    )
    assert save_token(token, str(path)) == str(path)
    loaded = load_token(str(path))
    assert loaded is not None
    assert loaded.access_token == "access-1"
    assert loaded.refresh_token == "refresh-1"


def test_load_token_rejects_invalid_json(tmp_path):
    path = tmp_path / "token.json"
    path.write_text("{bad", encoding="utf-8")
    assert load_token(str(path)) is None
    path.write_text(json.dumps("not a dict"), encoding="utf-8")
    assert load_token(str(path)) is None


def test_google_oauth_reload_reads_new_token_from_disk(tmp_path):
    cfg = _config(tmp_path)
    save_token(OAuthToken(access_token="one", refresh_token="refresh",
                          expires_at=time.time() + 3600), cfg.token_file)
    oauth = GoogleOAuth(cfg)
    assert oauth.access_token() == "one"

    save_token(OAuthToken(access_token="two", refresh_token="refresh",
                          expires_at=time.time() + 3600), cfg.token_file)
    assert oauth.access_token() == "one"
    oauth.reload()
    assert oauth.access_token() == "two"


def test_google_oauth_accepts_env_access_token(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", "env-access")
    oauth = GoogleOAuth(OAuthConfig(client_id="", client_secret="", redirect_uri=""))
    assert oauth.configured is True
    assert oauth.access_token() == "env-access"


def test_google_oauth_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
    oauth = GoogleOAuth(_config(tmp_path))
    with pytest.raises(OAuthNotConfigured):
        oauth.access_token()
    assert oauth.status()["token_present"] is False


def test_google_oauth_refreshes_an_expired_token(tmp_path):
    cfg = _config(tmp_path)
    session = FakeSession(FakeResponse(200, {"access_token": "fresh", "expires_in": 3600}))
    oauth = GoogleOAuth(cfg, session=session)
    oauth._token = OAuthToken(access_token="stale", refresh_token="refresh-1", expires_at=0)
    assert oauth.access_token() == "fresh"
    assert load_token(cfg.token_file).access_token == "fresh"


def test_userinfo_returns_profile_without_leaking_token():
    session = FakeSession(FakeResponse(200, {"email": "hr@example.com", "name": "HR"}))
    info = userinfo("access-1", session=session)
    assert info["email"] == "hr@example.com"
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer access-1"


def test_token_serialization_excludes_nothing_needed_for_refresh():
    token = OAuthToken(access_token="a", refresh_token="r", expires_at=123.0)
    payload = json.loads(token.to_json())
    assert payload["refresh_token"] == "r"
    assert payload["expires_at"] == 123.0
    assert google_oauth.OAuthToken(**payload).access_token == "a"
