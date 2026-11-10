from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlmodel import Session

from app.api.deps import get_overview_writer
from app.core.db import get_session, new_session
from app.schemas.company import CompanyDetail, CompanySummary
from app.schemas.financials import Financials
from app.schemas.news import NewsPage
from app.schemas.price import Period, PriceHistory
from app.services import data_store, overview_service, price_service, segment_service
from app.services.overview_service import OverviewWriter
from app.services.segment_service import SegmentWriter

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("", response_model=list[CompanySummary])
def search_companies(
    query: str = Query("", description="회사 이름(한/영) 또는 종목코드"),
    limit: int = Query(20, ge=1, le=20, description="검색 결과 최대 개수"),
    stock_codes: str | None = Query(None, description="쉼표로 구분한 종목코드 (관심 기업 목록용)"),
):
    if stock_codes is not None:
        return data_store.companies_by_codes([c for c in stock_codes.split(",") if c])
    return data_store.search_companies(query, limit)


@router.get("/{stock_code}", response_model=CompanyDetail)
def get_company(
    stock_code: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    writer: OverviewWriter | None = Depends(get_overview_writer),
):
    """시드에 개요가 없는 회사는 AI 개요(사업보고서 정리)를 채운다.

    아직 없으면 백그라운드에서 만들기 시작하고, 다음 요청부터 채워진다.
    """
    company = overview_service.apply_overview(session, data_store.get_company_detail(stock_code))
    if writer is not None and overview_service.start_generation(session, company):
        background_tasks.add_task(overview_service.run_generation, stock_code, writer, new_session)
    return company


@router.get("/{stock_code}/financials", response_model=Financials)
def get_financials(
    stock_code: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    writer: SegmentWriter | None = Depends(segment_service.get_segment_writer),
):
    """시드에 사업별 매출이 없는 회사는 사업보고서 표에서 뽑은 값으로 채운다.

    아직 없거나 새 사업보고서가 나왔으면 백그라운드에서 만들기 시작하고, 다음 요청부터 채워진다.
    """
    financials = data_store.get_financials(stock_code)
    corp_code = data_store.get_company(stock_code).corp_code
    if writer is not None and segment_service.start_generation(session, financials, corp_code):
        background_tasks.add_task(segment_service.run_generation, stock_code, writer, new_session)
    return segment_service.apply_segments(session, financials)


@router.get("/{stock_code}/prices", response_model=PriceHistory)
def get_prices(stock_code: str, period: Period = Query("3m")):
    return price_service.get_price_history(stock_code, period)


@router.get("/{stock_code}/news", response_model=NewsPage)
def get_news(
    stock_code: str,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
):
    """최신순. 기사가 없으면 `items: []`."""
    return data_store.get_news_page(stock_code, page, size)
