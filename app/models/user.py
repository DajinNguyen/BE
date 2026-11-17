from datetime import datetime

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    """Google로 로그인한 사용자. 이메일과 이름만 저장한다 (전화번호 등 민감 정보는 받지 않는다)."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    google_sub: str = Field(unique=True, index=True, max_length=255)  # Google 계정 고유 ID
    email: str = Field(max_length=320)
    name: str = Field(max_length=255)
    nickname: str | None = Field(
        default=None, max_length=20
    )  # 직접 정한 닉네임 (없으면 자동 닉네임)
    created_at: datetime
    last_login_at: datetime


class RevokedSession(SQLModel, table=True):
    """로그아웃한 로그인 쿠키. 쿠키를 지워도 값을 따로 적어 둔 사람이 다시 쓰지 못하게 막는다.

    토큰 자체가 아니라 해시만 저장하고, 토큰이 어차피 끝나는 시각이 지나면 지운다.
    """

    __tablename__ = "revoked_sessions"

    token_hash: str = Field(primary_key=True, max_length=64)
    expires_at: datetime
