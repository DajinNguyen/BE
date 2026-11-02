"""메인 "오늘의 시장" 응답. 프론트 `frontend/src/types/market.ts` 와 같다."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.news import NewsArticle


class IntradayPoint(BaseModel):
    time: str  # HH:mm
    value: float


class MarketIndex(BaseModel):
    code: Literal["KOSPI", "KOSDAQ"]
    value: float
    change: float  # 어제 대비 등락폭
    change_rate: float  # 어제 대비 등락률 (%)
    is_sample: bool
    intraday: list[IntradayPoint] | None = None


class ReasonIndustry(BaseModel):
    industry_id: str
    name: str
    change_rate: float


class MarketReason(BaseModel):
    title: str
    description: str
    industries: list[ReasonIndustry]
    news: list[NewsArticle]


class MarketSummary(BaseModel):
    as_of: str  # ISO 8601
    is_previous_session: bool
    headline: str
    indices: list[MarketIndex]
    reasons: list[MarketReason]
