from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class CompanyOverviewRecord(SQLModel, table=True):
    """회사별 AI 개요 1건 (사업보고서 "사업의 내용"을 쉬운 말로 정리한 것).

    시드에 개요가 없는 회사만 만든다. 실패하면 content 는 비어 있고 잠시 뒤 다시 시도한다.
    """

    __tablename__ = "company_overviews"

    stock_code: str = Field(primary_key=True, max_length=6)
    status: str = Field(max_length=20)  # generating | done | failed
    content: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    source: str | None = None  # 예: "SK하이닉스 2025년 사업보고서 (2026년 3월 공시)"
    rcept_no: str | None = Field(default=None, max_length=20)  # 근거 사업보고서 접수번호
    started_at: datetime | None = None
    updated_at: datetime | None = None
    failure_reason: str | None = None


class CompanySegmentsRecord(SQLModel, table=True):
    """회사별 사업부문 매출 1건 (사업보고서 "주요 제품"·"매출실적" 표에서 뽑은 것).

    시드에 사업별 매출이 없는 회사만 만든다. content 는 `Segments` (조 원 단위).
    """

    __tablename__ = "company_segments"

    stock_code: str = Field(primary_key=True, max_length=6)
    status: str = Field(max_length=20)  # generating | done | failed
    content: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    rcept_no: str | None = Field(default=None, max_length=20)  # 근거 사업보고서 접수번호
    started_at: datetime | None = None
    updated_at: datetime | None = None
    failure_reason: str | None = None
