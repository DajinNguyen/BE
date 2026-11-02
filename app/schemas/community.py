"""커뮤니티 요청·응답. 프론트 `frontend/src/types/community.ts` 와 같다."""

from typing import Literal

from pydantic import BaseModel

PostCategory = Literal["question", "analysis", "news", "free"]
PostSort = Literal["latest", "popular"]
ReportReason = Literal["solicitation", "abuse", "ad", "privacy", "image", "etc"]


class Author(BaseModel):
    id: str
    nickname: str  # 실명 대신 닉네임


class PostOut(BaseModel):
    id: str
    stock_code: str
    category: PostCategory
    title: str  # 숨긴 글은 빈 문자열 (화면에서는 안 쓴다)
    body: str
    image_url: str | None
    author: Author
    created_at: str
    updated_at: str | None
    like_count: int
    comment_count: int  # 지우지 않은 댓글 수
    # 아래 네 칸은 지금 보는 사람 기준 (비로그인이면 false)
    liked_by_me: bool
    reported_by_me: bool
    is_mine: bool
    is_hidden: bool


class CommentOut(BaseModel):
    id: str
    post_id: str
    body: str  # 숨기거나 지운 댓글은 빈 문자열
    parent_id: str | None  # 답글이면 원댓글 id
    reply_to_nickname: str | None  # 답글의 답글이면 대상 닉네임
    reply_count: int  # 이 댓글에 달린 (지우지 않은) 답글 수
    is_deleted: bool  # 지웠지만 답글이 있어 자리만 남은 댓글
    image_url: str | None
    author: Author
    created_at: str
    is_mine: bool
    is_hidden: bool


class PostPage(BaseModel):
    items: list[PostOut]
    page: int
    has_next: bool


class PostInput(BaseModel):
    category: PostCategory
    title: str | None = None  # 안 보내면 본문 첫 줄(30자)로 채운다
    body: str
    image_url: str | None = None  # POST /api/uploads 응답의 image_url


class CommentInput(BaseModel):
    body: str
    parent_id: str | None = None
    reply_to_nickname: str | None = None
    image_url: str | None = None


class ReportInput(BaseModel):
    target_type: Literal["post", "comment"]
    target_id: str
    reason: ReportReason


class LikeResult(BaseModel):
    like_count: int
    liked_by_me: bool


class UploadResult(BaseModel):
    image_url: str
