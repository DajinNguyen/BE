from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.news import NewsArticle

ReportStatus = Literal["none", "generating", "done", "failed"]
KeyPointCategory = Literal["performance", "revenue_source", "recent_change", "risk", "strength"]
EvidenceKind = Literal["financials", "news", "vision"]

EVIDENCE_LABELS: dict[str, str] = {"financials": "재무제표", "news": "뉴스", "vision": "기업 비전"}

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


class EvidenceRef(BaseModel):
    """핵심 포인트의 근거 칩. 화면에는 "label date" 로 보여준다 (예: "뉴스 10.15")."""

    type: EvidenceKind
    label: str
    date: str | None = None  # YYYY-MM-DD 가 아니면 화면이 받은 글자를 그대로 붙인다


class _KeyPointBase(BaseModel):
    category: KeyPointCategory
    title: str
    before: KeyPointValue | None
    after: KeyPointValue | None
    reason: str
    meaning: str


class KeyPointDraft(_KeyPointBase):
    """AI가 쓰는 핵심 포인트. 근거는 종류만 고르고, 칩 이름·날짜는 서버가 붙인다."""

    evidence: list[EvidenceKind]


class KeyPoint(_KeyPointBase):
    evidence: list[EvidenceRef]

    @field_validator("evidence", mode="before")
    @classmethod
    def _from_kinds(cls, value: Any) -> Any:
        """예전에 저장한 리포트는 근거가 종류 이름뿐이라 기본 이름으로 바꿔 읽는다."""
        if not isinstance(value, list):
            return value
        return [
            (
                {"type": v, "label": EVIDENCE_LABELS.get(v, v), "date": None}
                if isinstance(v, str)
                else v
            )
            for v in value
        ]


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    answer_index: int
    explanation: str


class ReportContent(BaseModel):
    """AI가 작성하는 부분. 근거 자료(evidence)와 메타 필드는 서버가 채운다."""

    headline: str
    key_points: list[KeyPointDraft]
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
    news: list[NewsArticle] = []  # 뉴스 탭과 같은 형식
    vision: VisionEvidence | None = None


class ReportDataAsOf(BaseModel):
    """리포트를 만들 때 쓴 자료의 기준."""

    financials: str | None = None  # 예: "2025년 사업보고서"
    price: str | None = None  # 주가 기준일 (YYYY-MM-DD)
    news: str | None = None  # 가장 최근 뉴스 날짜


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
    data_as_of: ReportDataAsOf | None = None

    @model_validator(mode="before")
    @classmethod
    def _news_from_old_format(cls, data: Any) -> Any:
        """예전에 저장한 리포트의 근거 뉴스({title, press, published_at, summary, url})를
        뉴스 탭과 같은 형식으로 바꿔 읽는다."""
        if not isinstance(data, dict) or not isinstance(data.get("evidence"), dict):
            return data
        news = data["evidence"].get("news")
        if not isinstance(news, list):
            return data
        converted = [
            (
                {
                    "id": n.get("title", ""),  # 원문 주소가 같은 기사가 있어서 제목으로
                    "title": n.get("title", ""),
                    "press": n.get("press", ""),
                    "published_at": n.get("published_at", ""),
                    "url": n.get("url", ""),
                    "summary_short": n.get("summary", ""),
                    "company_stock_code": data.get("stock_code"),
                }
                if isinstance(n, dict) and "summary_short" not in n
                else n
            )
            for n in news
        ]
        return {**data, "evidence": {**data["evidence"], "news": converted}}
