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
    assert res.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


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
    body = me.json()
    assert body["id"].isdigit()
    assert (body["email"], body["name"]) == ("student@example.com", "김다진")
    assert body["nickname"] and body["nickname"] != "김다진"  # 실명 대신 닉네임


def _start_login_with_path(client, redirect, referer=None):
    headers = {"referer": referer} if referer else {}
    res = client.get(
        "/api/auth/google/login",
        params={"redirect": redirect},
        headers=headers,
        follow_redirects=False,
    )
    assert res.status_code == 302
    return parse_qs(urlsplit(res.headers["location"]).query)["state"][0]


def test_login_with_redirect_path_returns_to_referring_frontend(client, google):
    google()
    state = _start_login_with_path(
        client, "/companies/005930?tab=community", referer="http://localhost:5174/companies/005930"
    )
    res = _callback(client, code="auth-code", state=state)
    assert res.headers["location"] == "http://localhost:5174/companies/005930?tab=community"


def test_login_with_redirect_path_defaults_to_first_frontend(client, google):
    google()
    state = _start_login_with_path(client, "/terms", referer="https://evil.example.com/")
    res = _callback(client, code="auth-code", state=state)
    assert res.headers["location"] == "http://localhost:5173/terms"


@pytest.mark.parametrize(
    "redirect", ["//evil.example.com/x", "https://evil.example.com", "/\\evil"]
)
def test_login_redirect_must_be_our_path(client, google, redirect):
    google()
    state = _start_login_with_path(client, redirect)
    res = _callback(client, code="auth-code", state=state)
    assert res.headers["location"] == "http://localhost:5173/"


def test_nickname_is_stable_and_unique():
    a = User(id=1, google_sub="a", email="a@x", name="가", created_at=0, last_login_at=0)
    b = User(id=2, google_sub="b", email="b@x", name="가", created_at=0, last_login_at=0)
    assert auth_service.nickname_for(a) == auth_service.nickname_for(a)
    assert auth_service.nickname_for(a) != auth_service.nickname_for(b)


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
    assert res.json() == {"error": {"code": "LOGIN_REQUIRED", "message": "로그인이 필요해요."}}

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


def _login_as(client, n):
    with new_session() as session:
        user = auth_service.upsert_user(
            session,
            auth_service.GoogleProfile(sub=f"sub-{n}", email=f"u{n}@example.com", name=f"실명{n}"),
        )
    token = auth_service.issue_session(user)
    client.cookies.clear()
    client.cookies.set(auth_service.SESSION_COOKIE, token)
    return token


def test_logged_out_cookie_cannot_be_used_again(client):
    """로그아웃한 쿠키 값을 따로 적어 뒀다가 다시 보내도 받지 않는다."""
    token = _login_as(client, 1)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204

    client.cookies.set(auth_service.SESSION_COOKIE, token)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/me/term-progress").status_code == 401


def test_logout_keeps_other_logins(client):
    """한 기기에서 로그아웃해도 같은 사람의 다른 로그인, 다시 한 로그인은 그대로다."""
    other_device = _login_as(client, 1)
    _login_as(client, 1)
    client.post("/api/auth/logout")

    client.cookies.set(auth_service.SESSION_COOKIE, other_device)
    assert client.get("/api/auth/me").status_code == 200
    _login_as(client, 1)
    assert client.get("/api/auth/me").status_code == 200


def test_logout_without_login_is_fine(client):
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set(auth_service.SESSION_COOKIE, "not-a-token")
    assert client.post("/api/auth/logout").status_code == 204


def test_each_account_sees_only_its_own_data(client):
    progress = {
        "terms": {
            "per": {
                "status": "mastered",
                "correct_count": 3,
                "wrong_count": 1,
                "last_studied_at": "2026-11-13T09:12:00+09:00",
                "set_id": "basic",
            }
        },
        "set_progress": [],
        "study_dates": [],
    }
    _login_as(client, 1)
    client.put("/api/me/term-progress", json=progress)
    first = client.get("/api/auth/me").json()

    # 같은 브라우저에서 로그아웃하고 다른 계정으로 로그인
    client.post("/api/auth/logout")
    _login_as(client, 2)
    second = client.get("/api/auth/me").json()
    assert second["id"] != first["id"]
    assert client.get("/api/me/term-progress").json()["terms"] == {}

    _login_as(client, 1)
    assert list(client.get("/api/me/term-progress").json()["terms"]) == ["per"]


def test_personal_responses_are_not_cached(client):
    # 사용자별 API 는 로그인하지 않았을 때(401)도 저장하지 않는다
    for res in (client.get("/api/auth/me"), client.get("/api/me/term-progress")):
        assert res.headers["cache-control"] == "no-store"
        assert "Cookie" in res.headers["vary"]

    # 누구에게나 같은 응답은 그대로 둔다
    assert "cache-control" not in client.get("/api/companies/005930").headers

    # 로그인한 채로 부른 API 는 사람마다 다를 수 있어서 저장하지 않는다
    _login_as(client, 1)
    for path in ("/api/auth/me", "/api/me/term-progress", "/api/companies/005930/posts"):
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers["cache-control"] == "no-store"
        assert "Cookie" in res.headers["vary"]


