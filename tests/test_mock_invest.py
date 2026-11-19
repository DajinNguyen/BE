from datetime import UTC, datetime

import pytest

from app.core.db import new_session
from app.models.mock_invest import MockTrade
from app.services import mock_invest_service, naver
from tests.conftest import login_as

SAMSUNG = "005930"
HYNIX = "000660"
PRICES = {SAMSUNG: 128_500, HYNIX: 200_000}
RATES = {SAMSUNG: 1.4, HYNIX: -2.0}


@pytest.fixture(autouse=True)
def quotes(monkeypatch):
    """네이버 대신 정해 둔 시세를 쓴다. 테스트 안에서 `PRICES` 를 바꾸면 시세가 바뀐다."""
    prices = dict(PRICES)

    def fake(codes):
        return {
            c: naver.Quote(
                stock_code=c,
                price=prices[c],
                change_rate=RATES.get(c, 0.0),
                trading_value=0,
                market_cap=1_000_000_000_000,
                traded_at=datetime(2026, 11, 18, 10, 20),
            )
            for c in codes
            if c in prices
        }

    monkeypatch.setattr(naver, "quotes", fake)
    return prices


@pytest.fixture
def user(client):
    return login_as(client, 1)  # 가입 선물 50 ADK = 가상 50만 원


def _order(client, side, quantity, code=SAMSUNG):
    return client.post(
        "/api/me/mock-invest/orders",
        json={"stock_code": code, "side": side, "quantity": quantity},
    )


def _no_duplicate_window(monkeypatch):
    monkeypatch.setattr(mock_invest_service, "DUPLICATE_WINDOW", mock_invest_service.timedelta(0))


def _exchange(client, adk):
    return client.post("/api/me/mock-invest/exchange", json={"adk": adk})


@pytest.fixture
def funded(client, user):
    """30 ADK 를 가상 30만 원으로 환전해 둔다 (지갑에는 20 ADK 남는다)."""
    assert _exchange(client, 30).status_code == 200
    return user


def test_needs_login(client):
    for res in (
        client.get("/api/me/mock-invest"),
        client.get("/api/me/mock-invest/trades"),
        _order(client, "buy", 1),
        _exchange(client, 1),
    ):
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "LOGIN_REQUIRED"


def test_portfolio_before_starting(client, user):
    body = client.get("/api/me/mock-invest").json()
    assert body == {
        "started": False,
        "adk_to_won": 10_000,
        "adk_balance": 50,
        "cash": 0,
        "evaluation": 0,
        "total_asset": 0,
        "profit": 0,
        "profit_rate": None,
        "today_change": 0,
        "price_as_of": None,
        "holdings": [],
    }
    assert client.get("/api/me/mock-invest/trades").json() == []


def test_exchange_turns_adk_into_cash(client, user):
    res = _exchange(client, 30)
    assert res.status_code == 200
    body = res.json()
    assert (body["exchange"]["adk"], body["exchange"]["won"]) == (30, 300_000)
    assert body["balance"] == 20
    portfolio = body["portfolio"]
    assert (portfolio["started"], portfolio["adk_balance"]) == (True, 20)
    assert (portfolio["cash"], portfolio["total_asset"]) == (300_000, 300_000)

    # 지갑 내역에 쉬운 말로 남는다
    wallet = client.get("/api/me/wallet").json()
    assert wallet["balance"] == 20
    top = wallet["history"][0]
    assert (top["type"], top["amount"], top["reason"]) == (
        "spend",
        30,
        "모의투자 · 30 ADK를 가상 300,000원으로 환전",
    )


@pytest.mark.parametrize("adk", [0, -1, 1.5, 2.0, "2", None, True])
def test_invalid_exchange_amount(client, user, adk):
    res = _exchange(client, adk)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_AMOUNT"


def test_cannot_exchange_more_adk_than_held(client, user):
    res = _exchange(client, 51)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
    assert client.get("/api/me/wallet").json()["balance"] == 50
    assert client.get("/api/me/mock-invest").json()["cash"] == 0


