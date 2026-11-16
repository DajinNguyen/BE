import pytest

from app.core.db import new_session
from app.services import credit_service, wallet_service
from tests.conftest import login_as

PUZZLES = ["basics-easy", "financials-normal", "market-normal"]


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(credit_service, "MIN_SOLVE_SECONDS", 0)


def _balance(client) -> int:
    return client.get("/api/me/wallet").json()["balance"]


def _solve_three(client, minute=0):
    for i, puzzle_id in enumerate(PUZZLES):
        client.get(f"/api/learn/crosswords/{puzzle_id}")
        body = {
            "puzzle_id": puzzle_id,
            "completed_at": f"2026-11-13T09:{minute + i:02d}:00+09:00",
            "revealed": False,
        }
        res = client.post("/api/me/puzzle-completions", json=body)
    return res.json()


def test_wallet_needs_login(client):
    res = client.get("/api/me/wallet")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "LOGIN_REQUIRED"


def test_signup_bonus_is_given_once(client):
    login_as(client, 1)
    wallet = client.get("/api/me/wallet").json()
    assert wallet["balance"] == 50
    assert wallet["unit"] == "ADK"
    assert wallet["prices"] == {"report": 7, "chat": 5}
    assert wallet["rewards"] == {
        "signup": 50,
        "puzzle": 20,
        "puzzles_per_reward": 3,
        "puzzle_daily_max": 2,
    }
    assert [(h["amount"], h["reason"], h["title"]) for h in wallet["history"]] == [
        (50, "signup", "가입 축하")
    ]
    # 다시 보거나 다시 로그인해도 한 번만
    login_as(client, 1)
    assert _balance(client) == 50


def test_wallets_are_separate_per_user(client):
    login_as(client, 1)
    _solve_three(client)
    assert _balance(client) == 70
    login_as(client, 2)
    wallet = client.get("/api/me/wallet").json()
    assert wallet["balance"] == 50
    assert len(wallet["history"]) == 1


def test_puzzle_reward_gives_adk_only_when_three_are_solved(client):
    login_as(client, 1)
    body = _solve_three(client)
    assert (body["rewards_today"], body["balance"]) == (1, 70)
    wallet = client.get("/api/me/wallet").json()
    assert (wallet["history"][0]["amount"], wallet["history"][0]["reason"]) == (20, "puzzle_reward")
    # 같은 날 같은 퀴즈를 또 풀어도 더 주지 않는다
    assert _solve_three(client, minute=10)["balance"] == 70


def test_report_takes_adk_only_when_it_is_made(client, fake_writer):
    login_as(client, 1)
    assert client.post("/api/companies/005930/report").json()["status"] == "generating"
    assert client.get("/api/companies/005930/report").json()["status"] == "done"
    wallet = client.get("/api/me/wallet").json()
    assert wallet["balance"] == 43
    assert (wallet["history"][0]["amount"], wallet["history"][0]["reason"]) == (-7, "report")
    assert wallet["history"][0]["title"] == "AI 리포트 만들기 (삼성전자)"
    # 이미 만들어진 리포트를 보는 건 ADK가 들지 않는다
    client.get("/api/companies/005930/report")
    assert _balance(client) == 43


def test_failed_report_takes_nothing(client, fake_writer):
    login_as(client, 1)

    async def broken(context):
        raise RuntimeError("AI 고장")

    fake_writer.write = broken
    client.post("/api/companies/005930/report")
    assert client.get("/api/companies/005930/report").json()["status"] == "failed"
    wallet = client.get("/api/me/wallet").json()
    assert wallet["balance"] == 50
    assert len(wallet["history"]) == 1  # 가입 축하만


def test_report_is_refused_when_adk_is_short(client, fake_writer):
    user_id = login_as(client, 1)
    with new_session() as session:
        wallet_service.grant(session, user_id, -45, "report", "테스트용", "test-spend")
    res = client.post("/api/companies/005930/report")
    assert res.status_code == 402
    assert res.json()["error"] == {
        "code": "INSUFFICIENT_ADK",
        "message": "ADK가 모자라요. 7 ADK가 필요한데 지금 5 ADK가 있어요.",
        "required": 7,
        "balance": 5,
    }
    assert fake_writer.calls == []
    assert client.get("/api/companies/005930/report").json()["status"] == "none"
    assert _balance(client) == 5


def test_guest_cannot_make_report_but_can_read_it(client, fake_writer):
    res = client.post("/api/companies/005930/report")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "LOGIN_REQUIRED"
    assert fake_writer.calls == []
    login_as(client, 1)
    client.post("/api/companies/005930/report")
    login_as(client, None)
    assert client.get("/api/companies/005930/report").json()["status"] == "done"


def test_pressing_twice_takes_adk_once(client):
    """만드는 중에 또 누르면 다시 시작하지 않고 ADK도 한 번만 잡는다."""
    user_id = login_as(client, 1)
    with new_session() as session:
        first = wallet_service.hold(session, user_id, 7, "report", "리포트", "report:a")
        # 잡아 둔 금액은 확정 전에도 쓸 수 있는 금액에서 빠진다 (잔액보다 많이 쓸 수 없다)
        wallet_service.grant(session, user_id, -40, "report", "테스트용", "test-spend")
        with pytest.raises(Exception) as error:
            wallet_service.hold(session, user_id, 7, "report", "리포트", "report:b")
        assert error.value.code == "INSUFFICIENT_ADK"
        assert wallet_service.balance(session, user_id) == 10  # 확정 전이라 잔액은 그대로
        wallet_service.settle(session, first)
        wallet_service.settle(session, first)  # 두 번 확정해도 한 번만
        assert wallet_service.balance(session, user_id) == 3


def test_same_reward_is_not_given_twice(client):
    user_id = login_as(client, 1)
    with new_session() as session:
        assert wallet_service.grant(session, user_id, 20, "puzzle_reward", "퀴즈", "puzzle:d:1")
        assert not wallet_service.grant(session, user_id, 20, "puzzle_reward", "퀴즈", "puzzle:d:1")
        assert wallet_service.balance(session, user_id) == 70


def test_report_without_financials_takes_nothing(client, fake_writer, monkeypatch):
    from app.services import report_service

    monkeypatch.setattr(report_service, "find_financials", lambda code: None)
    login_as(client, 1)
    assert client.post("/api/companies/005930/report").json()["status"] == "failed"
    assert _balance(client) == 50
