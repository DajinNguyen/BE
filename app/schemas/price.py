from datetime import date
from typing import Literal

from pydantic import BaseModel

Period = Literal["1m", "3m", "6m", "1y"]


class PricePoint(BaseModel):
    """하루(1년은 한 주) 캔들. 단위는 원."""

    date: date
    open: int
    high: int
    low: int
    close: int


class PriceHistory(BaseModel):
    stock_code: str
    period: Period
    is_sample: bool
    points: list[PricePoint]
