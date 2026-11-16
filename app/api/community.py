from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import current_user, optional_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.community import (
    CommentInput,
    CommentOut,
    CommentPage,
    CommentSort,
    LikeResult,
    ReportInput,
)
from app.services import community_service

# 보기는 누구나 (optional_user), 쓰기는 로그인한 사람만 (current_user → 401 LOGIN_REQUIRED)
router = APIRouter(prefix="/api", tags=["community"])


@router.get("/companies/{stock_code}/comments", response_model=CommentPage)
def list_comments(
    stock_code: str,
    sort: CommentSort = Query("latest"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    viewer: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    return community_service.list_comments(session, stock_code, sort, page, size, viewer)


@router.post("/companies/{stock_code}/comments", response_model=CommentOut, status_code=201)
def create_comment(
    stock_code: str,
    data: CommentInput,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return community_service.create_comment(session, stock_code, data, user)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(
    comment_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    community_service.delete_comment(session, comment_id, user)


@router.post("/comments/{comment_id}/like", response_model=LikeResult)
def like_comment(
    comment_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return community_service.set_like(session, comment_id, user, liked=True)


@router.delete("/comments/{comment_id}/like", response_model=LikeResult)
def unlike_comment(
    comment_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return community_service.set_like(session, comment_id, user, liked=False)


@router.post("/reports", status_code=204)
def report_target(
    data: ReportInput,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    community_service.report(session, data, user)
