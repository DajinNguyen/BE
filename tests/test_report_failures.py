"""AI 가 실패했을 때: 예시 리포트로 대체하지 않고, 실패로 남기고 ADK 를 돌려준다."""

import ast
import asyncio
from pathlib import Path

import pytest
from google.genai import errors as genai_errors

from app.core.config import Settings
from app.core.db import new_session
from app.core.errors import AppError
from app.models.report import ReportRecord
from app.services import report_service, wallet_service
from tests.conftest import login_as

CONTEXT = {"company": {"stock_code": "005930"}}  # 예시 리포트가 있는 회사 (삼성전자)


def _client_error(code: int, status: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(code, {"error": {"code": code, "status": status}})


def _writer(monkeypatch, error: BaseException) -> tuple[report_service.GeminiReportWriter, list]:
    writer = report_service.GeminiReportWriter(Settings(gemini_api_key="fake-key"))
    monkeypatch.setattr(writer, "_RETRY_SECONDS", 0)
    calls: list = []

    async def failing(context):
        calls.append(1)
        raise error

    monkeypatch.setattr(writer, "_call", failing)
    return writer, calls


@pytest.mark.parametrize("code,status", [(400, "INVALID_ARGUMENT"), (403, "PERMISSION_DENIED")])
def test_bad_key_fails_instead_of_serving_the_sample_report(monkeypatch, code, status):
    writer, calls = _writer(monkeypatch, _client_error(code, status))
    with pytest.raises(AppError) as e:
        asyncio.run(writer.write(CONTEXT))
    assert e.value.code == "REPORT_GENERATION_FAILED"
    assert len(calls) == 1  # 키 오류는 다시 해도 같아서 재시도하지 않는다


def test_server_outage_fails_after_retries_instead_of_serving_the_sample_report(monkeypatch):
    error = genai_errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}})
    writer, calls = _writer(monkeypatch, error)
    with pytest.raises(AppError) as e:
        asyncio.run(writer.write(CONTEXT))
    assert e.value.code == "REPORT_GENERATION_FAILED"
    assert len(calls) == writer._MAX_ATTEMPTS


def test_failed_generation_is_saved_as_failed_and_adk_is_returned(client, monkeypatch):
    user_id = login_as(client, 1)
    with new_session() as session:
        assert wallet_service.balance(session, user_id) == 50
        hold_id = wallet_service.hold(session, user_id, 7, "report", "AI 리포트", "report:test")
        assert wallet_service.balance(session, user_id) == 43  # 잡아 둔 동안은 빠져 보인다
    writer, _ = _writer(monkeypatch, _client_error(403, "PERMISSION_DENIED"))

    asyncio.run(report_service.run_generation("005930", writer, new_session, hold_id))

    with new_session() as session:
        record = session.get(ReportRecord, "005930")
        assert (
            record is not None and record.status == "failed"
        )  # 예시 리포트가 'done' 으로 저장되지 않는다
        assert record.content is None or record.generated_at is None
        assert wallet_service.balance(session, user_id) == 50  # 차감되지 않는다


def test_every_claude_client_has_a_timeout():
    """기본 타임아웃은 약 10분이라, 멈춘 호출이 오래 붙잡혀 있지 않게 모든 Claude 클라이언트에 상한을 둔다."""
    missing = []
    for path in Path("app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"Anthropic", "AsyncAnthropic"} and not any(
                k.arg == "timeout" for k in node.keywords
            ):
                missing.append(f"{path}:{node.lineno}")
    assert missing == []
