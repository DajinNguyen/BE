from pydantic import BaseModel

from app.schemas.common import SampleValue
from app.schemas.company import CompanySummary


class TradingCompany(CompanySummary):
    trading_value: SampleValue  # 거래대금 (억 원)


class CompanyRanking(BaseModel):
    criteria: str  # 화면에 그대로 보여주는 선정 기준
    items: list[CompanySummary]


class AnalyzedCompany(BaseModel):
    stock_code: str
    name: str
    headline: str
    generated_at: str


class HomeDashboard(BaseModel):
    base_time: str
    trading_value_top: list[TradingCompany]
    rising: CompanyRanking
    most_viewed: CompanyRanking
    ai_analyzed: list[AnalyzedCompany]
