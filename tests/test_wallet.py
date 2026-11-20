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


def _solve_three(client):
    for puzzle_id in PUZZLES:
        client.get(f"/api/learn/crosswords/{puzzle_id}")
        res = client.post(
            "/api/me/puzzle-completions", json={"puzzle_id": puzzle_id, "used_reveal": False}
        )
    return res.json()


def test_wallet_needs_login(client):
    res = client.get("/api/me/wallet")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "LOGIN_REQUIRED"


def test_signup_bonus_is_given_once(client):
    login_as(client, 1)
    wallet = client.get("/api/me/wallet").json()
    history = wallet.pop("history")
    assert wallet == {
        "balance": 50,
        "puzzle_progress": {"done": 0, "required": 3},
        "reward_count_today": 0,
        "reward_daily_limit": 2,
    }
    assert [(h["type"], h["amount"], h["reason"]) for h in history] == [("earn", 50, "가입 선물")]
    assert set(history[0]) == {"id", "type", "amount", "reason", "created_at"}
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
    assert (body["rewarded"], body["reward_count_today"], body["balance"]) == (True, 1, 70)
    wallet = client.get("/api/me/wallet").json()
    assert (wallet["reward_count_today"], wallet["puzzle_progress"]["done"]) == (1, 0)
    top = wallet["history"][0]
    assert (top["type"], top["amount"], top["reason"]) == ("earn", 20, "낱말퀴즈 보상")
    # 같은 날 같은 퀴즈를 또 풀어도 더 주지 않는다
    again = _solve_three(client)
    assert (again["rewarded"], again["balance"]) == (False, 70)


def test_report_takes_adk_only_when_it_is_made(client, fake_writer):
    login_as(client, 1)
    started = client.post("/api/companies/005930/report").json()
    # 만들기를 시작하면 7 ADK를 뺀 잔액을 같이 준다
    assert (started["status"], started["balance"]) == ("generating", 43)
    assert client.get("/api/companies/005930/report").json()["status"] == "done"
    wallet = client.get("/api/me/wallet").json()
    assert wallet["balance"] == 43
    top = wallet["history"][0]
    assert (top["type"], top["amount"], top["reason"]) == ("spend", 7, "AI 리포트 · 삼성전자")
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
    assert res.status_code == 400
    assert res.json()["error"] == {
        "code": "INSUFFICIENT_BALANCE",
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
        assert error.value.code == "INSUFFICIENT_BALANCE"
        assert wallet_service.balance(session, user_id) == 3  # 잡아 둔 금액은 이미 뺀 잔액
        wallet_service.settle(session, first)
        wallet_service.settle(session, first)  # 두 번 확정해도 한 번만
        assert wallet_service.balance(session, user_id) == 3
        # 결과가 나오지 않으면 돌려놓는다
        second = wallet_service.hold(session, user_id, 3, "chat", "챗봇", "chat:c")
        assert wallet_service.balance(session, user_id) == 0
        wallet_service.release(session, second)
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


def test_report_failure_gives_the_adk_back(client, fake_writer):
    login_as(client, 1)

    async def broken(context):
        raise RuntimeError("AI 고장")

    fake_writer.write = broken
    client.post("/api/companies/005930/report")
    assert _balance(client) == 50


def test_already_generating_report_takes_nothing(client, fake_writer):
    from datetime import UTC, datetime

    from app.models.report import ReportRecord

    with new_session() as session:
        session.add(
            ReportRecord(stock_code="005930", status="generating", started_at=datetime.now(UTC))
        )
        session.commit()
    login_as(client, 1)
    started = client.post("/api/companies/005930/report").json()
    assert (started["status"], started["balance"]) == ("generating", 50)


def test_history_shows_the_latest_ten(client):
    user_id = login_as(client, 1)
    assert _balance(client) == 50  # 가입 선물을 먼저 받는다
    with new_session() as session:
        for i in range(12):
            wallet_service.grant(session, user_id, 1, "puzzle_reward", f"보상 {i}", f"test:{i}")
    history = client.get("/api/me/wallet").json()["history"]
    assert len(history) == 10
    assert history[0]["reason"] == "보상 11"


def test_spend_api_takes_once_per_target(client):
    login_as(client, 1)
    assert client.post(
        "/api/me/wallet/spend", json={"reason": "chat", "target_id": "x"}
    ).json() == {"balance": 45}
    # 같은 대상으로 다시 보내면 다시 빼지 않는다
    assert client.post(
        "/api/me/wallet/spend", json={"reason": "chat", "target_id": "x"}
    ).json() == {"balance": 45}
    assert client.post(
        "/api/me/wallet/spend", json={"reason": "report", "target_id": "x"}
    ).json() == {"balance": 38}
    for i in range(5):
        res = client.post("/api/me/wallet/spend", json={"reason": "report", "target_id": f"r{i}"})
    assert _balance(client) == 3
    res = client.post("/api/me/wallet/spend", json={"reason": "report", "target_id": "last"})
    assert (res.status_code, res.json()["error"]["code"]) == (400, "INSUFFICIENT_BALANCE")
    assert (
        client.post("/api/me/wallet/spend", json={"reason": "gift", "target_id": "x"}).status_code
        == 422
    )


def test_spend_api_needs_login(client):
    res = client.post("/api/me/wallet/spend", json={"reason": "chat", "target_id": "x"})
    assert res.status_code == 401


def test_concurrent_same_spend_is_not_a_server_error(client, monkeypatch):
    """같은 요청이 동시에 오면 한쪽이 유니크 제약에 걸린다. 500 이 아니라 한 번만 빠진 것으로 본다."""
    from sqlalchemy.exc import IntegrityError

    login_as(client, 1)
    real_hold = wallet_service.hold

    def raced(session, user_id, price, reason, title, ref):
        real_hold(session, user_id, price, reason, title, ref)  # 다른 요청이 먼저 뺐다고 가정
        raise IntegrityError("INSERT", {}, Exception("uq_adk_entries_user_ref"))

    monkeypatch.setattr(wallet_service, "hold", raced)
    res = client.post("/api/me/wallet/spend", json={"reason": "report", "target_id": "005930"})
    assert res.status_code == 200
