from datetime import date, datetime

import httpx
import pytest

from app.services import naver, yahoo
from app.services.market_clock import KST

# 리포트 만들기는 로그인과 ADK가 필요하다
pytestmark = pytest.mark.usefixtures("member")


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
    {
        "localDate": "20261016",
        "openPrice": 217000.0,
        "highPrice": 219500.0,
        "lowPrice": 216500.0,
        "closePrice": 218000.0,
    },
    {
        "localDate": "20261019",
        "openPrice": 218000.0,
        "highPrice": 220000.0,
        "lowPrice": 217500.0,
        "closePrice": 218000.0,
    },
    {
        "localDate": "20261020",
        "openPrice": 218000.0,
        "highPrice": 218500.0,
        "lowPrice": 209000.0,
        "closePrice": 210750.0,
    },
]


def _minute(at: str, price: float, volume: int = 10) -> dict:
    return {
        "localDateTime": at,
        "currentPrice": price,
        "openPrice": price,
        "highPrice": price + 100,
        "lowPrice": price - 100,
        "accumulatedTradingVolume": volume,
    }


# 10월 19일 2분, 20일 09:00~09:11 1분 캔들 (09:00~09:09 · 09:10~09:11 두 묶음)
MINUTES = [_minute("20261019152900", 217000.0), _minute("20261019153000", 217500.0)] + [
    _minute(f"2026102009{m:02d}00", 218000.0 + m * 100) for m in range(12)
]


def _hour(day: str, hour: int, price: float) -> dict:
    return _minute(f"{day}{hour:02d}0000", price, volume=1000)


# 네이버 시간 캔들: 10월 20일 하루 정규장(09~15시) + 장 마감 뒤 16시
HOURS = [_hour("20261020", h, 218000.0 + h * 100) for h in range(9, 17)]


def _yahoo_chart(days: list[str]) -> dict:
    """야후 1시간 캔들 (동시호가 15시 칸 없이 09~14시)."""
    stamps, prices = [], []
    for day in days:
        for h in range(9, 15):
            at = datetime.strptime(f"{day}{h:02d}", "%Y%m%d%H").replace(tzinfo=KST)
            stamps.append(int(at.timestamp()))
            prices.append(217000.0 + h * 10)
    quote = {"open": prices, "high": prices, "low": prices, "close": prices}
    return {
        "chart": {
            "result": [
                {
                    "timestamp": stamps,
                    "indicators": {"quote": [{**quote, "volume": [500] * len(stamps)}]},
                }
            ]
        }
    }


YAHOO = _yahoo_chart(["20261016", "20261019", "20261020"])


class FakeNaverApi:
    """httpx.get 대신 쓰는 가짜 네이버 금융 (야후 1시간 캔들도 여기서 받는다)."""

    def __init__(self, fail: bool = False, yahoo_fail: bool = False):
        self.fail = fail
        self.yahoo_fail = yahoo_fail
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        request = httpx.Request("GET", url)
        if "finance.yahoo.com" in url:
            if self.yahoo_fail:
                raise httpx.ConnectError("down", request=request)
            return httpx.Response(200, json=YAHOO, request=request)
        if self.fail:
            raise httpx.ConnectError("down", request=request)
        if url.endswith("/minute60"):
            return httpx.Response(200, json=HOURS, request=request)
        if url.endswith("/minute"):
            return httpx.Response(200, json=MINUTES, request=request)
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
        hourly = yahoo.YahooClient()
        monkeypatch.setattr(yahoo, "get_yahoo", lambda: hourly)
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


