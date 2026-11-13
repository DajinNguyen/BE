import pytest

from app.core.db import new_session
from app.services import auth_service
from app.services.auth_service import GoogleProfile

PROGRESS = {
    "terms": {
        "per": {
            "status": "mastered",
            "correct_count": 3,
            "wrong_count": 1,
            "last_studied_at": "2026-11-13T09:12:00+09:00",
            "set_id": "basic",
        }
    },
    "set_progress": [{"set_id": "basic", "mastered": 1, "studied": 1}],
    "study_dates": ["2026-11-12", "2026-11-13"],
}


@pytest.fixture
def login(client):
    """login(n): n번 사용자로 로그인한 상태로 바꾼다. login(None)은 로그아웃."""

    def _login(n: int | None):
        client.cookies.clear()
        if n is None:
            return
        with new_session() as session:
            user = auth_service.upsert_user(
                session, GoogleProfile(sub=f"sub-{n}", email=f"u{n}@example.com", name=f"실명{n}")
            )
        client.cookies.set(auth_service.SESSION_COOKIE, auth_service.issue_session(user))

    return _login


# ---------------------------------------------------------------- 학습 기록


def test_term_progress_needs_login(client):
    for res in (
        client.get("/api/me/term-progress"),
        client.put("/api/me/term-progress", json=PROGRESS),
    ):
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "LOGIN_REQUIRED"


def test_term_progress_is_saved_per_user(client, login):
    login(1)
    assert client.get("/api/me/term-progress").json() == {
        "terms": {},
        "set_progress": [],
        "study_dates": [],
    }
    assert client.put("/api/me/term-progress", json=PROGRESS).json() == PROGRESS
    assert client.get("/api/me/term-progress").json() == PROGRESS

    login(2)  # 다른 사용자는 빈 기록
    assert client.get("/api/me/term-progress").json()["terms"] == {}


def test_term_progress_put_replaces_all_and_drops_unknown_terms(client, login):
    login(1)
    client.put("/api/me/term-progress", json=PROGRESS)
    body = {
        "terms": {"없는-용어": PROGRESS["terms"]["per"], "pbr": PROGRESS["terms"]["per"]},
        "set_progress": [],
        "study_dates": ["2026-11-13", "2026-11-13", "2026-11-12"],
    }
    saved = client.put("/api/me/term-progress", json=body).json()
    assert list(saved["terms"]) == ["pbr"]
    assert saved["study_dates"] == ["2026-11-12", "2026-11-13"]


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "done"},
        {"correct_count": -1},
        {"last_studied_at": "어제"},
    ],
)
def test_term_progress_rejects_bad_values(client, login, bad):
    login(1)
    body = {**PROGRESS, "terms": {"per": {**PROGRESS["terms"]["per"], **bad}}}
    res = client.put("/api/me/term-progress", json=body)
    assert res.status_code in (400, 422)