def test_same_exchange_twice_exchanges_once(client, user):
    first = _exchange(client, 5).json()
    second = _exchange(client, 5).json()
    assert second["exchange"]["id"] == first["exchange"]["id"]
    assert second["balance"] == 45


def test_buy_uses_cash_not_adk(client, funded):
    res = _order(client, "buy", 2)
    assert res.status_code == 200
    body = res.json()
    trade = body["trade"]
    assert {k: trade[k] for k in ("stock_code", "name", "side", "quantity", "price")} == {
        "stock_code": SAMSUNG,
        "name": "삼성전자",
        "side": "buy",
        "quantity": 2,
        "price": 128_500,
    }
    assert (trade["amount"], trade["amount_adk"]) == (257_000, 0)
    assert body["balance"] == 20  # 앰똥깡은 그대로 (소수가 생기지 않는다)

    portfolio = body["portfolio"]
    assert portfolio["started"] is True
    assert (portfolio["adk_balance"], portfolio["cash"]) == (20, 43_000)
    assert (portfolio["evaluation"], portfolio["total_asset"]) == (257_000, 300_000)
    assert (portfolio["profit"], portfolio["profit_rate"]) == (0, 0.0)
    assert portfolio["price_as_of"] == "2026-11-18 10:20"
    assert portfolio["holdings"] == [
        {
            "stock_code": SAMSUNG,
            "name": "삼성전자",
            "quantity": 2,
            "avg_price": 128_500,
            "current_price": 128_500,
            "change_rate": 1.4,
            "evaluation": 257_000,
            "profit": 0,
            "profit_rate": 0.0,
        }
    ]
    # 오늘 1.4% 올랐으니 어제 종가 기준으로 257,000 − 257,000 ÷ 1.014 ≈ 3,548원 늘었다
    assert portfolio["today_change"] == 3548
    assert client.get("/api/me/wallet").json()["balance"] == 20


def test_sell_adds_cash_and_tracks_profit(client, funded, quotes, monkeypatch):
    _no_duplicate_window(monkeypatch)
    _order(client, "buy", 2)
    quotes[SAMSUNG] = 130_000  # 1,500원 올랐다
    portfolio = client.get("/api/me/mock-invest").json()
    assert (portfolio["profit"], portfolio["profit_rate"]) == (3_000, 1.17)
    assert portfolio["total_asset"] == 303_000

    res = _order(client, "sell", 1)
    assert res.status_code == 200
    body = res.json()
    assert (body["trade"]["side"], body["trade"]["amount_adk"]) == ("sell", 0)
    assert (body["balance"], body["portfolio"]["cash"]) == (20, 173_000)
    holding = body["portfolio"]["holdings"][0]
    assert (holding["quantity"], holding["avg_price"], holding["profit"]) == (1, 128_500, 1_500)

    _order(client, "sell", 1)  # 다 팔면 보유 종목에서 빠진다
    portfolio = client.get("/api/me/mock-invest").json()
    assert portfolio["holdings"] == []
    assert portfolio["profit_rate"] is None
    assert portfolio["started"] is True
    assert (portfolio["cash"], portfolio["adk_balance"]) == (303_000, 20)

    trades = client.get("/api/me/mock-invest/trades").json()
    assert [(t["side"], t["quantity"]) for t in trades] == [("sell", 1), ("sell", 1), ("buy", 2)]
    assert set(trades[0]) == {
        "id",
        "stock_code",
        "name",
        "side",
        "quantity",
        "price",
        "amount",
        "amount_adk",
        "created_at",
    }


def test_average_price_after_buying_twice(client, funded, quotes, monkeypatch):
    _no_duplicate_window(monkeypatch)
    _order(client, "buy", 1)
    quotes[SAMSUNG] = 131_500
    _order(client, "buy", 1)
    holding = client.get("/api/me/mock-invest").json()["holdings"][0]
    assert (holding["quantity"], holding["avg_price"]) == (2, 130_000)


