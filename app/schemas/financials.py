from pydantic import BaseModel

from app.schemas.common import SampleValue


class AnnualResult(BaseModel):
    fiscal_year: int
    revenue: float
    operating_income: float


class LatestQuarter(BaseModel):
    label: str
    revenue: float
    operating_income: float
    note: str | None = None


class SegmentRevenue(BaseModel):
    code: str
    name: str
    revenue: float


class Segments(BaseModel):
    base_label: str
    items: list[SegmentRevenue]


class Indicators(BaseModel):
    roe: SampleValue | None = None
    debt_ratio: SampleValue | None = None
    per: SampleValue | None = None
    pbr: SampleValue | None = None


class PerformanceMetric(BaseModel):
    value: float
    unit: str
    yoy_change_rate: float | None  # 작년 같은 분기 대비 (%)
    yoy_is_sample: bool = False


class PerformanceSegment(BaseModel):
    name: str
    revenue: float
    unit: str


class PerformanceSource(BaseModel):
    title: str  # DART 공시명
    date: str  # YYYY-MM-DD
    url: str


class RecentPerformance(BaseModel):
    """기업 상세 > 개요 "최근 사업 성과" (실적 발표 요약)."""

    period_label: str  # 예: "2025년 4분기 실적"
    announced_at: str  # YYYY-MM-DD
    revenue: PerformanceMetric
    operating_income: PerformanceMetric
    summary: str
    segments: list[PerformanceSegment] = []
    source: PerformanceSource


class Financials(BaseModel):
    stock_code: str
    unit: str
    basis: str
    source: str | None
    annual: list[AnnualResult]
    latest_quarter: LatestQuarter | None
    segments: Segments | None
    indicators_as_of: str | None = None  # 주요 지표 기준 (예: "2025년 사업보고서")
    recent_performance: RecentPerformance | None = None  # 없으면 "아직 불러오지 못했어요"
    indicators: Indicators
