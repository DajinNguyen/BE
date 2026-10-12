from functools import lru_cache

from app.core.config import get_settings
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
    if settings.gemini_api_key:
        return GeminiReportWriter(settings)
    return FixtureReportWriter()
