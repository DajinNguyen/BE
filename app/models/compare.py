from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class CompareAnalysisRecord(SQLModel, table=True):
    """두 회사 비교의 AI 분석 1건. 어느 쪽이 left 든 같은 글을 쓴다 (pair 는 종목코드를 정렬해 이은 값)."""

    __tablename__ = "compare_analyses"

    pair: str = Field(primary_key=True, max_length=13)  # 예: "000660:005930"
    fiscal_year: int  # 이 글을 쓴 비교 기준 연도. 새 사업보고서로 연도가 바뀌면 다시 만든다
    status: str = Field(max_length=20)  # generating | done | failed
    content: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    started_at: datetime
