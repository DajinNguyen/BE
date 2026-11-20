from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import current_user
from app.core.db import get_session
from app.core.rate_limit import rate_limit
from app.models.user import User
from app.schemas.mock_invest import (
    MockExchangeRequest,
    MockExchangeResult,
    MockOrderRequest,
    MockOrderResult,
    MockPortfolio,
    MockTradeItem,
)
from app.services import mock_invest_service

router = APIRouter(prefix="/api/me/mock-invest", tags=["mock-invest"])


@router.get("", response_model=MockPortfolio)
def get_portfolio(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 모의투자 자산·보유 종목. 가상 현금은 환전한 돈 + 판 돈 − 산 돈이다."""
    return mock_invest_service.get_portfolio(session, user.id)


@router.get("/trades", response_model=list[MockTradeItem])
def get_trades(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 모의투자 거래 내역 (최신 순, 최대 50개)."""
    return mock_invest_service.list_trades(session, user.id)


@router.post(
    "/orders",
    response_model=MockOrderResult,
    dependencies=[Depends(rate_limit("mock_order", 30))],
)
def post_order(
    body: MockOrderRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """사기·팔기 (`{ stock_code, side, quantity }`). 체결 가격은 서버가 지금 시세로 정한다.

    사면 가상 현금이 줄고 팔면 늘어난다 (앰똥깡은 그대로). 같은 주문을 연달아 보내면(3초 안) 한 번만 체결한다.
    """
    return mock_invest_service.place_order(
        session, user.id, body.stock_code, body.side, body.quantity
    )


@router.post(
    "/exchange",
    response_model=MockExchangeResult,
    dependencies=[Depends(rate_limit("mock_exchange", 30))],
)
def post_exchange(
    body: MockExchangeRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """앰똥깡 → 가상 현금 환전 (`{ adk }`, 정수). 1 ADK = 가상 1만 원, 되돌릴 수 없다."""
    return mock_invest_service.exchange(session, user.id, body.adk)
