from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class WatchlistRecord(SQLModel, table=True):
    """사용자별 관심 기업 목록 (추가한 순서대로)."""

    __tablename__ = "watchlists"

    user_id: int = Field(primary_key=True, foreign_key="users.id")
    stock_codes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    updated_at: datetime
