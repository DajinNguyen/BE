"""메인 대시보드.

거래대금·떠오르는 기업·관심도 높은 기업은 시세 연동과 집계 기준이 정해지기 전까지
`app/data/home.json` 의 예시 목록을 쓴다. "AI가 분석한 기업"만 실제로 저장된 리포트에서 뽑는다.
"""

from datetime import datetime

from sqlmodel import Session

from app.schemas.common import SampleValue
from app.schemas.company import CompanySummary
from app.schemas.home import AnalyzedCompany, CompanyRanking, HomeDashboard, TradingCompany
from app.services import report_service
from app.services.data_store import companies_by_codes, get_company, load_json

AI_ANALYZED_LIMIT = 6


def _ranking(data: dict) -> CompanyRanking:
    return CompanyRanking(criteria=data["criteria"], items=companies_by_codes(data["stock_codes"]))


def _trading_value_top(rows: list[dict]) -> list[TradingCompany]:
    companies: dict[str, CompanySummary] = {
        c.stock_code: c for c in companies_by_codes([r["stock_code"] for r in rows])
    }
    return [
        TradingCompany(
            **companies[r["stock_code"]].model_dump(),
            trading_value=SampleValue(value=r["trading_value"], is_sample=True),
        )
        for r in rows
        if r["stock_code"] in companies
    ]


def get_home(session: Session, now: datetime | None = None) -> HomeDashboard:
    data = load_json("home.json")
    now = now or datetime.now(report_service.KST)
    reports = report_service.list_generated(session, AI_ANALYZED_LIMIT)
    return HomeDashboard(
        base_time=f"{now:%Y-%m-%d} 15:30",  # 장 마감 기준 (예시 값)
        trading_value_top=_trading_value_top(data["trading_value_top"]),
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
