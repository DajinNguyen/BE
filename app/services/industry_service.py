"""산업: 뜨는 산업, 산업 페이지, 산업 뉴스.

기간별 변화율(주가·거래대금·뉴스 언급)은 아직 집계하지 않아서 `app/data/industries.json` 의
예시 값을 쓴다 (`is_sample: true`). 관련 기업의 시가총액·등락률은 네이버 시세를 받으면 실제 값이다.
산업 뉴스는 실제 뉴스 검색("산업 이름 산업")을 먼저 쓰고, 받지 못하면 예시 기사를 쓴다.
점수 기준은 프론트 `src/config/industryHeat.ts` 와 같은 임시값이다 (PM 확정 필요).
"""

import math
from datetime import date, datetime, timedelta
from typing import Any

from app.core.errors import industry_not_found
from app.schemas.industry import (
    HeadlineMetric,
    HeatLevel,
    HeatPeriod,
    IndustryCompany,
    IndustryDetail,
    IndustrySummary,
    IndustryWhy,
    ScoreBreakdown,
    TrendPoint,
)
from app.schemas.news import NewsArticle
from app.services import data_store, news_search
from app.services.market_clock import last_close, last_close_date
from app.services.sample_news import latest_first, sample_article

# 점수 = 주가 상승률 40% + 거래대금 증가율 30% + 뉴스 언급 증가율 30% (임시값)
HEAT_WEIGHTS = {
    "price_change_rate": 0.4,
    "trading_value_change_rate": 0.3,
    "news_mention_change_rate": 0.3,
}
# 점수 순위로 상위 3개 high, 다음 3개 mid, 나머지 low (임시값)
HIGH_RANKS = 3
MID_RANKS = 3
PERIOD_LABELS: dict[HeatPeriod, str] = {"1w": "최근 1주", "1m": "최근 1개월"}
WHY_NEWS_COUNT = 2  # "왜 관심이 몰리나요?" 근거 뉴스 개수
# 관심도(주가·거래대금·뉴스 언급 변화율)를 아직 집계하지 않는 산업은 0으로 보여준다
NO_FACTORS = {"price_change_rate": 0, "trading_value_change_rate": 0, "news_mention_change_rate": 0}
NEWS_HOURS_AGO = [2, 5, 20, 30, 60]  # 예시 기사 시각 (최신순)


def _round1(value: float) -> float:
    """소수 첫째 자리 반올림 (프론트 Math.round 와 같게 .5 는 올린다)."""
    return math.floor(value * 10 + 0.5) / 10


def heat_score(factors: dict[str, float]) -> float:
    return _round1(sum(factors[key] * weight for key, weight in HEAT_WEIGHTS.items()))


def _level(rank: int) -> HeatLevel:
    if rank < HIGH_RANKS:
        return "high"
    return "mid" if rank < HIGH_RANKS + MID_RANKS else "low"


def _record(industry_id: str) -> dict[str, Any]:
    for record in data_store.industry_records():
        if record["industry_id"] == industry_id:
            return record
    raise industry_not_found()


def hot_industries(period: HeatPeriod, now: datetime | None = None) -> list[IndustrySummary]:
    """점수 높은 순."""
    as_of, _ = last_close(now)
    scored = sorted(
        (
            (heat_score(r["factors"][period]), r)
            for r in data_store.industry_records()
            if "factors" in r  # 관심도를 집계하지 않는 산업은 순위에 넣지 않는다
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return [
        IndustrySummary(
            industry_id=r["industry_id"],
            name=r["name"],
            icon_key=r["icon_key"],
            heat_score=score,
            heat_level=_level(rank),
            headline_metric=HeadlineMetric(
                label=PERIOD_LABELS[period],
                value=r["factors"][period]["price_change_rate"],
                unit="%",
            ),
            as_of=as_of,
        )
        for rank, (score, r) in enumerate(scored)
    ]


def _news_query(record: dict[str, Any]) -> str:
    return record.get("news_query") or f"{record['name'].replace('·', ' ')} 산업"


def industry_news(industry_id: str, size: int, now: datetime | None = None) -> list[NewsArticle]:
    """산업 관련 기사 (최신순)."""
    record = _record(industry_id)
    live = news_search.latest(_news_query(record), size)
    if live is not None:
        return live
    articles = [
        sample_article(
            article_id=f"{industry_id}-news-{i + 1}",
            title=topic,
            summary=f"{topic} 소식이에요. (예시 기사)",
            hours_ago=NEWS_HOURS_AGO[i % len(NEWS_HOURS_AGO)],
            url=f"https://example.com/industries/{industry_id}/{i + 1}",
            now=now,
        )
        for i, topic in enumerate(record["news_topics"])
    ]
    return latest_first(articles)[:size]


def _weekly_trend(month_change: float, seed: int, close_day: date) -> list[TrendPoint]:
    """1개월 전 대비 누적 등락률을 주 단위 5개 점으로 (예시 흐름)."""
    points = []
    for i, weeks_ago in enumerate([4, 3, 2, 1, 0]):
        wobble = 0 if i in (0, 4) else math.sin(seed + i) * 0.8
        points.append(
            TrendPoint(
                date=(close_day - timedelta(weeks=weeks_ago)).isoformat(),
                value=_round1(month_change * i / 4 + wobble),
            )
        )
    return points


def _companies(stock_codes: list[str]) -> list[IndustryCompany]:
    """시가총액 큰 순. 시가총액을 모르면 맨 뒤 (0)."""
    rows = [
        IndustryCompany(
            stock_code=c.stock_code,
            name=c.name,
            intro=c.one_liner,
            market_cap=c.market_cap.value if c.market_cap else 0,
            change_rate=c.change_rate.value,
        )
        for c in data_store.company_details_by_codes(stock_codes)
    ]
    return sorted(rows, key=lambda c: c.market_cap, reverse=True)


def get_industry(industry_id: str, now: datetime | None = None) -> IndustryDetail:
    record = _record(industry_id)
    as_of, _ = last_close(now)
    news = industry_news(industry_id, WHY_NEWS_COUNT, now)
    seed = data_store.industry_records().index(record)
    factors = record.get("factors") or {"1w": NO_FACTORS, "1m": NO_FACTORS}
    return IndustryDetail(
        industry_id=industry_id,
        name=record["name"],
        icon_key=record["icon_key"],
        description=record["description"],
        why=IndustryWhy(text=record["why"], news_ids=[n.id for n in news]),
        trend=_weekly_trend(factors["1m"]["price_change_rate"], seed, last_close_date(now)),
        score_breakdown=ScoreBreakdown(**factors["1w"], period="1w"),
        news=news,
        companies=_companies(record["companies"]),
        as_of=as_of,
    )
