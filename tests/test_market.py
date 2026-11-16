import asyncio
from datetime import datetime

from app.services import market_service
from app.services.market_clock import KST, last_close


def test_market_summary(client):
    res = client.get("/api/market/summary")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"as_of", "is_previous_session", "headline", "indices", "reasons"}
    assert body["as_of"].endswith("T15:30:00+09:00")
    assert body["headline"]
    assert [i["code"] for i in body["indices"]] == ["KOSPI", "KOSDAQ"]
    kospi = body["indices"][0]
    assert kospi["is_sample"] is True
    assert kospi["intraday"][0]["time"] == "09:00"
    assert kospi["intraday"][-1] == {"time": "15:30", "value": kospi["value"]}

    assert len(body["reasons"]) == 3
    reason = body["reasons"][0]
    assert reason["industries"][0]["industry_id"] == "semiconductor"
    assert [n["id"] for n in reason["news"]] == ["market-chip", "market-chip-2"]
    # 시장 이유의 산업은 뜨는 산업과 같은 id 를 쓴다
    hot_ids = {i["industry_id"] for i in client.get("/api/industries/hot").json()}
    assert all(i["industry_id"] in hot_ids for r in body["reasons"] for i in r["industries"])


def test_last_close_previous_session():
    # 평일 장중이면 직전 거래일, 장 마감 뒤면 오늘
    assert last_close(datetime(2026, 10, 26, 10, 0, tzinfo=KST)) == (
        "2026-10-23T15:30:00+09:00",
        True,
    )
    assert last_close(datetime(2026, 10, 26, 16, 0, tzinfo=KST)) == (
        "2026-10-26T15:30:00+09:00",
        False,
    )


def test_market_news(client):
    res = client.get("/api/market/news", params={"date": "2026-10-24"})
    assert res.status_code == 200
    news = res.json()
    assert len(news) == 6
    times = [n["published_at"] for n in news]
    assert times == sorted(times, reverse=True)
    first = news[0]
    assert first["press"] == "예시 뉴스" and first["url"].startswith("https://example.com/")
    assert first["company_stock_code"] is None
    assert first["published_at"].endswith("+09:00")


def test_market_news_hours_ago():
    now = datetime(2026, 10, 24, 18, 0, tzinfo=KST)
    news = asyncio.run(market_service.market_news(now))
    assert news[0].id == "market-chip" and news[0].published_at == "2026-10-24T16:00:00+09:00"


def test_market_news_invalid_date(client):
    res = client.get("/api/market/news", params={"date": "10/24"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PARAM"
