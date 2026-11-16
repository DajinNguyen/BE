from datetime import datetime

import httpx
import pytest

from app.services import market_brief, market_service, naver, naver_index, news_search
from app.services.market_brief import BriefReason, MarketBrief
from app.services.market_clock import KST
from app.services.naver import Quote
from app.services.naver_index import IndexQuote
from app.services.sample_news import sample_article

TRADED_AT = datetime(2026, 11, 3, 13, 28, tzinfo=KST)
INDEX_ITEMS = [
    {
        "itemCode": "KOSPI",
        "closePriceRaw": "5640.44",
        "compareToPreviousClosePriceRaw": "-218.64",
        "fluctuationsRatioRaw": "-3.73",
        "marketStatus": "OPEN",
        "localTradedAt": "2026-11-03T13:28:42+09:00",
    },
    {
        "itemCode": "KOSDAQ",
        "closePriceRaw": "706.96",
        "compareToPreviousClosePriceRaw": "-27.40",
        "fluctuationsRatioRaw": "-3.73",
        "marketStatus": "OPEN",
        "localTradedAt": "2026-11-03T13:28:40+09:00",
    },
]


def test_naver_index_parses_quotes_and_intraday(monkeypatch):
    def api(url, params=None, headers=None, timeout=None):
        if url.endswith("/minute10"):
            body = [
                {"localDateTime": "20261103090000", "currentPrice": 5647.29},
                {"localDateTime": "20261103091000", "currentPrice": 5669.73},
            ]
        else:
            body = {"datas": INDEX_ITEMS}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(naver_index.httpx, "get", api)
    client = naver_index.NaverIndexClient()
    kospi = client.quotes()["KOSPI"]
    assert (kospi.value, kospi.change, kospi.change_rate, kospi.is_open) == (
        5640.44,
        -218.64,
        -3.73,
        True,
    )
    assert client.intraday("KOSPI", TRADED_AT.date()) == [("09:00", 5647.29), ("09:10", 5669.73)]


