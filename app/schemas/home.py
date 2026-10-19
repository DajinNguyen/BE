from pydantic import BaseModel

from app.schemas.common import SampleValue
from app.schemas.company import CompanySummary, SectorGroup


class TradingCompany(CompanySummary):
    trading_value: SampleValue  # 거래대금 (억 원)
    ai_headline: str | None = None  # 마지막으로 성공한 AI 리포트의 한 줄 결론


class CompanyRanking(BaseModel):
    criteria: str  # 화면에 그대로 보여주는 선정 기준
    items: list[CompanySummary]


class AnalyzedCompany(BaseModel):
    stock_code: str
    name: str
    headline: str
    generated_at: str


class HomeDashboard(BaseModel):
    base_time: str  # 직전 거래일 장 마감 (예: "2026-10-16 15:30")
    trading_value_top: list[TradingCompany]  # 최대 30개, 거래대금 많은 순
    sector_companies: dict[SectorGroup, list[CompanySummary]]  # 업종 탭 6개 모두
    rising: CompanyRanking
    most_viewed: CompanyRanking
    ai_analyzed: list[AnalyzedCompany]
