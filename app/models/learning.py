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