def test_naver_index_failure_returns_empty(monkeypatch):
    def api(url, params=None, headers=None, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(naver_index.httpx, "get", api)
    client = naver_index.NaverIndexClient()
    assert client.quotes() == {}
    assert client.intraday("KOSPI", TRADED_AT.date()) is None


class FakeBriefWriter:
    def __init__(self, brief: MarketBrief | None):
        self.brief = brief
        self.contexts = []

    def write(self, context):
        self.contexts.append(context)
        return self.brief


NEWS = [
    sample_article(
        article_id=f"live-{n}",
        title=f"기사 {n}",
        summary="요약",
        hours_ago=n,
        url=f"https://n/{n}",
        now=TRADED_AT,
    )
    for n in (1, 2)
]


@pytest.fixture
def live_market(monkeypatch):
    def use(writer, is_open=True):
        quotes = {
            code: IndexQuote(code, value, change, -3.73, is_open, TRADED_AT)
            for code, value, change in [("KOSPI", 5640.44, -218.64), ("KOSDAQ", 706.96, -27.4)]
        }
        monkeypatch.setattr(naver_index, "quotes", lambda: quotes)
        monkeypatch.setattr(
            naver_index, "intraday", lambda code, day: [("09:00", 5700.0), ("09:10", 5650.0)]
        )
        monkeypatch.setattr(
            naver,
            "quotes",
            lambda codes: {
                c: Quote(c, 1000, -5.0 if c == "005930" else -1.0, 0, 0, TRADED_AT) for c in codes
            },
        )
        monkeypatch.setattr(news_search, "latest", lambda query, size: NEWS)
        monkeypatch.setattr(market_brief, "get_brief_writer", lambda: writer)
        monkeypatch.setattr(market_service, "_run_in_background", lambda job: job())
        monkeypatch.setattr(market_service, "_store", market_service._BriefStore())

    return use


def test_live_summary_uses_real_index_and_ai_brief(client, live_market):
    writer = FakeBriefWriter(
        MarketBrief(
            headline="반도체가 약해서 코스피가 3% 넘게 내리고 있어요",
            reasons=[
                BriefReason(
                    title="반도체가 크게 내렸어요",
                    description="큰 반도체 회사 주가가 내렸어요.",
                    industry_ids=["semiconductor", "없는산업"],
                    news_numbers=[2, 9],
                )
            ],
        )
    )
    live_market(writer)

    first = client.get("/api/market/summary").json()
    # 처음에는 AI 글을 만드는 동안 지수로 만든 기본 문장
    assert first["headline"] == "코스피는 3.73% 떨어지고 있고, 코스닥은 3.73% 떨어지고 있어요"
    assert first["reasons"] == []
    kospi = first["indices"][0]
    assert kospi == {
        "code": "KOSPI",
        "value": 5640.44,
        "change": -218.64,
        "change_rate": -3.73,
        "is_sample": False,
        "intraday": [{"time": "09:00", "value": 5700.0}, {"time": "09:10", "value": 5650.0}],
    }
    assert first["as_of"] == "2026-11-03T13:28:00+09:00"

    # AI 에게는 실제 지수·산업 등락률·기사만 넘긴다
    context = writer.contexts[0]
    assert context["is_open"] is True
    assert context["indices"][0] == {
        "name": "코스피",
        "value": 5640.44,
        "change": -218.64,
        "change_rate": -3.73,
    }
    semi = next(i for i in context["industries"] if i["industry_id"] == "semiconductor")
    assert -5.0 < semi["change_rate"] < -1.0  # 삼성전자 -5%, 나머지 -1% 의 평균
    assert [n["number"] for n in context["news"]] == [1, 2]

    body = client.get("/api/market/summary").json()
    assert body["headline"] == "반도체가 약해서 코스피가 3% 넘게 내리고 있어요"
    [reason] = body["reasons"]
    # 목록에 없는 산업·기사 번호는 버린다
    assert [i["industry_id"] for i in reason["industries"]] == ["semiconductor"]
    assert [n["id"] for n in reason["news"]] == ["live-2"]
    assert len(writer.contexts) == 1  # 30분 안에는 다시 쓰지 않는다


def test_live_summary_without_ai_key(client, live_market):
    live_market(None, is_open=False)
    body = client.get("/api/market/summary").json()
    assert body["headline"] == "코스피는 3.73% 떨어졌고, 코스닥은 3.73% 떨어졌어요"
    assert body["reasons"] == []
    assert body["indices"][1]["is_sample"] is False


def test_live_summary_ai_failure_tells_biggest_movers(client, live_market):
    writer = FakeBriefWriter(None)
    live_market(writer)
    client.get("/api/market/summary")
    body = client.get("/api/market/summary").json()
    # AI 글을 못 받아도 옆 숫자와 같은 말("코스피는 3.73% 내렸어요") 대신 많이 움직인 산업을 알려준다
    assert body["headline"].endswith("회사 주가가 가장 많이 떨어지고 있어요")
    assert "코스피" not in body["headline"] and body["reasons"] == []
    assert len(writer.contexts) == 1  # 실패하면 잠시 기다렸다가 다시 해 본다


def test_only_direction_spots_headlines_without_a_reason():
    only = market_service.only_direction
    assert only("코스피는 내리고 코스닥은 올랐어요")
    assert only("코스피가 1.2% 올랐어요")
    assert only("코스피는 떨어지고 코스닥은 올랐어요")
    assert only("코스피와 코스닥 모두 소폭 하락했어요")
    assert not only("반도체가 약해서 코스피가 3% 넘게 내리고 있어요")
    assert not only("미국 금리 인하 기대에 코스피가 올랐어요")


def test_live_summary_replaces_result_only_headline_with_first_reason(client, live_market):
    writer = FakeBriefWriter(
        MarketBrief(
            headline="코스피는 내리고 코스닥도 내리고 있어요",
            reasons=[
                BriefReason(
                    title="반도체 수출이 줄었다는 소식에 내렸어요",
                    description="반도체 회사 주가가 내렸어요.",
                    industry_ids=["semiconductor"],
                    news_numbers=[1],
                )
            ],
        )
    )
    live_market(writer)
    client.get("/api/market/summary")
    body = client.get("/api/market/summary").json()
    assert body["headline"] == "반도체 수출이 줄었다는 소식에 내렸어요"


def test_live_summary_result_only_headline_without_reasons_uses_movers(client, live_market):
    live_market(
        FakeBriefWriter(MarketBrief(headline="코스피와 코스닥이 내렸어요", reasons=[])), False
    )
    client.get("/api/market/summary")
    body = client.get("/api/market/summary").json()
    assert body["headline"].endswith("회사 주가가 가장 많이 떨어졌어요")


def test_live_summary_replaces_too_long_headline(client, live_market):
    long = "SK하이닉스와 삼성바이오로직스 같은 큰 회사들의 주가가 떨어져서 코스피가 내렸어요"
    reason = BriefReason(
        title="큰 반도체 회사 주가가 떨어졌어요",
        description="설명",
        industry_ids=[],
        news_numbers=[1],
    )
    live_market(FakeBriefWriter(MarketBrief(headline=long, reasons=[reason])))
    client.get("/api/market/summary")
    body = client.get("/api/market/summary").json()
    assert body["headline"] == "큰 반도체 회사 주가가 떨어졌어요"
