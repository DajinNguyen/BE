from datetime import date
from typing import Literal

from pydantic import BaseModel

Period = Literal["1m", "3m", "6m", "1y"]


class PricePoint(BaseModel):
    date: date
    close: int


class PriceHistory(BaseModel):
    stock_code: str
    period: Period
    is_sample: bool
    points: list[PricePoint]
