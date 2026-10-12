from fastapi import APIRouter, Query

from app.schemas.company import CompanyDetail, CompanySummary
from app.schemas.financials import Financials
from app.schemas.news import NewsItem
from app.schemas.price import Period, PriceHistory
from app.services import data_store, price_service

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
def get_company(stock_code: str):
    return data_store.get_company_detail(stock_code)


@router.get("/{stock_code}/financials", response_model=Financials)
def get_financials(stock_code: str):
    return data_store.get_financials(stock_code)


@router.get("/{stock_code}/prices", response_model=PriceHistory)
def get_prices(stock_code: str, period: Period = Query("3m")):
    return price_service.get_price_history(stock_code, period)


@router.get("/{stock_code}/news", response_model=list[NewsItem])
def get_news(stock_code: str):
    return data_store.get_news(stock_code)
