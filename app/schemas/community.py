"""커뮤니티 요청·응답. 프론트 `frontend/src/types/community.ts` 와 같다."""

from typing import Literal

from pydantic import BaseModel

PostCategory = Literal["question", "analysis", "news", "free"]
PostSort = Literal["latest", "popular"]
ReportReason = Literal["solicitation", "abuse", "ad", "privacy", "etc"]


class Author(BaseModel):
    id: str
    nickname: str  # 실명 대신 닉네임


class PostOut(BaseModel):
    id: str
    stock_code: str
    category: PostCategory
    title: str  # 숨긴 글은 빈 문자열
    body: str
    author: Author
    created_at: str
    updated_at: str | None
    like_count: int
    comment_count: int
    # 아래 네 칸은 지금 보는 사람 기준 (비로그인이면 false)
    liked_by_me: bool
    reported_by_me: bool
    is_mine: bool
    is_hidden: bool


class CommentOut(BaseModel):
    id: str
    post_id: str
    body: str  # 숨긴 댓글은 빈 문자열
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
    title: str
    body: str


class CommentInput(BaseModel):
    body: str


class ReportInput(BaseModel):
    target_type: Literal["post", "comment"]
    target_id: str
    reason: ReportReason


class LikeResult(BaseModel):
    like_count: int
    liked_by_me: bool
