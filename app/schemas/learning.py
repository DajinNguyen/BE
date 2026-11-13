from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

TermStatus = Literal["new", "learning", "mastered"]

MAX_TERMS = 2000  # 용어 기록 수 상한 (용어 사전보다 넉넉하게)
MAX_SETS = 100
MAX_STUDY_DATES = 60  # 최근 60일


class TermProgressItem(BaseModel):
    status: TermStatus
    correct_count: int = Field(ge=0, le=100_000)
    wrong_count: int = Field(ge=0, le=100_000)
    last_studied_at: datetime | None
    set_id: str = Field(max_length=50)


class SetProgress(BaseModel):
    set_id: str = Field(max_length=50)
    mastered: int = Field(ge=0, le=MAX_TERMS)
    studied: int = Field(ge=0, le=MAX_TERMS)


class TermProgress(BaseModel):
    """로그인 사용자의 용어 학습 기록 (PUT 은 전체를 한 번에 저장)."""

    terms: dict[str, TermProgressItem] = Field(default_factory=dict, max_length=MAX_TERMS)
    set_progress: list[SetProgress] = Field(default_factory=list, max_length=MAX_SETS)
    study_dates: list[date] = Field(default_factory=list, max_length=MAX_STUDY_DATES)