def test_legacy_adk_trades_do_not_touch_cash(client, funded):
    """환전 기능 전에 앰똥깡으로 바로 산 주식은 보유 종목에 남고, 팔면 가상 현금이 된다."""
    with new_session() as session:
        session.add(
            MockTrade(
                user_id=funded,
                stock_code=HYNIX,
                name="SK하이닉스",
                side="buy",
                quantity=1,
                price=200_000,
                amount=200_000,
                amount_adk=20,
                created_at=datetime(2026, 11, 17, 10, 0, tzinfo=UTC),
            )
        )
        session.commit()
    portfolio = client.get("/api/me/mock-invest").json()
    assert portfolio["cash"] == 300_000  # 예전에 앰똥깡으로 낸 돈은 빼지 않는다
    assert [h["stock_code"] for h in portfolio["holdings"]] == [HYNIX]
    assert portfolio["total_asset"] == 500_000

    body = _order(client, "sell", 1, code=HYNIX).json()
    assert body["portfolio"]["cash"] == 500_000
    assert body["balance"] == 20


@pytest.mark.parametrize("quantity", [0, -1, 10_001, 1.5, 2.0, "2", None, True])
def test_invalid_quantity(client, user, quantity):
    res = _order(client, "buy", quantity)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_QUANTITY"


def test_not_enough_cash(client, funded):
    res = _order(client, "buy", 3)  # 385,500원 > 30만 원
    assert res.status_code == 400
    assert res.json()["error"] == {
        "code": "INSUFFICIENT_CASH",
        "message": "가상 현금이 모자라요. 385,500원이 필요한데 지금 300,000원이 있어요. "
        "앰똥깡을 환전해 주세요.",
        "required": 385_500,
        "cash": 300_000,
    }
    portfolio = client.get("/api/me/mock-invest").json()
    assert (portfolio["cash"], portfolio["holdings"]) == (300_000, [])
    assert client.get("/api/me/wallet").json()["balance"] == 20


def test_cannot_buy_before_exchanging(client, user):
    res = _order(client, "buy", 1)
    assert res.json()["error"]["code"] == "INSUFFICIENT_CASH"
    assert client.get("/api/me/wallet").json()["balance"] == 50


def test_cannot_sell_more_than_held(client, funded, monkeypatch):
    _no_duplicate_window(monkeypatch)
    res = _order(client, "sell", 1)
    assert res.status_code == 400
    assert res.json()["error"] == {
        "code": "INSUFFICIENT_QUANTITY",
        "message": "가진 수량보다 많이 팔 수 없어요. 지금 0주를 가지고 있어요.",
        "held": 0,
    }
    _order(client, "buy", 1)
    assert _order(client, "sell", 2).json()["error"]["held"] == 1


def test_unknown_company(client, user):
    res = _order(client, "buy", 1, code="999999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "COMPANY_NOT_FOUND"


def test_same_order_twice_executes_once(client, funded):
    first = _order(client, "buy", 1).json()
    second = _order(client, "buy", 1).json()
    assert second["trade"]["id"] == first["trade"]["id"]
    assert second["portfolio"]["cash"] == first["portfolio"]["cash"] == 171_500
    assert len(client.get("/api/me/mock-invest/trades").json()) == 1


def test_trades_are_per_user(client, funded):
    _order(client, "buy", 1)
    login_as(client, 2)
    assert client.get("/api/me/mock-invest").json()["started"] is False
    assert client.get("/api/me/mock-invest/trades").json() == []


def test_withdrawal_removes_trades(client, funded):
    _order(client, "buy", 1)
    assert client.delete("/api/auth/me").status_code in (200, 204)
    with new_session() as session:
        assert mock_invest_service.list_trades(session, funded) == []
        assert mock_invest_service._exchanges(session, funded) == []
