from datetime import date

import httpx
import pytest

from app.services import naver


def _item(code, price, rate, trading_value, market_cap, traded_at="2026-10-20T13:05:57.86+09:00"):
    return {
        "itemCode": code,
        "closePriceRaw": str(price),
        "fluctuationsRatioRaw": str(rate),
        "accumulatedTradingValueRaw": str(trading_value),
        "marketValueFullRaw": str(market_cap),
        "localTradedAt": traded_at,
    }


QUOTES = {
    "005930": _item("005930", 210750, -3.33, 1_748_742_000_000, 1_232_103_216_636_000),
    "000660": _item("000660", 512000, 1.2, 2_100_000_000_000, 372_700_000_000_000),
    "271560": _item("271560", 103200, 0.68, 20_000_000_000, 4_080_000_000_000),
}

CHART = [
    {"localDate": "20261016", "closePrice": 218000.0},
    {"localDate": "20261019", "closePrice": 218000.0},
    {"localDate": "20261020", "closePrice": 210750.0},
]


class FakeNaverApi:
    """httpx.get 대신 쓰는 가짜 네이버 금융."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        request = httpx.Request("GET", url)
        if self.fail:
            raise httpx.ConnectError("down", request=request)
        if "/chart/" in url:
            return httpx.Response(200, json=CHART, request=request)
        codes = url.rsplit("/", 1)[-1].split(",")
        datas = [QUOTES[c] for c in codes if c in QUOTES]
        return httpx.Response(200, json={"datas": datas}, request=request)


@pytest.fixture
def use_naver(monkeypatch):
    """가짜 네이버에 붙은 NaverClient로 바꿔 끼운다."""

    def install(api: FakeNaverApi) -> naver.NaverClient:
        monkeypatch.setattr(naver.httpx, "get", api)
        client = naver.NaverClient()
        monkeypatch.setattr(naver, "get_naver", lambda: client)
        return client

    return install


def test_quotes_parse_and_cache(use_naver):
    api = FakeNaverApi()
    client = use_naver(api)
    quotes = client.quotes(["005930", "271560", "999999"])
    assert set(quotes) == {"005930", "271560"}  # 네이버가 모르는 종목은 빠진다
    q = quotes["005930"]
    assert (q.price, q.change_rate, q.trading_value) == (210750, -3.33, 1_748_742_000_000)
    assert q.traded_at.isoformat().startswith("2026-10-20T13:05:57")

    client.quotes(["005930", "271560"])  # 1분 안에는 다시 묻지 않는다
    assert len(api.calls) == 1


def test_quotes_failure_returns_empty_and_backs_off(use_naver):
    api = FakeNaverApi(fail=True)
    client = use_naver(api)
    assert client.quotes(["005930"]) == {}
    assert client.quotes(["005930"]) == {}
    assert len(api.calls) == 1  # 실패 직후에는 매번 다시 부르지 않는다


def test_company_uses_live_price(client, use_naver):
    use_naver(FakeNaverApi())
    summary = client.get("/api/companies", params={"query": "005930"}).json()[0]
    assert summary["current_price"] == {"value": 210750, "is_sample": False}
    assert summary["change_rate"] == {"value": -3.33, "is_sample": False}

    detail = client.get("/api/companies/005930").json()
    assert detail["price_as_of"] == "2026-10-20"
    assert detail["market_cap"] == {"value": 1232.1, "is_sample": False}  # 조 원


def test_company_without_quote_keeps_sample(client, use_naver):
    use_naver(FakeNaverApi())
    body = client.get("/api/companies/005380").json()  # 가짜 네이버에 없는 회사
    assert body["current_price"]["is_sample"] is True


def test_naver_down_falls_back_to_sample(client, use_naver):
    use_naver(FakeNaverApi(fail=True))
    body = client.get("/api/companies/005930").json()
    assert body["current_price"] == {"value": 128500, "is_sample": True}
    home = client.get("/api/home").json()
    assert home["trading_value_top"][0]["trading_value"]["is_sample"] is True
    assert len(home["trading_value_top"]) == 30


def test_home_ranks_by_live_trading_value(client, use_naver):
    use_naver(FakeNaverApi())
    home = client.get("/api/home").json()
    top = home["trading_value_top"]
    assert [c["stock_code"] for c in top] == ["000660", "005930", "271560"]
    assert top[1]["trading_value"] == {"value": 17487, "is_sample": False}  # 억 원
    assert home["base_time"] == "2026-10-20 13:05"  # 장중이면 마지막 체결 시각

    # 업종 탭은 시가총액 순 (시세가 없는 회사는 뒤에 시드 순서대로)
    semis = [c["stock_code"] for c in home["sector_companies"]["semiconductor"]]
    assert semis[:2] == ["005930", "000660"]


def test_home_base_time_after_close_is_market_close(client, use_naver, monkeypatch):
    monkeypatch.setitem(
        QUOTES, "005930", {**QUOTES["005930"], "localTradedAt": "2026-10-20T16:10:00+09:00"}
    )
    use_naver(FakeNaverApi())
    assert client.get("/api/home").json()["base_time"] == "2026-10-20 15:30"


def test_prices_use_live_chart(client, use_naver):
    api = FakeNaverApi()
    use_naver(api)
    body = client.get("/api/companies/005930/prices", params={"period": "1m"}).json()
    assert body["is_sample"] is False
    assert body["points"][-1] == {"date": "2026-10-20", "close": 210750}
    assert any(url.endswith("/005930/day") for url in api.calls)

    client.get("/api/companies/005930/prices", params={"period": "1y"})
    assert any(url.endswith("/005930/week") for url in api.calls)


def test_closes_failure_returns_none(use_naver):
    client = use_naver(FakeNaverApi(fail=True))
    assert client.closes("005930", date(2026, 9, 20), date(2026, 10, 20), weekly=False) is None


def test_report_price_as_of_uses_live_date(client, use_naver):
    use_naver(FakeNaverApi())
    client.post("/api/companies/005930/report")
    report = client.get("/api/companies/005930/report").json()
    assert report["data_as_of"]["price"] == "2026-10-20"
