from datetime import date
from typing import Literal

from pydantic import BaseModel

# 그래프에 보여 줄 기간 (1d·5d는 마지막 1·5 거래일)
Period = Literal["1d", "5d", "1m", "3m", "6m", "1y", "5y", "all"]
# 캔들 하나의 길이
# 60min: 1시간, halfday: 반나절 (09:00~12:00 · 12:00~장 마감)
Interval = Literal["1min", "10min", "60min", "halfday", "day", "week", "month", "year"]


class PricePoint(BaseModel):
    date: date
    # 분·시간 캔들("1min", "10min", "60min", "halfday")만 "HH:MM", 나머지는 null
    time: str | None = None
    open: int
    high: int
    low: int
    close: int
    # 거래량 (주). 예시 데이터는 null
    volume: int | None = None


class PriceHistory(BaseModel):
    stock_code: str
    period: Period
    interval: Interval
    is_sample: bool
    points: list[PricePoint]