def test_preopen_blank_fields_do_not_drop_the_batch(use_naver, monkeypatch):
    """장 시작 전에는 거래대금이 ""로 온다. 예전에는 그 때문에 묶음 50종목이 모두 예전 시드 가격으로
    보였다 (알테오젠 실제 17만 원 → 화면 41만 원)."""
    preopen = dict(QUOTES["005930"], accumulatedTradingValueRaw="", fluctuationsRatioRaw="0.00")
    broken = dict(QUOTES["271560"], closePriceRaw="")  # 현재가가 없는 종목은 그것만 빠진다
    monkeypatch.setitem(QUOTES, "005930", preopen)
    monkeypatch.setitem(QUOTES, "271560", broken)
    client = use_naver(FakeNaverApi())
    quotes = client.quotes(["005930", "000660", "271560"])
    assert set(quotes) == {"005930", "000660"}
    assert (quotes["005930"].price, quotes["005930"].trading_value) == (210750, 0)


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


def _quote_at(monkeypatch, price, compare, traded_at="2026-10-20T13:05:57.86+09:00"):
    monkeypatch.setitem(
        QUOTES,
        "005930",
        {
            **QUOTES["005930"],
            "closePriceRaw": str(price),
            "compareToPreviousClosePriceRaw": str(compare),
            "localTradedAt": traded_at,
        },
    )


def test_quote_previous_close_is_price_minus_change(monkeypatch, use_naver):
    _quote_at(monkeypatch, 211000, -7000)
    client = use_naver(FakeNaverApi())
    quote = client.quotes(["005930"])["005930"]
    assert quote.previous_close == 218000
    assert client.quotes(["000660"])["000660"].previous_close is None  # 변동 값이 없으면 모른다


def test_prices_today_candle_follows_live_quote(client, use_naver, monkeypatch):
    """차트 API는 오늘 종가를 늦게 갱신해서, 현재가(더 최근)로 마지막 캔들을 맞춘다."""
    _quote_at(monkeypatch, 211000, -7000)
    use_naver(FakeNaverApi())
    body = client.get("/api/companies/005930/prices", params={"period": "1m"}).json()
    last = body["points"][-1]
    assert (last["date"], last["close"]) == ("2026-10-20", 211000)
    assert (last["high"], last["low"]) == (218500, 209000)  # 범위 안이면 그대로
    assert body["points"][-2]["close"] == 218000  # 지난 캔들은 건드리지 않는다
    assert body["previous_close"] == 218000  # 현재가·등락률과 같은 기준


def test_prices_today_candle_range_widens_to_live_quote(client, use_naver, monkeypatch):
    _quote_at(monkeypatch, 219000, 1000)
    use_naver(FakeNaverApi())
    last = client.get("/api/companies/005930/prices", params={"period": "1m"}).json()["points"][-1]
    assert (last["close"], last["high"], last["low"]) == (219000, 219000, 209000)


def test_prices_after_hours_quote_does_not_change_candle(client, use_naver, monkeypatch):
    _quote_at(monkeypatch, 205000, -13000, traded_at="2026-10-20T16:10:00+09:00")
    use_naver(FakeNaverApi())
    body = client.get("/api/companies/005930/prices", params={"period": "1m"}).json()
    assert body["points"][-1]["close"] == 210750  # 시간외 가격은 정규장 캔들에 섞지 않는다
    assert body["previous_close"] == 218000


def test_prices_use_live_chart(client, use_naver):
    api = FakeNaverApi()
    use_naver(api)
    body = client.get("/api/companies/005930/prices", params={"period": "1m"}).json()
    assert body["is_sample"] is False
    assert body["interval"] == "day"
    assert body["points"][-1] == {
        "date": "2026-10-20",
        "time": None,
        "open": 218000,
        "high": 218500,
        "low": 209000,
        "close": 210750,
        "volume": 0,
    }
    assert any(url.endswith("/005930/day") for url in api.calls)

    client.get("/api/companies/005930/prices", params={"period": "1y"})
    assert any(url.endswith("/005930/week") for url in api.calls)


def test_closes_failure_returns_none(use_naver):
    client = use_naver(FakeNaverApi(fail=True))
    assert client.candles("005930", "day", date(2026, 9, 20), date(2026, 10, 20)) is None


def test_report_price_as_of_uses_live_date(client, use_naver):
    use_naver(FakeNaverApi())
    client.post("/api/companies/005930/report")
    report = client.get("/api/companies/005930/report").json()
    assert report["data_as_of"]["price"] == "2026-10-20"


