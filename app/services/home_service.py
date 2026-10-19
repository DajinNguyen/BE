"""메인 대시보드.

거래대금·떠오르는 기업·관심도 높은 기업은 시세 연동과 집계 기준이 정해지기 전까지
`app/data/home.json` 의 예시 목록을 쓴다. AI 한 줄 결론과 "AI가 분석한 기업"은 실제로 저장된
리포트에서 뽑는다. 업종 탭은 시드 회사 목록 순서(대략 시가총액 순)로 묶는다.
"""

from datetime import datetime

from sqlmodel import Session

from app.schemas.common import SampleValue
from app.schemas.company import SECTOR_GROUPS, CompanySummary, SectorGroup
from app.schemas.home import AnalyzedCompany, CompanyRanking, HomeDashboard, TradingCompany
from app.services import report_service
from app.services.data_store import all_companies, companies_by_codes, get_company, load_json
from app.services.market_clock import MARKET_CLOSE, last_close_date

AI_ANALYZED_LIMIT = 6
TRADING_VALUE_LIMIT = 30


def _ranking(data: dict) -> CompanyRanking:
    return CompanyRanking(criteria=data["criteria"], items=companies_by_codes(data["stock_codes"]))


def _trading_value_top(session: Session, rows: list[dict]) -> list[TradingCompany]:
    rows = sorted(rows, key=lambda r: r["trading_value"], reverse=True)[:TRADING_VALUE_LIMIT]
    codes = [r["stock_code"] for r in rows]
    companies: dict[str, CompanySummary] = {c.stock_code: c for c in companies_by_codes(codes)}
    headlines = report_service.latest_headlines(session, codes)
    return [
        TradingCompany(
            **companies[r["stock_code"]].model_dump(),
            trading_value=SampleValue(value=r["trading_value"], is_sample=True),
            ai_headline=headlines.get(r["stock_code"]),
        )
        for r in rows
        if r["stock_code"] in companies
    ]


def _sector_companies() -> dict[SectorGroup, list[CompanySummary]]:
    groups: dict[SectorGroup, list[CompanySummary]] = {g: [] for g in SECTOR_GROUPS}
    for company in all_companies():
        groups[company.sector_group].append(company)
    return groups


def get_home(session: Session, now: datetime | None = None) -> HomeDashboard:
    data = load_json("home.json")
    reports = report_service.list_generated(session, AI_ANALYZED_LIMIT)
    return HomeDashboard(
        base_time=f"{last_close_date(now):%Y-%m-%d} {MARKET_CLOSE:%H:%M}",  # 직전 거래일 장 마감
        trading_value_top=_trading_value_top(session, data["trading_value_top"]),
        sector_companies=_sector_companies(),
        rising=_ranking(data["rising"]),
        most_viewed=_ranking(data["most_viewed"]),
        ai_analyzed=[
            AnalyzedCompany(
                stock_code=r.stock_code,
                name=get_company(r.stock_code).name,
                headline=r.headline,
                generated_at=r.generated_at.isoformat(),
            )
            for r in reports
            if r.generated_at is not None
        ],
    )
