from fastapi import APIRouter, Query

from app.schemas.compare import Comparison
from app.services import compare_service

router = APIRouter(prefix="/api/compare", tags=["compare"])


@router.get("", response_model=Comparison)
def compare(
    stock_codes: str = Query(..., description="쉼표로 구분한 종목코드 두 개 (예: 005930,000660)"),
):
    """두 회사의 매출·영업이익·영업이익률·매출 증가율·부채비율을 같은 연도·같은 기준으로 맞춰서 준다."""
    return compare_service.compare([c.strip() for c in stock_codes.split(",") if c.strip()])
