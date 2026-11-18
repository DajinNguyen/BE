"""모의투자: 앰똥깡(ADK)을 가상 현금으로 써서 지연 시세로 사고판다.

- 가상 현금 = ADK 잔액 × `ADK_TO_WON`. 모의투자 전용 돈은 따로 없다 (초기화도 없다).
- 사면 지갑에서 ADK가 빠지고(spend), 팔면 돌아온다(earn). 그래서 모의투자에서 잃으면
  AI 리포트·챗봇에 쓸 ADK도 줄어든다.
- 체결 가격은 서버가 정한다: 주문을 받은 때의 시세(`GET /api/companies/{code}` 의 current_price 와 같은 값).
  장이 끝난 뒤·주말에는 마지막 체결가(그날 종가)로 체결한다.
- 보유 종목은 저장하지 않고 체결 내역을 더해서 구한다 (평균 산 가격은 이동평균).
"""

import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from app.core.errors import data_not_found, insufficient_quantity, invalid_quantity
from app.models.mock_invest import MockTrade
from app.schemas.company import CompanyDetail
from app.schemas.mock_invest import (
    MockHolding,
    MockOrderResult,
    MockPortfolio,
    MockTradeItem,
)
from app.services import data_store, naver, wallet_service

ADK_TO_WON = 10_000  # 1 ADK = 가상 1만 원
MAX_ORDER_QUANTITY = 10_000  # 한 번에 주문할 수 있는 최대 수량 (임시값)
TRADE_HISTORY_COUNT = 50
# 같은 주문(종목·사기/팔기·수량)이 이 시간 안에 다시 오면 두 번 누른 것으로 보고 한 번만 체결한다
DUPLICATE_WINDOW = timedelta(seconds=3)
REASON = "mock_invest"

# 같은 사용자의 주문이 겹치지 않게 한다 (잔액·보유 수량 확인 → 체결)
_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass
class _Position:
    stock_code: str
    name: str
    quantity: int = 0
    cost: float = 0.0  # 지금 가진 주식을 산 금액 (원)


def _trades(session: Session, user_id: int) -> list[MockTrade]:
    return list(
        session.exec(
            select(MockTrade)
            .where(MockTrade.user_id == user_id)
            .order_by(col(MockTrade.created_at), col(MockTrade.id))
        ).all()
    )


def _positions(trades: list[MockTrade]) -> dict[str, _Position]:
    """체결 내역을 순서대로 더해서 지금 가진 종목을 구한다. 다 판 종목은 빠진다."""
    positions: dict[str, _Position] = {}
    for t in trades:
        p = positions.setdefault(t.stock_code, _Position(t.stock_code, t.name))
        p.name = t.name
        if t.side == "buy":
            p.quantity += t.quantity
            p.cost += t.amount
        else:  # 팔면 평균 산 가격은 그대로, 판 만큼 산 금액이 줄어든다
            p.cost -= p.cost / p.quantity * t.quantity if p.quantity else 0
            p.quantity -= t.quantity
            if p.quantity <= 0:
                p.quantity, p.cost = 0, 0.0
    return {code: p for code, p in positions.items() if p.quantity > 0}


def _price(company: CompanyDetail) -> int:
    value = company.current_price.value
    if not value or value <= 0:
        raise data_not_found()
    return int(value)


def _as_of(codes: list[str]) -> str | None:
    """시세 기준 시각 ("2026-11-18 10:20"). 실시간 시세를 못 받았으면 None."""
    quotes = naver.quotes(codes) if codes else {}
    times = [q.traded_at for q in quotes.values()]
    return max(times).strftime("%Y-%m-%d %H:%M") if times else None


def _rate(profit: float, cost: float) -> float:
    return round(profit / cost * 100, 2) if cost else 0.0


def get_portfolio(session: Session, user_id: int) -> MockPortfolio:
    trades = _trades(session, user_id)
    positions = _positions(trades)
    companies = {
        c.stock_code: c for c in data_store.company_details_by_codes(list(positions))
    }  # 시세는 한 번에 받는다

    holdings: list[MockHolding] = []
    total_cost = 0.0
    today_change = 0.0
    for code, p in positions.items():
        company = companies.get(code)
        price = int(company.current_price.value) if company else round(p.cost / p.quantity)
        change_rate = float(company.change_rate.value) if company else 0.0
        evaluation = price * p.quantity
        profit = evaluation - p.cost
        total_cost += p.cost
        # 어제 종가 = 지금 가격 ÷ (1 + 등락률)
        today_change += evaluation - evaluation / (1 + change_rate / 100)
        holdings.append(
            MockHolding(
                stock_code=code,
                name=company.name if company else p.name,
                quantity=p.quantity,
                avg_price=round(p.cost / p.quantity),
                current_price=price,
                change_rate=change_rate,
                evaluation=evaluation,
                profit=round(profit),
                profit_rate=_rate(profit, p.cost),
            )
        )

    cash_adk = wallet_service.balance(session, user_id)
    cash = round(cash_adk * ADK_TO_WON)
    evaluation = sum(h.evaluation for h in holdings)
    profit = evaluation - total_cost
    return MockPortfolio(
        started=bool(trades),
        adk_to_won=ADK_TO_WON,
        cash_adk=cash_adk,
        cash=cash,
        evaluation=evaluation,
        total_asset=cash + evaluation,
        profit=round(profit),
        profit_rate=_rate(profit, total_cost) if holdings else None,
        today_change=round(today_change),
        price_as_of=_as_of(list(positions)),
        holdings=holdings,
    )


