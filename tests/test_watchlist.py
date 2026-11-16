import pytest

from app.core.db import new_session
from app.services import auth_service
from app.services.auth_service import GoogleProfile


@pytest.fixture
def login(client):
    """login(n): n번 사용자로 로그인한 상태로 바꾼다."""

    def _login(n: int):
        client.cookies.clear()
        with new_session() as session:
            user = auth_service.upsert_user(
                session, GoogleProfile(sub=f"sub-{n}", email=f"u{n}@example.com", name=f"실명{n}")
            )
        client.cookies.set(auth_service.SESSION_COOKIE, auth_service.issue_session(user))

    return _login


def test_watchlist_needs_login(client):
    for res in (
        client.get("/api/me/watchlist"),
        client.put("/api/me/watchlist", json={"stock_codes": ["005930"]}),
    ):
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "LOGIN_REQUIRED"


def test_watchlist_is_saved_per_user(client, login):
    login(1)
    assert client.get("/api/me/watchlist").json() == {"stock_codes": []}
    saved = client.put("/api/me/watchlist", json={"stock_codes": ["000660", "005930"]})
    assert saved.json() == {"stock_codes": ["000660", "005930"]}  # 보낸 순서 그대로
    login(2)
    assert client.get("/api/me/watchlist").json() == {"stock_codes": []}
    login(1)
    assert client.get("/api/me/watchlist").json() == {"stock_codes": ["000660", "005930"]}
    # 전체를 바꿔 저장한다 (빼기 = 뺀 목록을 다시 보내기)
    assert client.put("/api/me/watchlist", json={"stock_codes": ["005930"]}).json() == {
        "stock_codes": ["005930"]
    }
    assert client.put("/api/me/watchlist", json={"stock_codes": []}).json() == {"stock_codes": []}


def test_watchlist_drops_duplicates_and_unknown_companies(client, login):
    login(1)
    body = {"stock_codes": ["005930", "999999", "005930", "000660"]}
    assert client.put("/api/me/watchlist", json=body).json() == {
        "stock_codes": ["005930", "000660"]
    }
    assert (
        client.put("/api/me/watchlist", json={"stock_codes": ["005930"] * 201}).status_code == 422
    )
