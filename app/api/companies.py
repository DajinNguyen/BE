from fastapi import APIRouter, Query

from app.schemas.company import CompanyDetail, CompanySummary
from app.schemas.financials import Financials
from app.schemas.news import NewsItem
from app.schemas.price import Period, PriceHistory
from app.services import data_store, price_service

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("", response_model=list[CompanySummary])
def search_companies(query: str = Query("", description="회사 이름(한/영) 또는 종목코드")):
    return data_store.search_companies(query)


@router.get("/popular", response_model=list[CompanySummary])
def popular_companies():
    return data_store.popular_companies()


@router.get("/{stock_code}", response_model=CompanyDetail)
def get_company(stock_code: str):
    return data_store.get_company(stock_code)


@router.get("/{stock_code}/financials", response_model=Financials)
def get_financials(stock_code: str):
    return data_store.get_financials(stock_code)


@router.get("/{stock_code}/prices", response_model=PriceHistory)
def get_prices(stock_code: str, period: Period = Query("3m")):
    return price_service.get_price_history(stock_code, period)


@router.get("/{stock_code}/news", response_model=list[NewsItem])
def get_news(stock_code: str):
    return data_store.get_news(stock_code)
