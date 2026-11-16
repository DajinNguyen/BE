"""비교하기 응답. 프론트 `frontend/src/types/compare.ts` 와 같다."""

from pydantic import BaseModel


class CompareCompany(BaseModel):
    """비교하기의 한 회사. 금액은 `unit`(조 원), 비율은 %. 구할 수 없는 값은 None (지어내지 않는다)."""

    stock_code: str
    name: str
    sector: str
    market_cap: float | None  # 시가총액 (조 원)
    is_financial: bool  # 은행·보험·증권 등 금융업이면 부채비율을 비교하지 않는다
    revenue: float | None
    operating_income: float | None
    operating_margin: float | None  # 영업이익률 (%)
    revenue_growth: float | None  # 전년 대비 매출 증가율 (%)
    debt_ratio: float | None  # 부채비율 (%)
    unit: str = "조 원"
    base_period: str | None  # 이 회사 숫자의 기간 (예: "2025년 연간"). 재무제표가 없으면 None
    basis: str | None  # "연결 기준" | "별도 기준"


class CompareSource(BaseModel):
    title: str
    url: str
    date: str  # YYYY-MM-DD


class ComparePoint(BaseModel):
    title: str  # 예: "규모", "수익성", "성장 속도"
    description: str


class CompareAnalysis(BaseModel):
    """한눈에 비교 (AI가 쓴 글). 숫자는 비교 응답의 값만 쓴다."""

    headline: str
    points: list[ComparePoint]
    reason: str | None  # 왜 다른가요? (사업 구성 차이 1~2문장)
    sources: list[CompareSource]


class CompanyCompare(BaseModel):
    """GET /api/companies/compare?left=&right= (left = 비교할 기업, right = 지금 보는 기업)."""

    base_period: str  # 두 회사 공통 기간 (예: "2025년 연간"). 겹치는 연도가 없으면 ""
    basis: str  # 두 회사 공통 기준. 다르면 지금 보는 기업(right)의 기준
    left: CompareCompany
    right: CompareCompany
    analysis: CompareAnalysis | None = None  # 아직 없으면 None (프론트가 숫자 규칙 문장을 만든다)
    notes: list[str] = []  # 비교할 때 알아야 할 점 (쉬운 말)
