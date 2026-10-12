from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ReportStatus = Literal["none", "generating", "done", "failed"]
KeyPointCategory = Literal["performance", "revenue_source", "recent_change", "risk", "strength"]
EvidenceKind = Literal["financials", "news", "vision"]

# 핵심 포인트는 항상 이 순서로 하나씩 보여준다.
KEY_POINT_ORDER: tuple[KeyPointCategory, ...] = (
    "performance",
    "revenue_source",
    "recent_change",
    "risk",
    "strength",
)


class KeyPointValue(BaseModel):
    label: str
    value: str
    period: str


class KeyPoint(BaseModel):
    category: KeyPointCategory
    title: str
    before: KeyPointValue | None
    after: KeyPointValue | None
    reason: str
    meaning: str
    evidence: list[EvidenceKind]


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    answer_index: int
    explanation: str


class ReportContent(BaseModel):
    """AI가 작성하는 부분. 근거 자료(evidence)와 메타 필드는 서버가 채운다."""

    headline: str
    key_points: list[KeyPoint]
    vision_summary: str
    quiz: list[QuizQuestion]


class FinancialHighlight(BaseModel):
    label: str
    value: str
    source: str
    is_sample: bool = False


class FinancialsEvidence(BaseModel):
    unit: str
    years: list[str]
    revenue: list[float]
    operating_income: list[float]
    highlights: list[FinancialHighlight]
    base_date: str | None = None


class NewsEvidence(BaseModel):
    title: str
    press: str
    published_at: str
    summary: str
    url: str


class RevenueMixItem(BaseModel):
    name: str
    amount: float
    unit: str
    period: str


class VisionEvidence(BaseModel):
    summary: str
    revenue_mix: list[RevenueMixItem]
    source: str
    base_date: str | None = None


class Evidence(BaseModel):
    financials: FinancialsEvidence | None = None
    news: list[NewsEvidence] = []
    vision: VisionEvidence | None = None


class CompanyReport(BaseModel):
    """마지막으로 성공한 리포트 내용 + 지금 상태."""

    stock_code: str
    status: ReportStatus
    generated_at: datetime | None = None
    headline: str = ""
    key_points: list[KeyPoint] = []
    evidence: Evidence = Field(default_factory=Evidence)
    quiz: list[QuizQuestion] = []
    sources: list[str] = []
    failure_reason: str | None = None
