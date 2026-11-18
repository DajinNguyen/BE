from datetime import datetime

import pytest

from app.core.db import new_session
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


def test_needs_login(client):
    for res in (
        client.get("/api/me/mock-invest"),
        client.get("/api/me/mock-invest/trades"),
        _order(client, "buy", 1),
    ):
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "LOGIN_REQUIRED"


def test_portfolio_before_starting(client, user):
    body = client.get("/api/me/mock-invest").json()
    assert body == {
        "started": False,
        "adk_to_won": 10_000,
        "cash_adk": 50,
        "cash": 500_000,
        "evaluation": 0,
        "total_asset": 500_000,
        "profit": 0,
        "profit_rate": None,
        "today_change": 0,
        "price_as_of": None,
        "holdings": [],
    }
    assert client.get("/api/me/mock-invest/trades").json() == []


def test_buy_spends_adk_and_shows_holding(client, user):
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
    assert (trade["amount"], trade["amount_adk"]) == (257_000, 25.7)
    assert body["balance"] == 24.3

    portfolio = body["portfolio"]
    assert portfolio["started"] is True
    assert (portfolio["cash_adk"], portfolio["cash"]) == (24.3, 243_000)
    assert (portfolio["evaluation"], portfolio["total_asset"]) == (257_000, 500_000)
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

    # 지갑과 같은 잔액, 지갑 내역에 쉬운 말로 남는다
    wallet = client.get("/api/me/wallet").json()
    assert wallet["balance"] == 24.3
    top = wallet["history"][0]
    assert (top["type"], top["amount"], top["reason"]) == (
        "spend",
        25.7,
        "모의투자 · 삼성전자 2주 사기",
    )


def test_sell_returns_adk_and_tracks_profit(client, user, quotes, monkeypatch):
    _no_duplicate_window(monkeypatch)
    _order(client, "buy", 2)
    quotes[SAMSUNG] = 130_000  # 1,500원 올랐다
    portfolio = client.get("/api/me/mock-invest").json()
    assert (portfolio["profit"], portfolio["profit_rate"]) == (3_000, 1.17)
    assert portfolio["total_asset"] == 503_000

    res = _order(client, "sell", 1)
    assert res.status_code == 200
    body = res.json()
    assert (body["trade"]["side"], body["trade"]["amount_adk"]) == ("sell", 13)
    assert body["balance"] == 37.3
    holding = body["portfolio"]["holdings"][0]
    assert (holding["quantity"], holding["avg_price"], holding["profit"]) == (1, 128_500, 1_500)

    wallet = client.get("/api/me/wallet").json()
    assert (wallet["history"][0]["type"], wallet["history"][0]["reason"]) == (
        "earn",
        "모의투자 · 삼성전자 1주 팔기",
    )

    _order(client, "sell", 1)  # 다 팔면 보유 종목에서 빠진다
    portfolio = client.get("/api/me/mock-invest").json()
    assert portfolio["holdings"] == []
    assert portfolio["profit_rate"] is None
    assert portfolio["started"] is True
    assert portfolio["cash"] == 503_000

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


def test_average_price_after_buying_twice(client, user, quotes, monkeypatch):
    _no_duplicate_window(monkeypatch)
    _order(client, "buy", 1)
    quotes[SAMSUNG] = 131_500
    _order(client, "buy", 1)
    holding = client.get("/api/me/mock-invest").json()["holdings"][0]
    assert (holding["quantity"], holding["avg_price"]) == (2, 130_000)


@pytest.mark.parametrize("quantity", [0, -1, 10_001, 1.5, 2.0, "2", None, True])
def test_invalid_quantity(client, user, quantity):
    res = _order(client, "buy", quantity)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_QUANTITY"


def test_not_enough_adk(client, user):
    res = _order(client, "buy", 4)  # 514,000원 > 50만 원
    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "INSUFFICIENT_BALANCE"
    assert (error["required"], error["balance"]) == (51.4, 50)
    assert client.get("/api/me/wallet").json()["balance"] == 50
    assert client.get("/api/me/mock-invest").json()["started"] is False


def test_cannot_sell_more_than_held(client, user, monkeypatch):
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


def test_same_order_twice_executes_once(client, user):
    first = _order(client, "buy", 1).json()
    second = _order(client, "buy", 1).json()
    assert second["trade"]["id"] == first["trade"]["id"]
    assert second["balance"] == first["balance"] == 37.15
    assert len(client.get("/api/me/mock-invest/trades").json()) == 1


def test_decimal_balance_blocks_report_when_short(client, user, quotes, monkeypatch):
    """6.99 ADK면 7 ADK짜리 AI 리포트를 만들 수 없다."""
    _no_duplicate_window(monkeypatch)
    quotes[SAMSUNG] = 430_100  # 43.01 ADK → 6.99 ADK 남는다
    _order(client, "buy", 1)
    assert client.get("/api/me/wallet").json()["balance"] == 6.99
    res = client.post(f"/api/companies/{SAMSUNG}/report")
    assert res.status_code == 400
    assert res.json()["error"] == {
        "code": "INSUFFICIENT_BALANCE",
        "message": "ADK가 모자라요. 7 ADK가 필요한데 지금 6.99 ADK가 있어요.",
        "required": 7,
        "balance": 6.99,
    }


def test_trades_are_per_user(client, user):
    _order(client, "buy", 1)
    login_as(client, 2)
    assert client.get("/api/me/mock-invest").json()["started"] is False
    assert client.get("/api/me/mock-invest/trades").json() == []


def test_withdrawal_removes_trades(client, user):
    _order(client, "buy", 1)
    assert client.delete("/api/auth/me").status_code in (200, 204)
    with new_session() as session:
        assert mock_invest_service.list_trades(session, user) == []
