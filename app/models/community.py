from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class CompanyComment(SQLModel, table=True):
    """기업에 다는 댓글. parent_id 가 있으면 그 댓글에 단 답글(대댓글)이다.

    답글은 한 단계만: 답글에 다시 답글을 달면 같은 원댓글 아래에 붙는다.
    좋아요·답글 수는 목록 정렬(인기순)과 표시에 쓰려고 함께 저장한다.
    """

    __tablename__ = "company_comments"

    id: int | None = Field(default=None, primary_key=True)
    stock_code: str = Field(index=True, max_length=6)
    parent_id: int | None = Field(default=None, foreign_key="company_comments.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    body: str = Field(max_length=500)
    created_at: datetime
    like_count: int = 0
    reply_count: int = 0
    is_hidden: bool = False  # 서로 다른 사람의 신고가 쌓여 숨긴 댓글


class CommentLike(SQLModel, table=True):
    """한 사람이 한 댓글에 한 번만 (같은 요청을 여러 번 보내도 결과가 같다)."""

    __tablename__ = "company_comment_likes"

    comment_id: int = Field(foreign_key="company_comments.id", primary_key=True)
    user_id: int = Field(foreign_key="users.id", primary_key=True)


class ContentReport(SQLModel, table=True):
    """댓글 신고. 같은 사람은 같은 대상을 한 번만 신고할 수 있다."""

    __tablename__ = "company_comment_reports"
    __table_args__ = (UniqueConstraint("target_type", "target_id", "user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    target_type: str = Field(max_length=10)  # comment
    target_id: int = Field(index=True)
    user_id: int = Field(foreign_key="users.id")
    reason: str = Field(max_length=20)
    created_at: datetime
