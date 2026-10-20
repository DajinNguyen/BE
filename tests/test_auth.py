from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlmodel import select

from app.core.config import get_settings
from app.core.db import new_session
from app.models.user import User
from app.services import auth_service

PROFILE = {
    "sub": "google-123",
    "email": "student@example.com",
    "email_verified": True,
    "name": "김다진",
}


class FakeGoogle:
    """httpx.post/get 대신 쓰는 가짜 Google (토큰 교환 + userinfo)."""

    def __init__(self, profile=None, token_status=200):
        self.profile = profile or PROFILE
        self.token_status = token_status
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append(("token", data))
        request = httpx.Request("POST", url)
        if self.token_status != 200:
            return httpx.Response(
                self.token_status, json={"error": "invalid_grant"}, request=request
            )
        return httpx.Response(200, json={"access_token": "at-1"}, request=request)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("userinfo", headers))
        return httpx.Response(200, json=self.profile, request=httpx.Request("GET", url))


@pytest.fixture
def google(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "client-id")
    monkeypatch.setattr(settings, "google_client_secret", "client-secret")

    def install(fake: FakeGoogle | None = None) -> FakeGoogle:
        fake = fake or FakeGoogle()
        monkeypatch.setattr(auth_service.httpx, "post", fake.post)
        monkeypatch.setattr(auth_service.httpx, "get", fake.get)
        return fake

    return install


def _start_login(client, next_url=None):
    params = {"next": next_url} if next_url else {}
    res = client.get("/api/auth/google", params=params, follow_redirects=False)
    assert res.status_code == 302
    return parse_qs(urlsplit(res.headers["location"]).query)["state"][0]


def _callback(client, **params):
    return client.get("/api/auth/google/callback", params=params, follow_redirects=False)


def test_login_not_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", None)
    res = client.get("/api/auth/google", follow_redirects=False)
    assert res.status_code == 503
    assert res.json()["code"] == "AUTH_NOT_CONFIGURED"


def test_login_redirects_to_google(client, google):
    google()
    res = client.get("/api/auth/google", follow_redirects=False)
    assert res.status_code == 302
    url = urlsplit(res.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == auth_service.GOOGLE_AUTH_URL
    query = parse_qs(url.query)
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/auth/google/callback"]
    assert query["scope"] == ["openid email profile"]
    assert query["state"][0]
    assert auth_service.STATE_COOKIE in res.cookies


def test_full_login_flow(client, google):
    fake = google()
    state = _start_login(client, "http://localhost:5174/companies/005930")
    res = _callback(client, code="auth-code", state=state)

    assert res.status_code == 302
    assert res.headers["location"] == "http://localhost:5174/companies/005930"
    assert auth_service.SESSION_COOKIE in res.cookies
    assert fake.calls[0] == (
        "token",
        {
            "code": "auth-code",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "http://localhost:8000/api/auth/google/callback",
            "grant_type": "authorization_code",
        },
    )

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"email": "student@example.com", "name": "김다진"}


def test_second_login_updates_same_user(client, google):
    google()
    _callback(client, code="c1", state=_start_login(client))
    google(FakeGoogle(profile={**PROFILE, "name": "새 이름"}))
    _callback(client, code="c2", state=_start_login(client))

    with new_session() as session:
        users = session.exec(select(User)).all()
    assert [(u.google_sub, u.name) for u in users] == [("google-123", "새 이름")]


def test_state_mismatch_is_rejected(client, google):
    fake = google()
    _start_login(client, "http://localhost:5174/")
    res = _callback(client, code="auth-code", state="forged")
    assert res.headers["location"] == "http://localhost:5174/?login_error=state_mismatch"
    assert auth_service.SESSION_COOKIE not in res.cookies
    assert fake.calls == []  # Google에 code를 보내지도 않는다


def test_callback_without_state_cookie_is_rejected(client, google):
    google()
    res = _callback(client, code="auth-code", state="anything")
    assert res.headers["location"] == "http://localhost:5173/?login_error=state_missing"


def test_user_cancelled(client, google):
    google()
    state = _start_login(client)
    res = _callback(client, error="access_denied", state=state)
    assert res.headers["location"] == "http://localhost:5173/?login_error=cancelled"


def test_next_to_other_site_falls_back_to_frontend(client, google):
    google()
    state = _start_login(client, "https://evil.example.com/steal")
    res = _callback(client, code="auth-code", state=state)
    assert res.headers["location"] == "http://localhost:5173/"


def test_unverified_email_is_rejected(client, google):
    google(FakeGoogle(profile={**PROFILE, "email_verified": False}))
    res = _callback(client, code="auth-code", state=_start_login(client))
    assert res.headers["location"].endswith("login_error=email_not_verified")
    assert client.get("/api/auth/me").status_code == 401


def test_google_token_error(client, google):
    google(FakeGoogle(token_status=400))
    res = _callback(client, code="bad-code", state=_start_login(client))
    assert res.headers["location"].endswith("login_error=google_error")


def test_me_requires_login(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 401
    assert res.json() == {"detail": "로그인이 필요해요.", "code": "NOT_LOGGED_IN"}

    client.cookies.set(auth_service.SESSION_COOKIE, "not-a-jwt")
    assert client.get("/api/auth/me").status_code == 401


def test_state_token_cannot_be_used_as_session(client, google):
    google()
    _, state_token = auth_service.make_state("http://localhost:5173/")
    client.cookies.set(auth_service.SESSION_COOKIE, state_token)
    assert client.get("/api/auth/me").status_code == 401


def test_logout(client, google):
    google()
    _callback(client, code="auth-code", state=_start_login(client))
    assert client.get("/api/auth/me").status_code == 200

    res = client.post("/api/auth/logout")
    assert res.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
