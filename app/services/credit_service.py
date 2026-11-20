"""낱말퀴즈 완료 보상: 퀴즈 3개를 [정답 보기] 없이 풀 때마다 20 ADK.

규칙은 프론트 `src/config/usagePolicy.ts` 의 `PUZZLE_REWARD` 와 같다 (임시값, 확정 필요).
프론트 계산은 화면에 바로 보여주기 위한 것이고, 보상은 여기서 판단한 값이 기준이다.
- "오늘"은 서버 날짜(KST)다. 요청의 `completed_at` 은 기기 시각이라 기록만 하고 판단에 쓰지 않는다.
- 같은 퀴즈는 하루에 한 번만 센다. 없는 퀴즈 id 는 404.
- 퀴즈를 연 기록이 없거나(완료 요청만 보낸 경우) 연 지 너무 금방 완료한 퀴즈는 세지 않는다.
- 같은 퀴즈를 다시 보내도 하루에 한 번만 센다 (프론트가 실패하면 다시 보낸다).

서버 검증 (`crossword_service`): 완료 요청의 마지막 칸이 서버의 정답과 모두 같아야 센다.
그 밖에 오늘 [정답 보기]를 쓰지 않았고, 오늘 연 뒤 풀 만한 시간이 지났고, 정답을 추측하느라 확인·힌트를
너무 많이 부르지 않았어야 한다. 이 풀이 상태(`PuzzleOpenRecord`)는 날마다 새로 시작한다.
"""

import threading
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col

from app.core.config import get_settings
from app.models.learning import PuzzleOpenRecord, UsageCreditsRecord
from app.schemas.crossword import CrosswordPuzzle
from app.schemas.learning import PuzzleCompletion, UsageCredits
from app.schemas.wallet import PuzzleCompletionResult, PuzzleProgress
from app.services import crossword_service, data_store, wallet_service
from app.services.market_clock import KST

PUZZLES_PER_REWARD = 3  # 보상 1회에 필요한 퀴즈 수
DAILY_MAX_REWARDS = 2  # 하루 최대 보상 횟수
KEEP_PROGRESS_ACROSS_DAYS = True  # 날짜가 바뀌어도 센 퀴즈 수(예: 2/3)를 남긴다
MIN_SOLVE_SECONDS = 15  # 퀴즈를 열고 이보다 빨리 온 완료는 세지 않는다 (임시값)
KEEP_COMPLETIONS = 50  # 완료 기록은 최근 이만큼만 남긴다

# 같은 사용자의 완료 요청이 겹치지 않게 한다 (프로세스 안). 여러 서버에서는 아래 행 잠금이 막는다
_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)


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


