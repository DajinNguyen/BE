from datetime import date

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    """GET /api/auth/me 응답. 프론트 AuthUser 와 같다."""

    id: str  # 커뮤니티 author.id 와 같은 값 ("내 글" 판단)
    email: str
    name: str
    nickname: str  # 커뮤니티·상단 메뉴에 보이는 이름 (실명 대신)
    joined_at: date  # 가입일
    consent_required: bool  # true 면 약관 동의 화면을 먼저 보여준다 (다른 기능은 403)
    terms_version: str  # 지금 약관 버전


class ProfileUpdate(BaseModel):
    """PATCH /api/auth/me 요청. 닉네임을 비우거나 null 로 보내면 자동 닉네임으로 돌아간다."""

    nickname: str | None = Field(None, max_length=50)


class ConsentRequest(BaseModel):
    """POST /api/auth/consent 요청. 세 가지 모두 true 여야 한다."""

    terms: bool  # 이용약관 동의
    privacy: bool  # 개인정보 처리방침 동의
    age_14: bool  # 만 14세 이상입니다
