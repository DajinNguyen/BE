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


class Financials(BaseModel):
    stock_code: str
    unit: str
    basis: str
    source: str | None
    annual: list[AnnualResult]
    latest_quarter: LatestQuarter | None
    segments: Segments | None
    indicators: Indicators
