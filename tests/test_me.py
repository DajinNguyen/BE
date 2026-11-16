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


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    """테스트에서는 퀴즈를 열자마자 완료해도 센다 (따로 확인하는 테스트만 시간을 둔다)."""
    monkeypatch.setattr(credit_service, "MIN_SOLVE_SECONDS", 0)


def _complete(client, puzzle_id, revealed=False, opened=True):
    if opened:  # 화면에서 퀴즈를 열면 이 요청이 먼저 나간다
        client.get(f"/api/learn/crosswords/{puzzle_id}")
    body = {"puzzle_id": puzzle_id, "used_reveal": revealed}
    return client.post("/api/me/puzzle-completions", json=body)


def _done(res) -> int:
    return res.json()["puzzle_progress"]["done"]


def _credits(client):
    return client.get("/api/me/usage-credits").json()


def test_credits_need_login(client):
    assert client.get("/api/me/usage-credits").status_code == 401
    assert _complete(client, PUZZLES[0]).status_code == 401


def test_three_puzzles_give_one_reward(client, login, clock):
    login(1)
    assert _credits(client) == {
        "date": "2026-11-13",
        "rewards_today": 0,
        "progress": 0,
        "counted_today": [],
        "completions": [],
    }
    first = _complete(client, PUZZLES[0]).json()
    assert first == {
        "puzzle_progress": {"done": 1, "required": 3},
        "rewarded": False,
        "balance": 50,  # 가입 선물
        "reward_count_today": 0,
        "reward_daily_limit": 2,
    }
    assert _done(_complete(client, PUZZLES[1])) == 2
    assert _complete(client, PUZZLES[2]).json() == {
        "puzzle_progress": {"done": 0, "required": 3},  # 3/3 이 되면 보상을 주고 0/3 부터
        "rewarded": True,
        "balance": 70,  # 보상 1회 = 20 ADK
        "reward_count_today": 1,
        "reward_daily_limit": 2,
    }
    credits = _credits(client)
    assert credits["counted_today"] == PUZZLES
    assert [c["puzzle_id"] for c in credits["completions"]] == PUZZLES


def test_old_request_format_still_works(client, login, clock):
    """예전 프론트가 보내던 `revealed`·`completed_at` 도 받는다."""
    login(1)
    client.get(f"/api/learn/crosswords/{PUZZLES[0]}")
    body = {
        "puzzle_id": PUZZLES[0],
        "completed_at": "2026-11-13T09:01:00+09:00",
        "revealed": False,
    }
    assert _done(client.post("/api/me/puzzle-completions", json=body)) == 1
    assert _credits(client)["completions"] == [body]


def test_revealed_and_duplicate_are_not_counted(client, login, clock):
    login(1)
    assert _done(_complete(client, PUZZLES[0], revealed=True)) == 0
    assert _done(_complete(client, PUZZLES[0])) == 1
    # 같은 퀴즈는 하루 한 번
    again = _complete(client, PUZZLES[0]).json()
    assert (again["puzzle_progress"]["done"], again["rewarded"]) == (1, False)


def test_unknown_puzzle_is_404(client, login, clock):
    login(1)
    res = _complete(client, "없는-퀴즈")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "PUZZLE_NOT_FOUND"


def test_today_is_server_date_and_progress_carries_over(client, login, clock):
    login(1)
    _complete(client, PUZZLES[0])
    _complete(client, PUZZLES[1])
    clock(14)  # 날짜는 서버 시각으로 판단한다
    credits = _credits(client)
    assert (credits["date"], credits["rewards_today"], credits["progress"]) == ("2026-11-14", 0, 2)
    assert credits["counted_today"] == []
    body = _complete(client, PUZZLES[0]).json()  # 어제 센 퀴즈도 오늘은 다시 센다
    assert (body["reward_count_today"], body["puzzle_progress"]["done"]) == (1, 0)
    assert body["rewarded"] is True


def test_daily_max_rewards(client, login, clock, monkeypatch):
    monkeypatch.setattr(credit_service, "DAILY_MAX_REWARDS", 1)
    monkeypatch.setattr(credit_service, "PUZZLES_PER_REWARD", 1)
    login(1)
    assert _complete(client, PUZZLES[0]).json()["reward_count_today"] == 1
    body = _complete(client, PUZZLES[1]).json()  # 오늘 보상을 다 받았으면 세지 않는다
    assert (body["reward_count_today"], body["puzzle_progress"]["done"]) == (1, 0)
    assert (body["rewarded"], body["balance"], body["reward_daily_limit"]) == (False, 70, 1)
    assert _credits(client)["counted_today"] == [PUZZLES[0]]


def test_completion_without_opening_the_puzzle_is_not_counted(client, login, clock):
    login(1)
    assert _done(_complete(client, PUZZLES[0], opened=False)) == 0
    credits = _credits(client)
    assert (credits["counted_today"], len(credits["completions"])) == ([], 1)  # 기록은 남긴다
    # 퀴즈를 연 뒤에 보낸 완료는 센다
    assert _done(_complete(client, PUZZLES[0])) == 1


def test_puzzle_opened_while_logged_out_does_not_count(client, login, clock):
    client.get(f"/api/learn/crosswords/{PUZZLES[0]}")  # 로그인 전에 열었다
    login(1)
    assert _done(_complete(client, PUZZLES[0], opened=False)) == 0


def test_completion_right_after_opening_is_not_counted(client, login, clock, monkeypatch):
    monkeypatch.setattr(credit_service, "MIN_SOLVE_SECONDS", 15)
    login(1)
    assert _done(_complete(client, PUZZLES[0])) == 0
    # 연 지 충분히 지난 뒤(여기서는 기준을 0초로 낮춰서)에는 센다. 다시 열어도 처음 연 시각이 기준이다
    monkeypatch.setattr(credit_service, "MIN_SOLVE_SECONDS", 0)
    assert _done(_complete(client, PUZZLES[0])) == 1
