from functools import lru_cache

from fastapi import Cookie, Depends
from sqlmodel import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import login_required
from app.models.user import User
from app.services import auth_service
from app.services.auth_service import SESSION_COOKIE
from app.services.overview_service import (
    ClaudeOverviewWriter,
    GeminiOverviewWriter,
    OverviewWriter,
)
from app.services.report_service import (
    ClaudeReportWriter,
    FixtureReportWriter,
    GeminiReportWriter,
    ReportWriter,
)


@lru_cache
def get_report_writer() -> ReportWriter:
    """설정된 키에 따라 작성자를 고른다: Anthropic > Gemini > 데모용 목데이터."""
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeReportWriter(settings)
    if settings.gemini_api_keys:
        return GeminiReportWriter(settings)
    return FixtureReportWriter()


@lru_cache
def get_overview_writer() -> OverviewWriter | None:
    """AI 개요 작성자: Anthropic > Gemini. 키가 없으면 None (개요를 만들지 않는다)."""
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeOverviewWriter(settings)
    if settings.gemini_api_keys:
        return GeminiOverviewWriter(settings)
    return None


def optional_user(
    token: str | None = Cookie(None, alias=SESSION_COOKIE),
    session: Session = Depends(get_session),
) -> User | None:
    """로그인했으면 사용자, 아니면 None (로그인 없이도 쓰는 API용)."""
    return auth_service.user_from_session(session, token)


def current_user(user: User | None = Depends(optional_user)) -> User:
    """로그인이 필요한 API용. 로그인하지 않았으면 401 `LOGIN_REQUIRED`."""
    if user is None:
        raise login_required()
    return user
