from datetime import datetime

import pytest

from app.core.db import new_session
from app.services import auth_service, credit_service
from app.services.auth_service import GoogleProfile
from app.services.market_clock import KST

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


# ---------------------------------------------------------------- 낱말퀴즈 보상

PUZZLES = ["basics-easy", "financials-normal", "market-normal"]


def _at(day, hour=9):
    return datetime(2026, 11, day, hour, tzinfo=KST)


@pytest.fixture
def clock(monkeypatch):
    """서버 시각을 정한다: clock(13) → 11월 13일."""
    now = {"value": _at(13)}
    real = credit_service._today
    monkeypatch.setattr(credit_service, "_today", lambda _=None: real(now["value"]))
    return lambda day: now.update(value=_at(day))


def _complete(client, puzzle_id, minute=0, revealed=False):
    body = {
        "puzzle_id": puzzle_id,
        "completed_at": f"2026-11-13T09:{minute:02d}:00+09:00",
        "revealed": revealed,
    }
    return client.post("/api/me/puzzle-completions", json=body)


def test_credits_need_login(client):
    assert client.get("/api/me/usage-credits").status_code == 401
    assert _complete(client, PUZZLES[0]).status_code == 401


def test_three_puzzles_give_one_reward(client, login, clock):
    login(1)
    assert client.get("/api/me/usage-credits").json() == {
        "date": "2026-11-13",
        "rewards_today": 0,
        "progress": 0,
        "counted_today": [],
        "completions": [],
    }
    assert _complete(client, PUZZLES[0], 1).json()["progress"] == 1
    assert _complete(client, PUZZLES[1], 2).json()["progress"] == 2
    body = _complete(client, PUZZLES[2], 3).json()
    assert (body["rewards_today"], body["progress"]) == (1, 0)
    assert body["counted_today"] == PUZZLES
    # 요청에 온 completed_at 을 그대로 돌려준다
    assert body["completions"][0] == {
        "puzzle_id": "basics-easy",
        "completed_at": "2026-11-13T09:01:00+09:00",
        "revealed": False,
    }
    assert client.get("/api/me/usage-credits").json() == body


def test_revealed_duplicate_and_resent_are_not_counted(client, login, clock):
    login(1)
    assert _complete(client, PUZZLES[0], 1, revealed=True).json()["progress"] == 0
    assert _complete(client, PUZZLES[0], 2).json()["progress"] == 1
    # 같은 퀴즈는 하루 한 번
    body = _complete(client, PUZZLES[0], 3).json()
    assert body["progress"] == 1 and len(body["completions"]) == 3
    # 실패한 줄 알고 같은 기록을 다시 보내도 한 번만 남는다
    again = _complete(client, PUZZLES[0], 3).json()
    assert again == body


def test_unknown_puzzle_is_404(client, login, clock):
    login(1)
    res = _complete(client, "없는-퀴즈")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "PUZZLE_NOT_FOUND"


def test_today_is_server_date_and_progress_carries_over(client, login, clock):
    login(1)
    _complete(client, PUZZLES[0], 1)
    _complete(client, PUZZLES[1], 2)
    clock(14)  # 기기 시각(completed_at)은 13일 그대로여도 서버 날짜로 판단한다
    body = client.get("/api/me/usage-credits").json()
    assert (body["date"], body["rewards_today"], body["progress"]) == ("2026-11-14", 0, 2)
    assert body["counted_today"] == []
    body = _complete(client, PUZZLES[0], 5).json()  # 어제 센 퀴즈도 오늘은 다시 센다
    assert (body["rewards_today"], body["progress"]) == (1, 0)


def test_daily_max_rewards(client, login, clock, monkeypatch):
    monkeypatch.setattr(credit_service, "DAILY_MAX_REWARDS", 1)
    monkeypatch.setattr(credit_service, "PUZZLES_PER_REWARD", 1)
    login(1)
    assert _complete(client, PUZZLES[0], 1).json()["rewards_today"] == 1
    body = _complete(client, PUZZLES[1], 2).json()  # 오늘 보상을 다 받았으면 세지 않는다
    assert (body["rewards_today"], body["progress"]) == (1, 0)
    assert body["counted_today"] == [PUZZLES[0]]
