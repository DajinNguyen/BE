"""주가 흐름. 네이버 시세를 받지 못하면 결정적인 예시 데이터를 만든다."""

import random
from datetime import date, datetime, timedelta

from app.schemas.price import Period, PriceHistory, PricePoint
from app.services import naver
from app.services.data_store import get_company
from app.services.market_clock import KST, last_close_date

PERIOD_DAYS: dict[str, int] = {"1m": 30, "3m": 91, "6m": 182, "1y": 365}


def _weekly_days(end: date, days: int) -> list[date]:
    """1년은 주 단위로 보낸다 (약 52개)."""
    return [end - timedelta(weeks=w) for w in range(days // 7, -1, -1)]


def _business_days(end: date, days: int) -> list[date]:
    start = end - timedelta(days=days)
    result = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def get_price_history(stock_code: str, period: Period, today: date | None = None) -> PriceHistory:
    company = get_company(stock_code)
    weekly = period == "1y"

    live_end = today or datetime.now(KST).date()
    live = naver.candles(
        stock_code, live_end - timedelta(days=PERIOD_DAYS[period]), live_end, weekly
    )
    if live:
        points = [
            PricePoint(date=c.date, open=c.open, high=c.high, low=c.low, close=c.close)
            for c in live
        ]
        return PriceHistory(stock_code=stock_code, period=period, is_sample=False, points=points)

    end = today or last_close_date()  # 마지막 값은 직전 장 마감일 종가 (실시간 아님)
    days = (_weekly_days if weekly else _business_days)(end, PERIOD_DAYS[period])
    volatility = 0.032 if weekly else 0.015  # 주 단위는 한 칸 변동폭이 더 크다

    # 같은 종목·기간·날짜면 항상 같은 모양이 나오도록 시드를 고정하고,
    # 마지막 종가가 현재가(예시 값)와 맞도록 뒤에서부터 거슬러 올라가며 만든다.
    rng = random.Random(f"{stock_code}:{period}:{end.isoformat()}")
    price = float(company.current_price.value)
    closes = [price]
    for _ in range(len(days) - 1):
        price = max(price / (1 + rng.gauss(0, volatility)), 1)
        closes.append(price)
    closes.reverse()

    # 시가는 전날 종가 근처에서, 고가·저가는 시가·종가 바깥으로 조금씩 벌려 만든다.
    points = []
    prev = closes[0] / (1 + rng.gauss(0, volatility))
    for d, close in zip(days, closes, strict=True):
        open_ = prev * (1 + rng.gauss(0, volatility * 0.3))
        high = max(open_, close) * (1 + abs(rng.gauss(0, volatility * 0.5)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, volatility * 0.5)))
        points.append(
            PricePoint(
                date=d, open=round(open_), high=round(high), low=round(low), close=round(close)
            )
        )
        prev = close
    return PriceHistory(stock_code=stock_code, period=period, is_sample=True, points=points)
