from typing import Literal

from pydantic import BaseModel

from app.schemas.common import SampleValue


class CompanySummary(BaseModel):
    stock_code: str
    corp_code: str | None
    name: str
    name_en: str
    sector: str
    market: Literal["KOSPI", "KOSDAQ"]
    current_price: SampleValue
    change_rate: SampleValue
    one_liner: str = ""
    market_alert: str | None = None


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
    """원본 자료가 없는 회사는 검색 목록 정보만 있고 나머지는 빈 값이다."""

    market_cap: SampleValue | None = None
    homepage_url: str = ""
    dart_url: str = "https://dart.fss.or.kr/"
    business_description: str = ""
    business_segments: list[BusinessSegment] = []
    timeline: list[TimelineItem] = []
    timeline_source: str = ""
