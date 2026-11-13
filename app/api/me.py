from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import current_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.learning import TermProgress
from app.services import learning_service

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
