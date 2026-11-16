from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import current_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.learning import PuzzleCompletion, TermProgress, UsageCredits
from app.services import credit_service, learning_service

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/term-progress", response_model=TermProgress)
def get_term_progress(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 용어 학습 기록. 기록이 없으면 빈 기록(`terms: {}`)."""
    return learning_service.get_progress(session, user.id)


@router.put("/term-progress", response_model=TermProgress)
def put_term_progress(
    body: TermProgress,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """내 용어 학습 기록 전체를 한 번에 저장하고, 저장한 값을 돌려준다."""
    return learning_service.save_progress(session, user.id, body)


@router.get("/usage-credits", response_model=UsageCredits)
def get_usage_credits(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 낱말퀴즈 보상 (오늘 받은 리포트 만들기 추가 횟수)."""
    return credit_service.get_credits(session, user.id)


@router.post("/puzzle-completions", response_model=UsageCredits)
def post_puzzle_completion(
    body: PuzzleCompletion,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """낱말퀴즈를 다 풀었다고 알린다. 보상은 서버 날짜 기준으로 판단하고, 바뀐 보상 상태를 돌려준다."""
    return credit_service.complete_puzzle(session, user.id, body)
