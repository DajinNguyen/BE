"""AI 호출이 실패하거나 시간 초과·취소되면 잡아 둔 ADK 가 정확히 돌아오는지 본다.

챗봇(5 ADK)과 리포트(7 ADK)는 먼저 잡아 두고, 답이 제대로 나왔을 때만 확정한다.
비교 분석은 ADK 를 쓰지 않는다 (아래 마지막 테스트가 그 사실을 지킨다).
"""

import asyncio

import anthropic
import httpx
import pytest
from google.genai import errors as genai_errors

from app.core.db import new_session
from app.core.errors import report_generation_failed
from app.main import app
from app.models.report import ReportRecord
from app.services import chat_service, report_service, wallet_service
from tests.conftest import login_as

REQUEST = httpx.Request("POST", "https://ai.example/v1")


def _failures():
    """외부 AI 가 실패하는 여러 모습 (서로 다른 SDK·네트워크 오류)."""
    return [
        pytest.param(TimeoutError("timed out"), id="timeout"),
        pytest.param(httpx.ReadTimeout("slow", request=REQUEST), id="httpx-timeout"),
        pytest.param(httpx.ConnectError("down", request=REQUEST), id="connect-error"),
        pytest.param(
            genai_errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}),
            id="gemini-503",
        ),
        pytest.param(
            genai_errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}),
            id="gemini-429",
        ),
        pytest.param(anthropic.APITimeoutError(request=REQUEST), id="claude-timeout"),
        pytest.param(RuntimeError("뜻밖의 오류"), id="unexpected"),
    ]


def _wallet(client):
    return client.get("/api/me/wallet").json()


def _assert_nothing_taken(client):
    wallet = _wallet(client)
    assert wallet["balance"] == 50
    assert len(wallet["history"]) == 1  # 가입 축하만 (차감 내역이 남지 않는다)


# ------------------------------------------------------------------ 챗봇


class _ChatWriter:
    def __init__(self):
        self.reply: str | Exception = "삼성전자는 2025년에 매출이 늘었어요."

    def answer(self, screen, history, question):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def chat_writer(client):
    fake = _ChatWriter()
    app.dependency_overrides[chat_service.get_chat_writer] = lambda: fake
    return fake


def _ask(client):
    body = {
        "message": "매출이 왜 늘었어요?",
        "context": {
            "page": "company",
            "tab": "financials",
            "stock_code": "005930",
            "company_name": "삼성전자",
        },
    }
    return client.post("/api/chat", json=body)


@pytest.mark.parametrize("error", _failures())
def test_chat_failure_gives_the_adk_back(client, chat_writer, error):
    login_as(client, 1)
    chat_writer.reply = error
    res = _ask(client)
    assert (res.status_code, res.json()["error"]["code"]) == (502, "CHAT_FAILED")
    _assert_nothing_taken(client)
    assert client.get("/api/chat/conversations").json() == []


def test_chat_success_takes_exactly_five(client, chat_writer):
    login_as(client, 1)
    assert _ask(client).status_code == 200
    wallet = _wallet(client)
    assert wallet["balance"] == 45
    spent = [h["amount"] for h in wallet["history"] if h["type"] == "spend"]
    assert spent == [5]


def test_chat_failure_after_the_answer_still_gives_the_adk_back(client, chat_writer, monkeypatch):
    """답변은 나왔는데 응답을 만들다 오류가 나면, 사용자는 답을 못 받았으니 ADK 도 빠지지 않는다."""
    login_as(client, 1)

    def broken(screen, answer):
        raise RuntimeError("출처를 만들다 오류")

    monkeypatch.setattr(chat_service, "cited_sources", broken)
    with pytest.raises(RuntimeError):
        _ask(client)
    _assert_nothing_taken(client)


def test_chat_retry_after_failure_is_charged_once(client, chat_writer):
    login_as(client, 1)
    chat_writer.reply = TimeoutError("timed out")
    assert _ask(client).status_code == 502
    chat_writer.reply = "다시 시도해서 성공한 답변이에요."
    assert _ask(client).status_code == 200
    assert _wallet(client)["balance"] == 45  # 실패는 무료, 성공 1번만 5 ADK


# ------------------------------------------------------------------ 리포트


@pytest.mark.parametrize("error", _failures())
def test_report_failure_gives_the_adk_back(client, fake_writer, error):
    login_as(client, 1)

    async def failing(context):
        raise error

    fake_writer.write = failing
    client.post("/api/companies/005930/report")
    assert client.get("/api/companies/005930/report").json()["status"] == "failed"
    _assert_nothing_taken(client)


def test_report_failure_reason_is_shown_and_adk_is_returned_for_app_errors(client, fake_writer):
    login_as(client, 1)

    async def failing(context):
        raise report_generation_failed("지금은 리포트 요청이 많아요.")

    fake_writer.write = failing
    client.post("/api/companies/005930/report")
    body = client.get("/api/companies/005930/report").json()
    assert body["status"] == "failed" and "요청이 많아요" in body["failure_reason"]
    _assert_nothing_taken(client)


def test_cancelled_report_generation_gives_the_adk_back(client):
    """서버를 끄거나 작업이 취소돼도 잡아 둔 ADK 가 10분 동안 묶여 있지 않게 바로 돌려준다."""
    user_id = login_as(client, 1)
    with new_session() as session:
        wallet_service.balance(session, user_id)
        hold_id = wallet_service.hold(session, user_id, 7, "report", "AI 리포트", "report:cancel")

    class Hangs:
        async def write(self, context):
            await asyncio.sleep(3600)

    async def scenario():
        task = asyncio.create_task(
            report_service.run_generation("005930", Hangs(), new_session, hold_id)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    with new_session() as session:
        record = session.get(ReportRecord, "005930")
        assert record is not None and record.status == "failed"
        assert wallet_service.balance(session, user_id) == 50


def test_report_success_takes_exactly_seven(client, fake_writer):
    login_as(client, 1)
    client.post("/api/companies/005930/report")
    assert client.get("/api/companies/005930/report").json()["status"] == "done"
    wallet = _wallet(client)
    assert wallet["balance"] == 43
    assert [h["amount"] for h in wallet["history"] if h["type"] == "spend"] == [7]


# ------------------------------------------------------------------ 비교 분석


def test_compare_does_not_use_adk(client):
    """비교 분석은 ADK 를 쓰지 않는다. 나중에 ADK 를 받게 바꾼다면 이 파일에 환불 테스트를 먼저 추가해야 한다."""
    login_as(client, 1)
    before = _wallet(client)["balance"]
    client.get("/api/companies/compare", params={"left": "000660", "right": "005930"})
    assert _wallet(client)["balance"] == before