def test_prices_one_day_is_minute_candles_of_last_trading_day(client, use_naver):
    use_naver(FakeNaverApi())
    body = client.get("/api/companies/005930/prices", params={"period": "1d"}).json()
    assert (body["interval"], body["is_sample"]) == ("1min", False)
    points = body["points"]
    assert {p["date"] for p in points} == {"2026-10-20"}  # 마지막 거래일만
    assert (points[0]["time"], points[-1]["time"], len(points)) == ("09:00", "09:11", 12)


def test_prices_five_days_bundle_minutes_into_ten(client, use_naver):
    use_naver(FakeNaverApi())
    points = client.get("/api/companies/005930/prices", params={"period": "5d"}).json()["points"]
    assert [(p["date"], p["time"]) for p in points] == [
        ("2026-10-19", "15:20"),
        ("2026-10-19", "15:30"),
        ("2026-10-20", "09:00"),
        ("2026-10-20", "09:10"),
    ]
    first_ten = points[2]
    assert (first_ten["open"], first_ten["close"]) == (218000, 218900)
    assert (first_ten["high"], first_ten["low"], first_ten["volume"]) == (219000, 217900, 100)


def test_prices_interval_picks_naver_timeframe(client, use_naver):
    api = FakeNaverApi()
    use_naver(api)
    for period, interval, timeframe in [
        ("1y", "day", "day"),
        ("5y", "month", "month"),
        ("all", "year", "year"),
    ]:
        res = client.get(
            "/api/companies/005930/prices", params={"period": period, "interval": interval}
        )
        assert res.json()["interval"] == interval
        assert api.calls[-1].endswith(f"/005930/{timeframe}")


def test_implausible_candles_are_dropped():
    broken = {**CHART[0], "openPrice": 44000.0, "highPrice": 45800.0, "lowPrice": 286.0}
    assert not naver._plausible(naver._parse_candle(broken))
    assert naver._plausible(naver._parse_candle(CHART[0]))


def test_prices_hourly_joins_yahoo_and_recent_naver(client, use_naver):
    use_naver(FakeNaverApi())
    body = client.get(
        "/api/companies/005930/prices", params={"period": "1m", "interval": "60min"}
    ).json()
    assert (body["interval"], body["is_sample"]) == ("60min", False)
    keys = [(p["date"], p["time"]) for p in body["points"]]
    # 야후 16·19일 (09~14시 + 일 캔들로 만든 15시 동시호가) → 네이버 20일 정규장 09~15시
    assert keys == [
        (day, f"{h:02d}:00") for day in ("2026-10-16", "2026-10-19") for h in range(9, 16)
    ] + [("2026-10-20", f"{h:02d}:00") for h in range(9, 16)]
    closing = body["points"][6]
    assert (closing["open"], closing["close"]) == (217140, 218000)  # 16일 종가
    assert body["points"][-1]["close"] == 219500  # 네이버 20일 15시 (16시 장외는 빠진다)


def test_prices_half_days_split_morning_and_afternoon(client, use_naver):
    use_naver(FakeNaverApi())
    points = client.get(
        "/api/companies/005930/prices", params={"period": "3m", "interval": "halfday"}
    ).json()["points"]
    assert [(p["date"], p["time"]) for p in points] == [
        (day, half)
        for day in ("2026-10-16", "2026-10-19", "2026-10-20")
        for half in ("09:00", "12:00")
    ]
    morning = points[0]  # 16일 09·10·11시
    assert (morning["open"], morning["close"], morning["volume"]) == (217090, 217110, 1500)


def test_prices_hourly_falls_back_to_days_without_yahoo(client, use_naver):
    use_naver(FakeNaverApi(yahoo_fail=True))
    body = client.get(
        "/api/companies/005930/prices", params={"period": "1m", "interval": "60min"}
    ).json()
    assert (body["interval"], body["is_sample"]) == ("day", False)
    assert all(p["time"] is None for p in body["points"])