def _load(session: Session, user_id: int, today: str, *, lock: bool = False) -> UsageCreditsRecord:
    """lock=True 면 PostgreSQL 에서 이 사용자의 기록 행을 잠가서, 같은 사용자의 완료 요청이 차례로 처리된다."""
    record = session.get(UsageCreditsRecord, user_id, with_for_update=lock) or UsageCreditsRecord(
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


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def mark_opened(
    session: Session, user_id: int, puzzle_id: str, now: datetime | None = None
) -> None:
    """로그인 사용자가 퀴즈를 열었다고 적는다.

    `opened_at` 은 처음 연 시각 그대로 둔다 (다시 열 때마다 바꾸면, 오래 풀다가 새로고침한 사람이
    "너무 빨리 완료"로 걸린다). 오늘의 풀이 상태(`attempt_*`)는 날이 바뀌면 새로 시작한다:
    그렇지 않으면 처음 연 날 한 번만 기다리면 그 뒤로는 기다리지 않고 완료할 수 있다.
    """
    moment = now or datetime.now(UTC)
    today = _today(moment)
    record = session.get(PuzzleOpenRecord, (user_id, puzzle_id))
    if record is None:
        record = PuzzleOpenRecord(user_id=user_id, puzzle_id=puzzle_id, opened_at=moment)
    elif record.attempt_day == today:
        return
    record.attempt_day = today
    record.attempt_started_at = moment
    record.attempt_calls = 0
    record.attempt_revealed = False
    session.add(record)
    try:
        session.commit()
    except IntegrityError:  # 같은 퀴즈를 동시에 처음 열었다: 먼저 저장된 것을 쓴다
        session.rollback()


def note_call(session: Session, user_id: int, puzzle_id: str) -> None:
    """확인·힌트를 한 번 불렀다고 센다 (정답을 추측하는 데 많이 부르면 보상을 세지 않는다). 한 번의 UPDATE."""
    mark_opened(session, user_id, puzzle_id)
    session.exec(
        update(PuzzleOpenRecord)
        .where(
            col(PuzzleOpenRecord.user_id) == user_id,
            col(PuzzleOpenRecord.puzzle_id) == puzzle_id,
        )
        .values(attempt_calls=func.coalesce(col(PuzzleOpenRecord.attempt_calls), 0) + 1)
    )
    session.commit()


def note_reveal(session: Session, user_id: int, puzzle_id: str) -> None:
    """오늘 [정답 보기]를 썼다고 적는다. 서버가 정답을 준 것이라 클라이언트가 숨길 수 없다."""
    mark_opened(session, user_id, puzzle_id)
    session.exec(
        update(PuzzleOpenRecord)
        .where(
            col(PuzzleOpenRecord.user_id) == user_id,
            col(PuzzleOpenRecord.puzzle_id) == puzzle_id,
        )
        .values(attempt_revealed=True)
    )
    session.commit()


def _attempt_ok(session: Session, user_id: int, puzzle_id: str, today: str) -> bool:
    """오늘 이 퀴즈를 열었고, 풀 만한 시간이 지났고, 정답을 보거나 추측하지 않았는지."""
    puzzle = data_store.get_crossword(puzzle_id)
    session.expire_all()  # 방금 다른 요청이 바꾼 값을 읽는다
    record = session.get(PuzzleOpenRecord, (user_id, puzzle_id))
    if record is None or record.attempt_day != today or record.attempt_started_at is None:
        return False
    if record.attempt_revealed:
        return False
    if (record.attempt_calls or 0) > crossword_service.call_budget(puzzle):
        return False
    waited = (datetime.now(UTC) - _aware(record.attempt_started_at)).total_seconds()
    return waited >= _min_wait(puzzle)


def _min_wait(puzzle) -> float:
    """풀 만한 최소 시간. MIN_SOLVE_SECONDS 가 0 이면 기다리지 않는다 (테스트)."""
    if MIN_SOLVE_SECONDS <= 0:
        return 0
    return max(MIN_SOLVE_SECONDS, crossword_service.min_solve_seconds(puzzle))


def _really_solved(session: Session, user_id: int, puzzle_id: str) -> bool:
    """(예전 프론트용) 퀴즈를 열었고, 풀 만한 시간이 지났는지. 정답은 확인하지 못한다."""
    opened = session.get(PuzzleOpenRecord, (user_id, puzzle_id))
    if opened is None:
        return False
    return (datetime.now(UTC) - _aware(opened.opened_at)).total_seconds() >= MIN_SOLVE_SECONDS


def get_credits(session: Session, user_id: int, now: datetime | None = None) -> UsageCredits:
    return _response(_load(session, user_id, _today(now)))


def progress(session: Session, user_id: int, now: datetime | None = None) -> tuple[int, int]:
    """(다음 보상까지 센 퀴즈 수, 오늘 받은 보상 횟수). 지갑 화면용."""
    record = _load(session, user_id, _today(now))
    return record.progress, record.rewards_today


def _verified(
    session: Session,
    user_id: int,
    completion: PuzzleCompletion,
    puzzle: CrosswordPuzzle,
    today: str,
) -> bool:
    """이 완료 요청이 보상으로 셀 만큼 진짜 푼 것인지 (정답 확인은 서버가 한다)."""
    if completion.revealed:
        return False
    if completion.entries is not None:
        return crossword_service.is_solved(puzzle, completion.entries) and _attempt_ok(
            session, user_id, puzzle.id, today
        )
    # 정답을 보내지 않는 예전 프론트: 정답이 공개돼 있던 시절의 규칙(연 지 얼마나 됐는지)만 본다.
    # 서버가 정답을 감추면(CROSSWORD_EXPOSE_ANSWERS=false) 확인할 수 없으니 보상으로 세지 않는다.
    return get_settings().crossword_expose_answers and _really_solved(session, user_id, puzzle.id)


def complete_puzzle(
    session: Session, user_id: int, completion: PuzzleCompletion, now: datetime | None = None
) -> PuzzleCompletionResult:
    puzzle = data_store.get_crossword(completion.puzzle_id)  # 없는 퀴즈면 404
    today = _today(now)
    with _locks[user_id]:
        try:
            return _complete(session, user_id, completion, puzzle, today)
        except IntegrityError:  # 이 사용자의 첫 기록을 동시에 만들었다: 읽어서 다시 한 번
            session.rollback()
            return _complete(session, user_id, completion, puzzle, today)


def _complete(
    session: Session,
    user_id: int,
    completion: PuzzleCompletion,
    puzzle: CrosswordPuzzle,
    today: str,
) -> PuzzleCompletionResult:
    record = _load(session, user_id, today, lock=True)
    rewarded = False
    if completion.completed_at is None:  # 지금 프론트는 시각을 보내지 않는다
        completion = completion.model_copy(update={"completed_at": datetime.now(KST).isoformat()})
    entry = completion.model_dump(exclude={"entries"})  # 채운 칸은 기록에 남기지 않는다 (크다)
    if entry not in record.completions:  # 다시 보낸 같은 기록은 한 번만 반영한다
        record.completions = [*record.completions, entry][-KEEP_COMPLETIONS:]
        counts = (
            completion.puzzle_id not in record.counted_today
            and record.rewards_today < DAILY_MAX_REWARDS
            and _verified(session, user_id, completion, puzzle, today)
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
