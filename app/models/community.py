from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Post(SQLModel, table=True):
    """기업별 커뮤니티 글. 좋아요·댓글 수는 목록 정렬(인기순)에 쓰려고 함께 저장한다."""

    __tablename__ = "community_posts"

    id: int | None = Field(default=None, primary_key=True)
    stock_code: str = Field(index=True, max_length=6)
    user_id: int = Field(foreign_key="users.id", index=True)
    category: str = Field(max_length=20)  # question | analysis | news | free
    title: str = Field(max_length=50)
    body: str = Field(max_length=2000)
    created_at: datetime
    updated_at: datetime | None = None
    like_count: int = 0
    comment_count: int = 0
    is_hidden: bool = False  # 서로 다른 사람의 신고가 쌓여 숨긴 글


class PostComment(SQLModel, table=True):
    __tablename__ = "community_comments"

    id: int | None = Field(default=None, primary_key=True)
    post_id: int = Field(foreign_key="community_posts.id", index=True)
    user_id: int = Field(foreign_key="users.id")
    body: str = Field(max_length=500)
    created_at: datetime
    is_hidden: bool = False


class PostLike(SQLModel, table=True):
    """한 사람이 한 글에 한 번만 (같은 요청을 여러 번 보내도 결과가 같다)."""

    __tablename__ = "community_likes"

    post_id: int = Field(foreign_key="community_posts.id", primary_key=True)
    user_id: int = Field(foreign_key="users.id", primary_key=True)


class ContentReport(SQLModel, table=True):
    """글·댓글 신고. 같은 사람은 같은 대상을 한 번만 신고할 수 있다."""

    __tablename__ = "community_reports"
    __table_args__ = (UniqueConstraint("target_type", "target_id", "user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    target_type: str = Field(max_length=10)  # post | comment
    target_id: int = Field(index=True)
    user_id: int = Field(foreign_key="users.id")
    reason: str = Field(max_length=20)
    created_at: datetime
