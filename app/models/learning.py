from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class TermProgressRecord(SQLModel, table=True):
    """사용자별 용어 학습 기록 1건. content 는 `TermProgress` 전체 (프론트가 한 번에 저장한다)."""

    __tablename__ = "term_progress"

    user_id: int = Field(primary_key=True, foreign_key="users.id")
    content: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    updated_at: datetime


class UsageCreditsRecord(SQLModel, table=True):
    """사용자별 낱말퀴즈 보상 기록 1건 (오늘 받은 보상, 다음 보상까지 센 퀴즈, 최근 완료 기록)."""

    __tablename__ = "usage_credits"

    user_id: int = Field(primary_key=True, foreign_key="users.id")
    day: str = Field(max_length=10)  # rewards_today·counted_today 의 기준 날짜 (서버 날짜, KST)
    rewards_today: int = 0
    progress: int = 0  # 다음 보상까지 센 퀴즈 수
    counted_today: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    completions: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    updated_at: datetime
