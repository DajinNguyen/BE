from typing import Literal, get_args

from pydantic import BaseModel

from app.schemas.common import SampleValue

# 메인 화면 업종 탭 묶음. 탭 이름·순서는 프론트 상수로 고정이고, 어디에도 안 맞으면 "etc".
SectorGroup = Literal["semiconductor", "auto", "internet", "finance", "food", "etc"]
SECTOR_GROUPS: tuple[SectorGroup, ...] = get_args(SectorGroup)


class CompanySummary(BaseModel):
    stock_code: str
    corp_code: str | None
    name: str
    name_en: str
    sector: str  # 화면에 그대로 보여주는 자세한 업종 이름
    sector_group: SectorGroup
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
    price_as_of: str | None = None  # 현재가·등락률 기준일 (YYYY-MM-DD, 직전 장 마감일)
