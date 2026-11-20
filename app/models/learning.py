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


class PuzzleOpenRecord(SQLModel, table=True):
    """로그인 사용자가 낱말퀴즈를 마지막으로 연 시각 (퀴즈를 열지 않고 보낸 완료 요청을 거르는 데 쓴다)."""

    __tablename__ = "puzzle_open"

    user_id: int = Field(primary_key=True, foreign_key="users.id")
    puzzle_id: str = Field(primary_key=True, max_length=100)
    opened_at: datetime
    # 오늘의 풀이 상태 (서버 검증용). 새 칸이라 비워 둘 수 있다 — 기존 행은 None 이고, 다음에 열 때 채워진다.
    attempt_day: str | None = Field(default=None, max_length=10)  # 이 풀이를 시작한 날 (KST)
    attempt_started_at: datetime | None = None  # 오늘 처음 연 시각
    attempt_calls: int | None = None  # 오늘 부른 확인·힌트 수 (정답 추측을 막는 데 쓴다)
    attempt_revealed: bool | None = None  # 오늘 [정답 보기]를 썼는지
