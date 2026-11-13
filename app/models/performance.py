from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class CompanyPerformanceRecord(SQLModel, table=True):
    """회사별 "왜 이렇게 됐나요?" 1건 (그 분기 실적 기사를 AI가 쉬운 말로 정리한 것).

    숫자는 저장하지 않는다 (DART에서 그때그때 계산). content 는 `PerformanceReason` 목록.
    """

    __tablename__ = "company_performance"

    stock_code: str = Field(primary_key=True, max_length=6)
    status: str = Field(max_length=20)  # generating | done | failed
    content: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    rcept_no: str | None = Field(default=None, max_length=20)  # 근거 분기 보고서 접수번호
    started_at: datetime | None = None
    updated_at: datetime | None = None
