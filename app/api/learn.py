from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import optional_user
from app.core.db import get_session
from app.core.errors import invalid_param
from app.core.rate_limit import rate_limit
from app.models.user import User
from app.schemas.crossword import (
    CrosswordCheckRequest,
    CrosswordCheckResult,
    CrosswordHintRequest,
    CrosswordHintResult,
    CrosswordPuzzleOut,
    CrosswordRevealResult,
    CrosswordSummary,
)
from app.services import credit_service, crossword_service, data_store

router = APIRouter(prefix="/api/learn", tags=["learn"])

# 확인·힌트는 정답을 조금씩 알려 주는 API 라서 자주 부르지 못하게 한다 (사용자별, 로그인 전에는 주소별)
_guess_limit = Depends(rate_limit("crossword", 60))


@router.get("/crosswords", response_model=list[CrosswordSummary])
def list_crosswords():
    return data_store.list_crosswords()


@router.get(
    "/crosswords/{puzzle_id}", response_model=CrosswordPuzzleOut, response_model_exclude_none=True
)
def get_crossword(
    puzzle_id: str,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    """퀴즈의 칸 수와 열쇠. 정답은 CROSSWORD_EXPOSE_ANSWERS 가 켜져 있을 때만 들어 있다."""
    puzzle = data_store.get_crossword(puzzle_id)
    if user is not None:  # 보상은 실제로 연 퀴즈만 센다 (`credit_service`)
        credit_service.mark_opened(session, user.id, puzzle_id)
    return crossword_service.public_puzzle(puzzle)


@router.post(
    "/crosswords/{puzzle_id}/check",
    response_model=CrosswordCheckResult,
    dependencies=[_guess_limit],
)
def check_crossword(
    puzzle_id: str,
    body: CrosswordCheckRequest,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    """채운 칸이 맞는지: 틀린 칸, 다 채운 단어의 맞고 틀림, 다 맞았는지. (로그인 전에도 쓸 수 있다)"""
    puzzle = data_store.get_crossword(puzzle_id)
    if user is not None:
        credit_service.note_call(session, user.id, puzzle_id)
    return crossword_service.check(puzzle, body.entries)


@router.post(
    "/crosswords/{puzzle_id}/hint",
    response_model=CrosswordHintResult,
    dependencies=[_guess_limit],
)
def hint_crossword(
    puzzle_id: str,
    body: CrosswordHintRequest,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    """단어의 힌트: 1단계 초성, 2단계 첫 글자."""
    puzzle = data_store.get_crossword(puzzle_id)
    result = crossword_service.hint(puzzle, body.word_index, body.level)
    if result is None:
        raise invalid_param("없는 단어예요.")
    if user is not None:
        credit_service.note_call(session, user.id, puzzle_id)
    return result


@router.post(
    "/crosswords/{puzzle_id}/reveal",
    response_model=CrosswordRevealResult,
    dependencies=[_guess_limit],
)
def reveal_crossword(
    puzzle_id: str,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    """[정답 보기]: 모든 칸의 정답. 로그인했다면 오늘 이 퀴즈는 보상에서 빠진다 (서버가 기록한다)."""
    puzzle = data_store.get_crossword(puzzle_id)
    if user is not None:
        credit_service.note_reveal(session, user.id, puzzle_id)
    return crossword_service.reveal(puzzle)
