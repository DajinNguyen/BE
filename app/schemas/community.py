"""커뮤니티 요청·응답. 프론트 `frontend/src/types/community.ts` 와 같다."""

from typing import Literal

from pydantic import BaseModel

CommentSort = Literal["latest", "popular"]
ReportReason = Literal["solicitation", "abuse", "ad", "privacy", "etc"]


class Author(BaseModel):
    id: str
    nickname: str  # 실명 대신 닉네임


class CommentOut(BaseModel):
    id: str
    stock_code: str
    parent_id: str | None  # 답글이면 원댓글 id
    body: str  # 숨긴 댓글은 빈 문자열
    author: Author
    created_at: str
    like_count: int
    reply_count: int
    # 아래 세 칸은 지금 보는 사람 기준 (비로그인이면 false)
    liked_by_me: bool
    reported_by_me: bool
    is_mine: bool
    is_hidden: bool
    replies: list["CommentOut"]  # 원댓글에만 채운다 (오래된 순). 답글은 빈 목록


class CommentPage(BaseModel):
    items: list[CommentOut]  # 원댓글만, 답글은 각 댓글의 replies 에
    page: int
    has_next: bool
    total: int  # 원댓글 + 답글 수 (숨긴 것 포함)


class CommentInput(BaseModel):
    body: str
    parent_id: str | None = None  # 답글을 달 댓글 id


class ReportInput(BaseModel):
    target_type: Literal["comment"]
    target_id: str
    reason: ReportReason


class LikeResult(BaseModel):
    like_count: int
    liked_by_me: bool
