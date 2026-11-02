"""메인 "오늘의 시장": 지수·한마디·움직인 이유, 시장 뉴스.

지수·한마디·이유는 아직 실제 자료(지수 시세, AI 정리)를 붙이지 않아서
`app/data/market.json` 의 예시 값을 쓴다 (`is_sample: true`). 예측·투자 권유 표현은 넣지 않는다.
"""

import math
from datetime import datetime

from app.schemas.market import (
    IntradayPoint,
    MarketIndex,
    MarketReason,
    MarketSummary,
    ReasonIndustry,
)
from app.schemas.news import NewsArticle
from app.services.data_store import load_json
from app.services.market_clock import last_close
from app.services.sample_news import latest_first, sample_article

INTRADAY_STEPS = 39  # 09:00 ~ 15:30, 10분 간격


def _intraday(open_value: float, close: float, seed: int) -> list[IntradayPoint]:
    """예시 당일 흐름. 마지막 값은 종가와 같다."""
    points = []
    for i in range(INTRADAY_STEPS + 1):
        minutes = 9 * 60 + i * 10
        wave = math.sin(i * 0.7 + seed) * (abs(close - open_value) * 0.25 + 2)
        value = (
            close
            if i == INTRADAY_STEPS
            else open_value + (close - open_value) * i / INTRADAY_STEPS + wave
        )
        points.append(
            IntradayPoint(time=f"{minutes // 60:02d}:{minutes % 60:02d}", value=round(value, 2))
        )
    return points


def market_news(now: datetime | None = None) -> list[NewsArticle]:
    """시장 기사 (최신순). 예시 기사라 날짜와 상관없이 같은 목록이다."""
    return latest_first(
        [
            sample_article(
                article_id=n["id"],
                title=n["title"],
                summary=n["summary"],
                hours_ago=n["hours_ago"],
                url=f"https://example.com/market/{n['id']}",
                now=now,
            )
            for n in load_json("market.json")["news"]
        ]
    )


def get_summary(now: datetime | None = None) -> MarketSummary:
    data = load_json("market.json")
    as_of, is_previous = last_close(now)
    news = {n.id: n for n in market_news(now)}
    return MarketSummary(
        as_of=as_of,
        is_previous_session=is_previous,
        headline=data["headline"],
        indices=[
            MarketIndex(
                code=i["code"],
                value=i["value"],
                change=i["change"],
                change_rate=i["change_rate"],
                is_sample=True,
                intraday=_intraday(i["open"], i["value"], seed),
            )
            for seed, i in enumerate(data["indices"], start=1)
        ],
        reasons=[
            MarketReason(
                title=r["title"],
                description=r["description"],
                industries=[ReasonIndustry(**ind) for ind in r["industries"]],
                news=[news[i] for i in r["news_ids"] if i in news],
            )
            for r in data["reasons"]
        ],
    )
