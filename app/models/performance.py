from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class CompanyPerformanceRecord(SQLModel, table=True):
    """회사별 "왜 이렇게 됐나요?" 1건 (그 분기 실적 기사를 AI가 쉬운 말로 정리한 것).

    숫자는 저장하지 않는다 (DART에서 그때그때 계산). content 는 `PerformanceReason` 목록과 AI 한 줄 요약.
    """

    __tablename__ = "company_performance"

    stock_code: str = Field(primary_key=True, max_length=6)
    status: str = Field(max_length=20)  # generating | done | failed
    # {"reasons": [...], "summary": "..."} (예전 기록은 이유 목록만 있는 list)
    content: Any | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    rcept_no: str | None = Field(default=None, max_length=20)  # 근거 분기 보고서 접수번호
    started_at: datetime | None = None
    updated_at: datetime | None = None


class CompanyQuarterSegmentsRecord(SQLModel, table=True):
    """회사별 "사업별 성과" 1건 (분기 보고서 영업부문 주석에서 뽑은 그 분기 사업부문별 실적).

    content 는 {"kind": ..., "items": [{name, revenue, operating_income, prior_revenue, comment}]}.
    금액은 원 단위로 저장하고, 화면 단위(조 원·억 원)는 응답할 때 바꾼다.
    """

    __tablename__ = "company_quarter_segments"

    stock_code: str = Field(primary_key=True, max_length=6)
    status: str = Field(max_length=20)  # generating | done | failed
    content: Any | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    rcept_no: str | None = Field(default=None, max_length=20)  # 근거 분기 보고서 접수번호
    started_at: datetime | None = None
    updated_at: datetime | None = None
    failure_reason: str | None = None
