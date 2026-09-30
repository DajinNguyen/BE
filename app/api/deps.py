from functools import lru_cache

from app.core.config import get_settings
from app.services.report_service import ClaudeReportWriter, ReportWriter


@lru_cache
def get_report_writer() -> ReportWriter:
    return ClaudeReportWriter(get_settings())
