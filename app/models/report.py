from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class StoredReport(SQLModel, table=True):
    """회사별로 가장 최근에 생성한 AI 리포트 1건 (POST 시 덮어씀)."""

    __tablename__ = "company_reports"

    stock_code: str = Field(primary_key=True, max_length=6)
    generated_at: datetime
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