def _item(t: MockTrade) -> MockTradeItem:
    return MockTradeItem(
        id=str(t.id),
        stock_code=t.stock_code,
        name=t.name,
        side=t.side,  # type: ignore[arg-type]
        quantity=t.quantity,
        price=t.price,
        amount=t.amount,
        amount_adk=wallet_service.adk(t.amount_adk),
        created_at=_aware(t.created_at),
    )


def list_trades(session: Session, user_id: int) -> list[MockTradeItem]:
    trades = session.exec(
        select(MockTrade)
        .where(MockTrade.user_id == user_id)
        .order_by(col(MockTrade.created_at).desc(), col(MockTrade.id).desc())
        .limit(TRADE_HISTORY_COUNT)
    ).all()
    return [_item(t) for t in trades]


def _check_quantity(quantity: object) -> int:
    # bool 은 int 의 하위 타입이라 따로 막는다. 2.0 처럼 소수점이 붙은 값도 받지 않는다
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise invalid_quantity(MAX_ORDER_QUANTITY)
    if not 1 <= quantity <= MAX_ORDER_QUANTITY:
        raise invalid_quantity(MAX_ORDER_QUANTITY)
    return quantity


def _recent_duplicate(
    session: Session, user_id: int, stock_code: str, side: str, quantity: int
) -> MockTrade | None:
    last = session.exec(
        select(MockTrade)
        .where(MockTrade.user_id == user_id)
        .order_by(col(MockTrade.created_at).desc(), col(MockTrade.id).desc())
    ).first()
    if (
        last is not None
        and (last.stock_code, last.side, last.quantity) == (stock_code, side, quantity)
        and _now() - _aware(last.created_at) < DUPLICATE_WINDOW
    ):
        return last
    return None


def _title(name: str, quantity: int, side: str) -> str:
    return f"모의투자 · {name} {quantity:,}주 {'사기' if side == 'buy' else '팔기'}"


def place_order(
    session: Session, user_id: int, stock_code: str, side: str, quantity: object
) -> MockOrderResult:
    """사기·팔기. 체결 가격과 ADK 계산은 모두 서버가 한다.

    - 수량이 정수 1~`MAX_ORDER_QUANTITY` 가 아니면 400 `INVALID_QUANTITY`
    - 살 ADK가 모자라면 400 `INSUFFICIENT_BALANCE`, 가진 것보다 많이 팔면 400 `INSUFFICIENT_QUANTITY`
    - 없는 종목이면 404 `COMPANY_NOT_FOUND`
    """
    quantity = _check_quantity(quantity)
    company = data_store.get_company(stock_code)  # 없는 종목이면 404
    price = _price(company)

    with _locks[user_id]:
        trade = _recent_duplicate(session, user_id, stock_code, side, quantity)
        if trade is None:
            trade = _execute(session, user_id, company, side, quantity, price)

    return MockOrderResult(
        trade=_item(trade),
        balance=wallet_service.balance(session, user_id),
        portfolio=get_portfolio(session, user_id),
    )


def _execute(
    session: Session, user_id: int, company: CompanyDetail, side: str, quantity: int, price: int
) -> MockTrade:
    amount = price * quantity
    amount_adk = wallet_service.adk(amount / ADK_TO_WON)
    title = _title(company.name, quantity, side)
    ref = f"mock:{uuid.uuid4().hex}"
    trade = MockTrade(
        user_id=user_id,
        stock_code=company.stock_code,
        name=company.name,
        side=side,
        quantity=quantity,
        price=price,
        amount=amount,
        amount_adk=amount_adk,
        created_at=_now(),
    )

    if side == "buy":
        # 잔액이 모자라면 여기서 400 INSUFFICIENT_BALANCE (아무것도 저장되지 않는다)
        hold_id = wallet_service.hold(session, user_id, amount_adk, REASON, title, ref)
        session.add(trade)
        wallet_service.settle(session, hold_id)  # 체결과 ADK 확정을 한 번에 저장한다
    else:
        held = _positions(_trades(session, user_id)).get(company.stock_code)
        held_quantity = held.quantity if held else 0
        if quantity > held_quantity:
            raise insufficient_quantity(held_quantity)
        session.add(trade)
        wallet_service.grant(session, user_id, amount_adk, REASON, title, ref)

    session.commit()  # 위에서 이미 저장됐지만, 혹시 남은 것이 있으면 저장한다
    session.refresh(trade)
    return trade
