"""낱말퀴즈 완료 보상: 퀴즈 3개를 [정답 보기] 없이 풀면 AI 리포트 만들기 1회 추가.

규칙은 프론트 `src/config/usagePolicy.ts` 의 `PUZZLE_REWARD` 와 같다 (임시값, 확정 필요).
프론트 계산은 화면에 바로 보여주기 위한 것이고, 보상은 여기서 판단한 값이 기준이다.
- "오늘"은 서버 날짜(KST)다. 요청의 `completed_at` 은 기기 시각이라 기록만 하고 판단에 쓰지 않는다.
- 같은 퀴즈는 하루에 한 번만 센다. 없는 퀴즈 id 는 404.
- 같은 완료 기록(퀴즈 id·completed_at 이 같은 것)을 다시 보내면 한 번만 반영한다 (프론트가 실패하면 다시 보낸다).
"""

from datetime import UTC, datetime

from sqlmodel import Session

from app.models.learning import UsageCreditsRecord
from app.schemas.learning import PuzzleCompletion, UsageCredits
from app.services import data_store
from app.services.market_clock import KST

PUZZLES_PER_REWARD = 3  # 보상 1회에 필요한 퀴즈 수
DAILY_MAX_REWARDS = 2  # 하루 최대 보상 횟수
KEEP_PROGRESS_ACROSS_DAYS = True  # 날짜가 바뀌어도 센 퀴즈 수(예: 2/3)를 남긴다
KEEP_COMPLETIONS = 50  # 완료 기록은 최근 이만큼만 남긴다


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(KST)).astimezone(KST).date().isoformat()


def _roll_over(record: UsageCreditsRecord, today: str) -> None:
    """날짜가 바뀌었으면 오늘 보상 횟수를 0부터."""
    if record.day == today:
        return
    record.day = today
    record.rewards_today = 0
    record.counted_today = []
    if not KEEP_PROGRESS_ACROSS_DAYS:
        record.progress = 0


def _load(session: Session, user_id: int, today: str) -> UsageCreditsRecord:
    record = session.get(UsageCreditsRecord, user_id) or UsageCreditsRecord(
        user_id=user_id, day=today, updated_at=datetime.now(UTC)
    )
    _roll_over(record, today)
    return record


def _response(record: UsageCreditsRecord) -> UsageCredits:
    return UsageCredits(
        date=record.day,
        rewards_today=record.rewards_today,
        progress=record.progress,
        counted_today=record.counted_today,
        completions=[PuzzleCompletion(**c) for c in record.completions],
    )


def get_credits(session: Session, user_id: int, now: datetime | None = None) -> UsageCredits:
    return _response(_load(session, user_id, _today(now)))


def complete_puzzle(
    session: Session, user_id: int, completion: PuzzleCompletion, now: datetime | None = None
) -> UsageCredits:
    data_store.get_crossword(completion.puzzle_id)  # 없는 퀴즈면 404
    record = _load(session, user_id, _today(now))
    entry = completion.model_dump()
    if entry not in record.completions:  # 다시 보낸 같은 기록은 한 번만 반영한다
        record.completions = [*record.completions, entry][-KEEP_COMPLETIONS:]
        counts = (
            not completion.revealed
            and completion.puzzle_id not in record.counted_today
            and record.rewards_today < DAILY_MAX_REWARDS
        )
        if counts:
            record.counted_today = [*record.counted_today, completion.puzzle_id]
            record.progress += 1
            if record.progress >= PUZZLES_PER_REWARD:
                record.progress = 0
                record.rewards_today += 1
    record.updated_at = datetime.now(UTC)
    session.add(record)
    session.commit()
    return _response(record)
