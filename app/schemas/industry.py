"""산업 응답. 프론트 `frontend/src/types/industry.ts` 와 같다."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.news import NewsArticle

HeatLevel = Literal["high", "mid", "low"]
HeatPeriod = Literal["1w", "1m"]


class HeadlineMetric(BaseModel):
    label: str  # 예: "최근 1주"
    value: float
    unit: str


class IndustrySummary(BaseModel):
    """메인 "지금 관심이 몰리는 산업" 아이콘 하나."""

    industry_id: str
    name: str
    icon_key: str
    heat_score: float
    heat_level: HeatLevel
    headline_metric: HeadlineMetric
    as_of: str  # ISO 8601
    is_sample: bool = True


class IndustryRef(BaseModel):
    """기업 상세의 "소속 산업" 칩."""

    industry_id: str
    name: str


class IndustryWhy(BaseModel):
    text: str
    news_ids: list[str]


class TrendPoint(BaseModel):
    date: str  # YYYY-MM-DD
    value: float  # 1개월 전 대비 누적 등락률 (%)


class ScoreBreakdown(BaseModel):
    price_change_rate: float
    trading_value_change_rate: float
    news_mention_change_rate: float
    period: HeatPeriod


class IndustryCompany(BaseModel):
    stock_code: str
    name: str
    intro: str
    market_cap: float  # 조 원
    change_rate: float  # 전일 대비 (%)


class IndustryDetail(BaseModel):
    industry_id: str
    name: str
    icon_key: str
    description: str
    why: IndustryWhy
    trend: list[TrendPoint]
    score_breakdown: ScoreBreakdown
    news: list[NewsArticle]
    companies: list[IndustryCompany]  # 시가총액 큰 순
    as_of: str
    is_sample: bool = True
