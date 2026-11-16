from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import current_user, optional_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.community import (
    CommentInput,
    CommentOut,
    LikeResult,
    PostCategory,
    PostInput,
    PostOut,
    PostPage,
    PostSort,
    ReportInput,
)
from app.services import community_service

# 보기는 누구나 (optional_user), 쓰기는 로그인한 사람만 (current_user → 401 LOGIN_REQUIRED)
router = APIRouter(prefix="/api", tags=["community"])


@router.get("/companies/{stock_code}/posts", response_model=PostPage)
def list_posts(
    stock_code: str,
    sort: PostSort = Query("latest"),
    category: PostCategory | None = Query(None, description="없으면 전체"),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    viewer: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    return community_service.list_posts(session, stock_code, sort, category, page, size, viewer)


@router.post("/companies/{stock_code}/posts", response_model=PostOut, status_code=201)
def create_post(
    stock_code: str,
    data: PostInput,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return community_service.create_post(session, stock_code, data, user)


@router.get("/posts/{post_id}", response_model=PostOut)
def get_post(
    post_id: str,
    viewer: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    return community_service.get_post(session, post_id, viewer)


@router.put("/posts/{post_id}", response_model=PostOut)
def update_post(
    post_id: str,
    data: PostInput,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return community_service.update_post(session, post_id, data, user)


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(
    post_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    community_service.delete_post(session, post_id, user)


@router.get("/posts/{post_id}/comments", response_model=list[CommentOut])
def list_comments(
    post_id: str,
    viewer: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    return community_service.list_comments(session, post_id, viewer)


@router.post("/posts/{post_id}/comments", response_model=CommentOut, status_code=201)
def create_comment(
    post_id: str,
    data: CommentInput,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return community_service.create_comment(session, post_id, data, user)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(
    comment_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    community_service.delete_comment(session, comment_id, user)


@router.post("/posts/{post_id}/like", response_model=LikeResult)
def like_post(
    post_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return community_service.set_like(session, post_id, user, liked=True)


@router.delete("/posts/{post_id}/like", response_model=LikeResult)
def unlike_post(
    post_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return community_service.set_like(session, post_id, user, liked=False)


@router.post("/reports", status_code=204)
def report_target(
    data: ReportInput,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    community_service.report(session, data, user)
