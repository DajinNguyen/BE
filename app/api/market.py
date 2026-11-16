from datetime import date

from fastapi import APIRouter, Query

from app.core.errors import invalid_param
from app.schemas.market import MarketSummary
from app.schemas.news import NewsArticle
from app.services import market_service

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/summary", response_model=MarketSummary)
def get_market_summary():
    """메인 "오늘의 시장": 지수·한마디·움직인 이유."""
    return market_service.get_summary()


@router.get("/news", response_model=list[NewsArticle])
def get_market_news(date_text: str = Query(..., alias="date", description="YYYY-MM-DD")):
    """시장 기사 (최신순). 기사가 없으면 빈 배열."""
    try:
        date.fromisoformat(date_text)
    except ValueError:
        raise invalid_param("날짜는 YYYY-MM-DD 형식으로 보내 주세요.") from None
    return market_service.latest_market_news()
