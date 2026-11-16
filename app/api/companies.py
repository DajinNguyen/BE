from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlmodel import Session

from app.api.deps import get_overview_writer
from app.core.db import get_session, new_session
from app.schemas.company import CompanyDetail, CompanySummary
from app.schemas.compare import CompanyCompare
from app.schemas.financials import Financials
from app.schemas.news import NewsPage
from app.schemas.performance import PerformanceDetail
from app.schemas.price import Period, PriceHistory
from app.services import (
    compare_service,
    data_store,
    overview_service,
    performance_service,
    price_service,
    segment_service,
)
from app.services.compare_service import CompareWriter
from app.services.overview_service import OverviewWriter
from app.services.performance_service import ReasonWriter
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


# "/compare" 가 "/{stock_code}" 보다 먼저 와야 한다
@router.get("/compare", response_model=CompanyCompare)
def compare_companies(
    background_tasks: BackgroundTasks,
    left: str = Query(..., description="비교할 기업의 종목코드"),
    right: str = Query(..., description="지금 보는 기업의 종목코드"),
    session: Session = Depends(get_session),
    writer: CompareWriter | None = Depends(compare_service.get_compare_writer),
):
    """두 회사의 매출·영업이익·영업이익률·매출 증가율·부채비율을 같은 연도·같은 기준으로 맞춰서 준다.

    한눈에 비교(`analysis`)는 처음 비교할 때 백그라운드에서 만들기 시작하고, 다음 요청부터 채워진다.
    """
    result, year = compare_service.compare(left.strip(), right.strip())
    if writer is not None and compare_service.start_analysis(session, result, year):
        background_tasks.add_task(
            compare_service.run_analysis, left.strip(), right.strip(), writer, new_session
        )
    return compare_service.apply_analysis(session, result, year)


@router.get("/{stock_code}", response_model=CompanyDetail)
def get_company(
    stock_code: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    writer: OverviewWriter | None = Depends(get_overview_writer),
):
    """시드에 개요가 없는 회사는 AI 개요(사업보고서 정리)를 채운다.

    아직 없거나 새 사업보고서가 나왔으면 백그라운드에서 만들기 시작하고, 다음 요청부터 채워진다.
    """
    company = data_store.get_company_detail(stock_code)
    # 개요를 채우기 전의 시드 값으로 만들지 정한다 (채운 뒤에는 시드에 개요가 있는 것처럼 보인다)
    if writer is not None and overview_service.start_generation(session, company):
        background_tasks.add_task(overview_service.run_generation, stock_code, writer, new_session)
    return overview_service.apply_overview(session, company)


@router.get("/{stock_code}/peers", response_model=list[CompanySummary])
def get_peers(
    stock_code: str,
    limit: int = Query(compare_service.PEERS_DEFAULT, ge=1, le=compare_service.PEERS_MAX),
):
    """비교해 볼 만한 회사: 같은 산업이고 시가총액이 비슷한 회사 (가까운 순, 자기 자신 제외)."""
    return compare_service.peers(stock_code, limit)


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


@router.get("/{stock_code}/performance", response_model=PerformanceDetail)
def get_performance(
    stock_code: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    writer: ReasonWriter | None = Depends(performance_service.get_reason_writer),
):
    """재무 상세 "최근 사업 성과 분석". 실적 자료가 없으면 404 `DATA_NOT_FOUND`.

    "왜 이렇게 됐나요?"는 백그라운드에서 만들고, 다음 요청부터 채워진다.
    """
    detail, rcept_no = performance_service.get_detail(session, stock_code)
    if writer is not None and performance_service.start_generation(session, stock_code, rcept_no):
        background_tasks.add_task(
            performance_service.run_generation, stock_code, writer, new_session
        )
    return detail


@router.get("/{stock_code}/prices", response_model=PriceHistory)
def get_prices(stock_code: str, period: Period = Query("3m")):
    return price_service.get_price_history(stock_code, period)


@router.get("/{stock_code}/news", response_model=NewsPage)
async def get_news(
    stock_code: str,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
):
    """최신순. 기사가 없으면 `items: []`."""
    return await data_store.get_news_page(stock_code, page, size)
