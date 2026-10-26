from pydantic import BaseModel


class AuthUser(BaseModel):
    """GET /api/auth/me 응답. 프론트 AuthUser 와 같다."""

    email: str
    name: str
