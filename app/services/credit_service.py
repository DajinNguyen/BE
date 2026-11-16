"""낱말퀴즈 완료 보상: 퀴즈 3개를 [정답 보기] 없이 풀 때마다 20 ADK.

규칙은 프론트 `src/config/usagePolicy.ts` 의 `PUZZLE_REWARD` 와 같다 (임시값, 확정 필요).
프론트 계산은 화면에 바로 보여주기 위한 것이고, 보상은 여기서 판단한 값이 기준이다.
- "오늘"은 서버 날짜(KST)다. 요청의 `completed_at` 은 기기 시각이라 기록만 하고 판단에 쓰지 않는다.
- 같은 퀴즈는 하루에 한 번만 센다. 없는 퀴즈 id 는 404.
- 퀴즈를 연 기록이 없거나(완료 요청만 보낸 경우) 연 지 너무 금방 완료한 퀴즈는 세지 않는다.
- 같은 퀴즈를 다시 보내도 하루에 한 번만 센다 (프론트가 실패하면 다시 보낸다).
"""

from datetime import UTC, datetime

from sqlmodel import Session

from app.models.learning import PuzzleOpenRecord, UsageCreditsRecord
from app.schemas.learning import PuzzleCompletion, UsageCredits
from app.schemas.wallet import PuzzleCompletionResult, PuzzleProgress
from app.services import data_store, wallet_service
from app.services.market_clock import KST

PUZZLES_PER_REWARD = 3  # 보상 1회에 필요한 퀴즈 수
DAILY_MAX_REWARDS = 2  # 하루 최대 보상 횟수
KEEP_PROGRESS_ACROSS_DAYS = True  # 날짜가 바뀌어도 센 퀴즈 수(예: 2/3)를 남긴다
MIN_SOLVE_SECONDS = 15  # 퀴즈를 열고 이보다 빨리 온 완료는 세지 않는다 (임시값)
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


def mark_opened(session: Session, user_id: int, puzzle_id: str) -> None:
    """로그인 사용자가 퀴즈를 열었다고 적는다. 이미 연 퀴즈면 처음 연 시각을 그대로 둔다.

    (다시 열 때마다 시각을 바꾸면, 오래 풀다가 새로고침한 사람이 "너무 빨리 완료"로 걸린다.)
    """
    if session.get(PuzzleOpenRecord, (user_id, puzzle_id)) is None:
        session.add(
            PuzzleOpenRecord(user_id=user_id, puzzle_id=puzzle_id, opened_at=datetime.now(UTC))
        )
        session.commit()


def _really_solved(session: Session, user_id: int, puzzle_id: str) -> bool:
    """퀴즈를 열었고, 풀 만한 시간이 지났는지."""
    opened = session.get(PuzzleOpenRecord, (user_id, puzzle_id))
    if opened is None:
        return False
    opened_at = (
        opened.opened_at if opened.opened_at.tzinfo else opened.opened_at.replace(tzinfo=UTC)
    )
    return (datetime.now(UTC) - opened_at).total_seconds() >= MIN_SOLVE_SECONDS


def get_credits(session: Session, user_id: int, now: datetime | None = None) -> UsageCredits:
    return _response(_load(session, user_id, _today(now)))


def progress(session: Session, user_id: int, now: datetime | None = None) -> tuple[int, int]:
    """(다음 보상까지 센 퀴즈 수, 오늘 받은 보상 횟수). 지갑 화면용."""
    record = _load(session, user_id, _today(now))
    return record.progress, record.rewards_today


def complete_puzzle(
    session: Session, user_id: int, completion: PuzzleCompletion, now: datetime | None = None
) -> PuzzleCompletionResult:
    data_store.get_crossword(completion.puzzle_id)  # 없는 퀴즈면 404
    today = _today(now)
    record = _load(session, user_id, today)
    rewarded = False
    if completion.completed_at is None:  # 지금 프론트는 시각을 보내지 않는다
        completion = completion.model_copy(update={"completed_at": datetime.now(KST).isoformat()})
    entry = completion.model_dump()
    if entry not in record.completions:  # 다시 보낸 같은 기록은 한 번만 반영한다
        record.completions = [*record.completions, entry][-KEEP_COMPLETIONS:]
        counts = (
            not completion.revealed
            and completion.puzzle_id not in record.counted_today
            and record.rewards_today < DAILY_MAX_REWARDS
            and _really_solved(session, user_id, completion.puzzle_id)
        )
        if counts:
            record.counted_today = [*record.counted_today, completion.puzzle_id]
            record.progress += 1
            if record.progress >= PUZZLES_PER_REWARD:
                record.progress = 0
                record.rewards_today += 1
                rewarded = True
    record.updated_at = datetime.now(UTC)
    session.add(record)
    session.commit()
    if rewarded:
        # 같은 날 같은 번째 보상은 한 번만 준다
        wallet_service.grant(
            session,
            user_id,
            wallet_service.PUZZLE_REWARD,
            "puzzle_reward",
            "낱말퀴즈 보상",
            f"puzzle:{today}:{record.rewards_today}",
        )
    return PuzzleCompletionResult(
        puzzle_progress=PuzzleProgress(done=record.progress, required=PUZZLES_PER_REWARD),
        rewarded=rewarded,
        balance=wallet_service.balance(session, user_id),
        reward_count_today=record.rewards_today,
        reward_daily_limit=DAILY_MAX_REWARDS,
    )
