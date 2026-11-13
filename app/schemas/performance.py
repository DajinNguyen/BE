from typing import Literal

from pydantic import BaseModel


class PerformanceSummary(BaseModel):
    revenue: float
    operating_income: float
    operating_margin: float  # 영업이익률 (%)


class PerformanceChange(BaseModel):
    revenue_rate: float | None  # 변화율 (%)
    operating_income_rate: float | None


class QuarterPerformance(BaseModel):
    quarter: str  # 예: "25.4Q"
    revenue: float
    operating_income: float
    operating_margin: float


class SegmentPerformance(BaseModel):
    name: str
    revenue: float
    operating_income: float | None  # 사업별 영업이익을 공개하지 않으면 null
    yoy_rate: float | None
    comment: str


class ReasonEvidence(BaseModel):
    type: Literal["disclosure", "news"]
    label: str
    date: str  # YYYY-MM-DD
    url: str


class PerformanceReason(BaseModel):
    title: str
    description: str
    evidence: list[ReasonEvidence]


class PerformanceSource(BaseModel):
    title: str  # DART 공시명
    date: str  # YYYY-MM-DD
    url: str


class PerformanceDetail(BaseModel):
    """재무 상세 "최근 사업 성과 분석". 없는 블록은 null·빈 배열 (그 블록만 "자료가 없어요")."""

    period_label: str  # 예: "2025년 4분기"
    announced_at: str  # 그 분기 보고서 공시일 (YYYY-MM-DD)
    unit: str  # "조 원" 또는 "억 원"
    summary: PerformanceSummary | None
    yoy: PerformanceChange | None  # 작년 같은 분기 대비
    qoq: PerformanceChange | None  # 지난 분기 대비
    quarterly: list[QuarterPerformance]  # 오래된 분기 → 최근 분기, 최대 8개
    segments: list[SegmentPerformance]
    reasons: list[PerformanceReason]
    source: PerformanceSource | None
