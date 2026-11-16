from fastapi import APIRouter, Query

from app.schemas.industry import HeatPeriod, IndustryDetail, IndustrySummary
from app.schemas.news import NewsArticle
from app.services import industry_service

router = APIRouter(prefix="/api/industries", tags=["industries"])


# "/hot" 이 "/{industry_id}" 보다 먼저 와야 한다
@router.get("/hot", response_model=list[IndustrySummary])
def get_hot_industries(
    period: HeatPeriod = Query("1w", description="1w: 최근 1주, 1m: 최근 1개월")
):
    """메인 "지금 관심이 몰리는 산업". 점수 높은 순."""
    return industry_service.hot_industries(period)


@router.get("/{industry_id}", response_model=IndustryDetail)
async def get_industry(industry_id: str):
    return await industry_service.get_industry(industry_id)


@router.get("/{industry_id}/news", response_model=list[NewsArticle])
async def get_industry_news(industry_id: str, size: int = Query(5, ge=1, le=20)):
    """최신순. 기사가 없으면 빈 배열."""
    return await industry_service.industry_news(industry_id, size)
