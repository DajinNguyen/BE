from pydantic import BaseModel, Field

MAX_WATCHLIST = 200


class Watchlist(BaseModel):
    """관심 기업 목록 (PUT 은 전체를 한 번에 저장)."""

    stock_codes: list[str] = Field(default_factory=list, max_length=MAX_WATCHLIST)
