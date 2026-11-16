from pydantic import BaseModel

from app.schemas.common import SampleValue
from app.schemas.company import CompanySummary
from app.schemas.industry import IndustryRef


class CompareMetrics(BaseModel):
    """비교 기준 연도(`fiscal_year`)의 값. 구할 수 없는 값은 None (지어내지 않는다)."""

    revenue: float | None  # 매출 (조 원)
    operating_income: float | None  # 영업이익 (조 원)
    operating_margin: float | None  # 영업이익률 (%)
    revenue_growth: float | None  # 전년 대비 매출 증가율 (%)
    debt_ratio: float | None  # 부채비율 (%)


class CompareYear(BaseModel):
    fiscal_year: int
    revenue: float
    operating_income: float
    operating_margin: float | None


class CompareCompany(BaseModel):
    stock_code: str
    name: str
    sector: str
    market: str
    market_cap: SampleValue | None
    is_financial: bool  # 금융업(은행·증권·보험·카드 등)인지
    has_financials: bool  # 재무제표를 불러왔는지
    basis: str | None  # "연결 기준" | "별도 기준"
    source: str | None
    fiscal_year: (
        int | None
    )  # metrics 의 연도. 보통 비교 기준 연도와 같다 (겹치는 연도가 없으면 이 회사의 최근 연도)
    metrics: CompareMetrics
    annual: list[
        CompareYear
    ]  # 두 회사에 모두 있는 연도만, 오래된 순 (겹치는 연도가 없으면 이 회사 것)


class Comparison(BaseModel):
    """두 회사를 같은 기간·같은 기준으로 맞춘 비교."""

    unit: str = "조 원"
    fiscal_year: int | None  # 비교 기준 연도 (두 회사에 모두 있는 가장 최근 사업연도)
    basis: str | None  # 두 회사 기준이 같으면 그 기준, 다르면 None
    comparable: bool  # 같은 연도·같은 기준으로 나란히 볼 수 있는지
    notes: list[str]  # 비교할 때 알아야 할 점 (쉬운 말)
    differences: list[str]  # 숫자로 본 차이 (쉬운 말, 서버가 계산해서 쓴다)
    companies: list[CompareCompany]  # 요청한 순서대로 2곳


class SimilarCompany(CompanySummary):
    """비교해 볼 만한 회사: 같은 산업이고 시가총액이 비슷하다."""

    market_cap: SampleValue | None
    industry: IndustryRef  # 같이 속한 산업
