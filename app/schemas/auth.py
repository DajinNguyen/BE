from pydantic import BaseModel


class AuthUser(BaseModel):
    """GET /api/auth/me 응답. 프론트 AuthUser 와 같다."""

    id: str  # 커뮤니티 author.id 와 같은 값 ("내 글" 판단)
    email: str
    name: str
    nickname: str  # 커뮤니티·상단 메뉴에 보이는 이름 (실명 대신)
