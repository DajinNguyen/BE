"""주가 흐름. pykrx/FinanceDataReader 연동 전까지는 결정적인 예시 데이터를 만든다."""

import random
from datetime import date, timedelta

from app.schemas.price import Period, PriceHistory, PricePoint
from app.services.data_store import get_company

PERIOD_DAYS: dict[str, int] = {"1m": 30, "3m": 91, "6m": 182, "1y": 365}


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
    end = today or date.today()
    days = _business_days(end, PERIOD_DAYS[period])

    # 같은 종목·기간·날짜면 항상 같은 모양이 나오도록 시드를 고정하고,
    # 마지막 종가가 현재가(예시 값)와 맞도록 뒤에서부터 거슬러 올라가며 만든다.
    rng = random.Random(f"{stock_code}:{period}:{end.isoformat()}")
    price = float(company.current_price.value)
    closes = [price]
    for _ in range(len(days) - 1):
        price = max(price / (1 + rng.gauss(0, 0.015)), 1)
        closes.append(price)
    closes.reverse()

    points = [PricePoint(date=d, close=round(c)) for d, c in zip(days, closes, strict=True)]
    return PriceHistory(stock_code=stock_code, period=period, is_sample=True, points=points)
