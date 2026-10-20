from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class ReportRecord(SQLModel, table=True):
    """회사별 AI 리포트 1건: 마지막으로 성공한 내용 + 지금 상태.

    다시 만드는 중이거나 실패해도 content(이전 내용)는 지우지 않는다.
    """

    __tablename__ = "ai_reports"

    stock_code: str = Field(primary_key=True, max_length=6)
    status: str = Field(max_length=20)  # generating | done | failed ("none"은 행이 없는 상태)
    content: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    generated_at: datetime | None = None  # content를 만든 시각
    started_at: datetime | None = None  # 지금 진행 중인 생성을 시작한 시각
    failure_reason: str | None = None
