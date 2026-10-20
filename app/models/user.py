from datetime import datetime

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    """Google로 로그인한 사용자. 이메일과 이름만 저장한다 (전화번호 등 민감 정보는 받지 않는다)."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    google_sub: str = Field(unique=True, index=True, max_length=255)  # Google 계정 고유 ID
    email: str = Field(max_length=320)
    name: str = Field(max_length=255)
    created_at: datetime
    last_login_at: datetime
