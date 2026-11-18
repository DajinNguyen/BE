"""모의투자 응답. 프론트 `frontend/src/types/mockInvest.ts` 와 같다. 금액은 모두 가상 원."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.wallet import Adk


class MockHolding(BaseModel):
    stock_code: str
    name: str
    quantity: int
    avg_price: int  # 평균 산 가격 (원)
    current_price: int  # 지금 가격 (지연 시세, 원)
    change_rate: float  # 오늘 등락률 (%)
    evaluation: int  # 평가금액 = current_price × quantity
    profit: int  # 평가손익 = evaluation − 산 금액
    profit_rate: float  # 수익률 (%)


class MockPortfolio(BaseModel):
    started: bool  # 한 번이라도 사고판 적이 있는지
    adk_to_won: int  # 1 ADK = 가상 몇 원
    cash_adk: Adk  # 가상 현금 = 앰똥깡 잔액 (GET /api/me/wallet 의 balance 와 같다)
    cash: int  # 가상 현금 (원)
    evaluation: int  # 평가금액 합 (원)
    total_asset: int  # 총 자산 = cash + evaluation
    profit: int  # 평가손익 합 (원)
    profit_rate: float | None  # 보유 종목 수익률 (%). 보유 종목이 없으면 None
    today_change: int  # 보유 종목 평가금액의 오늘 변화 (원)
    price_as_of: str | None  # 지연 시세 기준 시각 ("2026-11-18 10:20")
    holdings: list[MockHolding]


class MockTradeItem(BaseModel):
    id: str
    stock_code: str
    name: str
    side: Literal["buy", "sell"]
    quantity: int
    price: int  # 체결 가격 (원)
    amount: int  # 체결 금액 (원)
    amount_adk: Adk  # 빠지거나 돌아온 ADK
    created_at: datetime


class MockOrderRequest(BaseModel):
    stock_code: str
    side: Literal["buy", "sell"]
    # 정수 1~최대 수량이 아니면 422 대신 400 `INVALID_QUANTITY` 로 알려주려고 여기서는 받기만 한다
    quantity: Any = None


class MockOrderResult(BaseModel):
    trade: MockTradeItem
    balance: Adk  # 주문 뒤 앰똥깡 잔액
    portfolio: MockPortfolio
