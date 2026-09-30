from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ChartType = Literal[
    "annual_revenue", "annual_operating_income", "operating_margin", "segment_revenue"
]
KeyPointCategory = Literal["performance", "profitability", "stability", "business", "news"]
KeyPointStatus = Literal["good", "neutral", "caution"]


class KeyPoint(BaseModel):
    id: str
    category: KeyPointCategory
    title: str
    body: str
    status: KeyPointStatus
    source: str
    base_date: str


class ReportChart(BaseModel):
    id: str
    type: ChartType
    title: str
    caption: str


class Connection(BaseModel):
    chain: list[str]
    conclusion: str


class QuizItem(BaseModel):
    id: str
    question: str
    options: list[str]
    answer_index: int
    explanation: str


class ReportContent(BaseModel):
    """AI가 작성하는 부분. 나머지 메타 필드는 서버가 채운다."""

    summary: str
    key_points: list[KeyPoint]
    charts: list[ReportChart]
    connection: Connection
    strengths: list[str]
    watch_points: list[str]
    quiz: list[QuizItem]


class CompanyReport(ReportContent):
    stock_code: str
    generated_at: datetime
    generation_seconds: float
    is_cached: bool
    analyzed_sources: list[str]
    disclaimers: list[str]
    data_sources: list[str]
