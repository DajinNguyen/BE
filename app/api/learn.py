from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import optional_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.crossword import CrosswordPuzzle, CrosswordSummary
from app.services import credit_service, data_store

router = APIRouter(prefix="/api/learn", tags=["learn"])


@router.get("/crosswords", response_model=list[CrosswordSummary])
def list_crosswords():
    return data_store.list_crosswords()


@router.get("/crosswords/{puzzle_id}", response_model=CrosswordPuzzle)
def get_crossword(
    puzzle_id: str,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    puzzle = data_store.get_crossword(puzzle_id)
    if user is not None:  # 보상은 실제로 연 퀴즈만 센다 (`credit_service`)
        credit_service.mark_opened(session, user.id, puzzle_id)
    return puzzle