# ---- 마이페이지: 닉네임 바꾸기 ----


def _login(client, n=1):
    from tests.conftest import login_as

    return login_as(client, n)


def test_me_includes_joined_date(client):
    _login(client)
    body = client.get("/api/auth/me").json()
    assert body["joined_at"]
    assert body["nickname"] == body["nickname"].strip() and body["nickname"]


def test_update_nickname_shows_everywhere(client):
    _login(client)
    res = client.patch("/api/auth/me", json={"nickname": "주식왕_1"})
    assert res.status_code == 200
    assert res.json()["nickname"] == "주식왕_1"
    assert client.get("/api/auth/me").json()["nickname"] == "주식왕_1"


def test_update_nickname_requires_login(client):
    assert client.patch("/api/auth/me", json={"nickname": "주식왕"}).status_code == 401


def test_clear_nickname_returns_to_default(client):
    _login(client)
    default = client.get("/api/auth/me").json()["nickname"]
    client.patch("/api/auth/me", json={"nickname": "주식왕"})
    res = client.patch("/api/auth/me", json={"nickname": "  "})
    assert res.json()["nickname"] == default


@pytest.mark.parametrize("nickname", ["가", "a" * 13, "공백 있음", "이모지😀", "<b>x</b>"])
def test_invalid_nickname_is_rejected(client, nickname):
    _login(client)
    res = client.patch("/api/auth/me", json={"nickname": nickname})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PARAM"


def test_nickname_must_be_unique_ignoring_case(client):
    _login(client, 1)
    client.patch("/api/auth/me", json={"nickname": "Dajin"})
    _login(client, 2)
    res = client.patch("/api/auth/me", json={"nickname": "dajin"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NICKNAME_TAKEN"
    _login(client, 1)  # 내 닉네임을 다시 저장하는 건 괜찮다
    assert client.patch("/api/auth/me", json={"nickname": "DAJIN"}).status_code == 200


def test_cannot_impersonate_auto_nickname(client):
    _login(client, 1)
    other = _login(client, 2)
    from app.core.db import new_session
    from app.models.user import User

    with new_session() as s:
        auto = auth_service.default_nickname(s.get(User, other))
    _login(client, 1)
    assert client.patch("/api/auth/me", json={"nickname": auto}).status_code == 400
    _login(client, 2)  # 본인의 자동 닉네임은 그대로 저장할 수 있다
    assert client.patch("/api/auth/me", json={"nickname": auto}).status_code == 200


def test_custom_nickname_appears_in_community(client):
    _login(client)
    client.patch("/api/auth/me", json={"nickname": "주식왕"})
    res = client.post(
        "/api/companies/005930/posts",
        json={"title": "인사", "body": "안녕하세요", "category": "question"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["author"]["nickname"] == "주식왕"


# ---- 약관 동의 · 만 14세 이상 확인 ----


@pytest.fixture
def consent_gate(monkeypatch):
    monkeypatch.setattr(get_settings(), "require_consent", True)


ALL_AGREED = {"terms": True, "privacy": True, "age_14": True}


def test_new_user_must_consent_first(client, consent_gate):
    _login(client)
    assert client.get("/api/auth/me").json()["consent_required"] is True
    res = client.get("/api/me/wallet")  # 서비스 기능은 막힌다
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_consent_unlocks_service(client, consent_gate):
    _login(client)
    res = client.post("/api/auth/consent", json=ALL_AGREED)
    assert res.status_code == 200
    assert res.json()["consent_required"] is False
    assert client.get("/api/me/wallet").status_code == 200


@pytest.mark.parametrize("missing", ["terms", "privacy", "age_14"])
def test_consent_needs_every_checkbox(client, consent_gate, missing):
    _login(client)
    res = client.post("/api/auth/consent", json={**ALL_AGREED, missing: False})
    assert res.status_code == 400
    assert client.get("/api/auth/me").json()["consent_required"] is True


def test_consent_requires_login(client):
    assert client.post("/api/auth/consent", json=ALL_AGREED).status_code == 401


def test_account_pages_work_before_consent(client, consent_gate):
    """동의 전에도 내 정보 확인·로그아웃·탈퇴는 할 수 있다 (동의하기 싫으면 나갈 수 있어야 한다)."""
    _login(client)
    assert client.get("/api/auth/me").status_code == 200
    assert client.delete("/api/auth/me").status_code == 204


def test_new_terms_version_asks_again(client, consent_gate, monkeypatch):
    _login(client)
    client.post("/api/auth/consent", json=ALL_AGREED)
    monkeypatch.setattr(get_settings(), "terms_version", "2027-01-01")
    assert client.get("/api/auth/me").json()["consent_required"] is True
    assert client.get("/api/me/wallet").status_code == 403
