from pydantic import BaseModel

from app.schemas.common import SampleValue


class CompanySummary(BaseModel):
    stock_code: str
    corp_code: str
    name: str
    name_en: str
    sector: str
    market: str
    current_price: SampleValue
    change_rate: SampleValue
    has_report: bool


class BusinessSegment(BaseModel):
    code: str
    name: str
    description: str


class TimelineItem(BaseModel):
    year: str
    title: str
    description: str
    is_plan: bool


class CompanyDetail(CompanySummary):
    market_cap: SampleValue
    homepage_url: str | None
    dart_url: str
    business_description: str
    business_segments: list[BusinessSegment]
    timeline: list[TimelineItem]
    timeline_source: str | None
