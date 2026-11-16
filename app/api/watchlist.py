from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import current_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.watchlist import Watchlist
from app.services import watchlist_service

router = APIRouter(prefix="/api/me/watchlist", tags=["me"])


@router.get("", response_model=Watchlist)
def get_watchlist(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 관심 기업 목록. 없으면 빈 목록(`stock_codes: []`)."""
    return watchlist_service.get_watchlist(session, user.id)


@router.put("", response_model=Watchlist)
def put_watchlist(
    body: Watchlist,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """내 관심 기업 목록 전체를 한 번에 저장하고, 저장한 값을 돌려준다."""
    return watchlist_service.save_watchlist(session, user.id, body)
