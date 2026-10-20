"""메인 대시보드.

거래대금 순위는 네이버 시세로 다진이 다루는 회사(시드 목록) 안에서 매긴다. 시세를 받지 못하면
`app/data/home.json` 의 예시 목록을 쓴다. 떠오르는 기업·관심도 높은 기업은 집계 기준이 정해지기
전까지 예시 목록이다. AI 한 줄 결론과 "AI가 분석한 기업"은 실제로 저장된 리포트에서 뽑는다.
업종 탭은 시가총액 순 (시세를 받지 못하면 시드 목록 순서)으로 묶는다.
"""

from datetime import datetime

from sqlmodel import Session

from app.schemas.common import SampleValue
from app.schemas.company import SECTOR_GROUPS, CompanySummary, SectorGroup
from app.schemas.home import AnalyzedCompany, CompanyRanking, HomeDashboard, TradingCompany
from app.services import naver, report_service
from app.services.data_store import all_companies, companies_by_codes, get_company, load_json
from app.services.market_clock import KST, MARKET_CLOSE, last_close_date

AI_ANALYZED_LIMIT = 6
TRADING_VALUE_LIMIT = 30


def _ranking(data: dict) -> CompanyRanking:
    return CompanyRanking(criteria=data["criteria"], items=companies_by_codes(data["stock_codes"]))


def _live_trading_rows(quotes: dict[str, naver.Quote]) -> list[dict]:
    return [
        {"stock_code": code, "trading_value": round(q.trading_value / 1e8)}  # 원 → 억 원
        for code, q in quotes.items()
    ]


def _trading_value_top(session: Session, rows: list[dict], is_sample: bool) -> list[TradingCompany]:
    rows = sorted(rows, key=lambda r: r["trading_value"], reverse=True)[:TRADING_VALUE_LIMIT]
    codes = [r["stock_code"] for r in rows]
    companies: dict[str, CompanySummary] = {c.stock_code: c for c in companies_by_codes(codes)}
    headlines = report_service.latest_headlines(session, codes)
    return [
        TradingCompany(
            **companies[r["stock_code"]].model_dump(),
            trading_value=SampleValue(value=r["trading_value"], is_sample=is_sample),
            ai_headline=headlines.get(r["stock_code"]),
        )
        for r in rows
        if r["stock_code"] in companies
    ]


def _sector_companies(
    companies: list[CompanySummary], quotes: dict[str, naver.Quote]
) -> dict[SectorGroup, list[CompanySummary]]:
    def market_cap(c: CompanySummary) -> int:
        q = quotes.get(c.stock_code)
        return q.market_cap if q else 0

    groups: dict[SectorGroup, list[CompanySummary]] = {g: [] for g in SECTOR_GROUPS}
    for company in sorted(companies, key=market_cap, reverse=True):  # 시세가 없으면 시드 순서 유지
        groups[company.sector_group].append(company)
    return groups


def _base_time(quotes: dict[str, naver.Quote], now: datetime | None) -> str:
    """거래대금 기준 시각. 장중이면 마지막 체결 시각, 끝났으면 그날 장 마감."""
    if not quotes:
        return f"{last_close_date(now):%Y-%m-%d} {MARKET_CLOSE:%H:%M}"
    latest = max(q.traded_at for q in quotes.values()).astimezone(KST)
    shown = min(latest.time(), MARKET_CLOSE)
    return f"{latest:%Y-%m-%d} {shown:%H:%M}"


def get_home(session: Session, now: datetime | None = None) -> HomeDashboard:
    data = load_json("home.json")
    companies = all_companies()
    quotes = naver.quotes([c.stock_code for c in companies])
    trading_rows = _live_trading_rows(quotes) if quotes else data["trading_value_top"]
    reports = report_service.list_generated(session, AI_ANALYZED_LIMIT)
    return HomeDashboard(
        base_time=_base_time(quotes, now),
        trading_value_top=_trading_value_top(session, trading_rows, is_sample=not quotes),
        sector_companies=_sector_companies(companies, quotes),
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
