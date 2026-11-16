import pytest

from app.core.db import new_session
from app.main import app
from app.services import chat_service, wallet_service
from tests.conftest import login_as

COMPANY = {"page": "company", "stock_code": "005930", "tab": "financials"}


class FakeChatWriter:
    def __init__(self):
        self.calls = []
        self.reply = "삼성전자는 2025년에 매출이 늘었어요."

    def answer(self, screen, history, question):
        self.calls.append((screen, history, question))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def writer(client):
    fake = FakeChatWriter()
    app.dependency_overrides[chat_service.get_chat_writer] = lambda: fake
    return fake


def _ask(client, message="매출이 왜 늘었어요?", context=COMPANY, conversation_id=None):
    body = {"message": message, "context": context}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/chat/messages", json=body)


def _balance(client):
    return client.get("/api/me/wallet").json()["balance"]


def test_chat_needs_login(client, writer):
    for res in (
        _ask(client),
        client.get("/api/chat/conversations"),
        client.get("/api/chat/conversations/1"),
    ):
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "LOGIN_REQUIRED"
    assert writer.calls == []


def test_answer_uses_the_screen_data_and_takes_five_adk(client, writer):
    login_as(client, 1)
    res = _ask(client)
    assert res.status_code == 200
    body = res.json()
    assert body["question"]["role"] == "user"
    assert body["question"]["content"] == "매출이 왜 늘었어요?"
    assert body["answer"]["role"] == "assistant"
    assert body["answer"]["content"] == "삼성전자는 2025년에 매출이 늘었어요."
    assert (body["charged"], body["balance"]) == (5, 45)

    # AI에게는 보고 있는 화면의 실제 자료를 넘긴다
    screen, history, question = writer.calls[0]
    assert (screen["page"], screen["tab"]) == ("company", "financials")
    assert screen["company"]["name"] == "삼성전자"
    assert [a["fiscal_year"] for a in screen["company"]["financials"]["annual"]] == [
        2023,
        2024,
        2025,
    ]
    assert screen["company"]["financials"]["unit"] == "조 원"
    assert (history, question) == ([], "매출이 왜 늘었어요?")

    wallet = client.get("/api/me/wallet").json()
    assert (wallet["history"][0]["amount"], wallet["history"][0]["reason"]) == (-5, "chat")


def test_failed_answer_takes_nothing_and_is_not_saved(client, writer):
    login_as(client, 1)
    for broken in (RuntimeError("AI 고장"), "", "   "):
        writer.reply = broken
        res = _ask(client)
        assert res.status_code == 502
        assert res.json()["error"]["code"] == "CHAT_FAILED"
    assert _balance(client) == 50
    assert client.get("/api/chat/conversations").json() == []


def test_refused_when_adk_is_short(client, writer):
    user_id = login_as(client, 1)
    with new_session() as session:
        wallet_service.grant(session, user_id, -47, "chat", "테스트용", "test-spend")
    res = _ask(client)
    assert res.status_code == 402
    assert res.json()["error"]["code"] == "INSUFFICIENT_ADK"
    assert (res.json()["error"]["required"], res.json()["error"]["balance"]) == (5, 3)
    assert writer.calls == []
    assert _balance(client) == 3


def test_conversation_is_saved_and_continued(client, writer):
    login_as(client, 1)
    first = _ask(client).json()
    writer.reply = "영업이익은 물건을 팔아 남긴 돈이에요."
    second = _ask(client, "영업이익이 뭐예요?", conversation_id=first["conversation_id"]).json()
    assert second["conversation_id"] == first["conversation_id"]
    # 앞 질문·답변을 같이 넘겨서 이어서 대답하게 한다
    assert writer.calls[1][1] == [
        ("user", "매출이 왜 늘었어요?"),
        ("assistant", "삼성전자는 2025년에 매출이 늘었어요."),
    ]

    conversations = client.get("/api/chat/conversations").json()
    assert [(c["id"], c["title"]) for c in conversations] == [
        (first["conversation_id"], "매출이 왜 늘었어요?")
    ]
    detail = client.get(f"/api/chat/conversations/{first['conversation_id']}").json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "매출이 왜 늘었어요?"),
        ("assistant", "삼성전자는 2025년에 매출이 늘었어요."),
        ("user", "영업이익이 뭐예요?"),
        ("assistant", "영업이익은 물건을 팔아 남긴 돈이에요."),
    ]


def test_conversations_are_private(client, writer):
    login_as(client, 1)
    conversation_id = _ask(client).json()["conversation_id"]
    login_as(client, 2)
    assert client.get("/api/chat/conversations").json() == []
    for res in (
        client.get(f"/api/chat/conversations/{conversation_id}"),
        _ask(client, conversation_id=conversation_id),
        client.get("/api/chat/conversations/없는대화"),
    ):
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert _balance(client) == 50


@pytest.mark.parametrize(
    "advice",
    [
        "지금 사세요.",
        "이 회사는 매수하는 게 좋아요.",
        "지금 사도 돼요.",
        "이 주식을 추천해요.",
        "주가는 앞으로 오를 거예요.",
        "목표 주가는 10만 원이에요.",
    ],
)
def test_investment_advice_is_replaced_and_free(client, writer, advice):
    login_as(client, 1)
    writer.reply = f"실적이 좋아요. {advice}"
    body = _ask(client, "지금 사도 돼요?").json()
    assert body["answer"]["content"] == chat_service.REFUSAL
    assert (body["charged"], body["balance"]) == (0, 50)


@pytest.mark.parametrize(
    "fine",
    [
        "투자 추천이나 예측은 해 드릴 수 없어요. 매출 흐름을 살펴보세요.",
        "작년보다 매출이 올랐어요. 영업이익은 떨어졌어요.",
        "회사가 물건을 사고파는 일을 해요.",
    ],
)
def test_plain_explanations_are_not_mistaken_for_advice(fine):
    assert not chat_service.gives_advice(fine)


def test_prompt_forbids_advice_and_prediction():
    prompt = chat_service.SYSTEM_PROMPT
    for rule in ("투자 추천을 하지 않아요", "예측을 하지 않아요", "지어내거나 짐작하지 마세요"):
        assert rule in prompt


def test_too_many_questions_in_a_minute(client, writer, monkeypatch):
    monkeypatch.setattr(chat_service, "PER_MINUTE", 2)
    user_id = login_as(client, 1)
    assert _ask(client).status_code == 200
    assert _ask(client).status_code == 200
    res = _ask(client)
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "TOO_FREQUENT"
    assert _balance(client) == 40
    # 앞 질문에 답하는 동안 또 누르면 받지 않는다
    monkeypatch.setattr(chat_service, "PER_MINUTE", 100)
    chat_service._answering.add(user_id)
    try:
        assert _ask(client).status_code == 429
    finally:
        chat_service._answering.discard(user_id)
    assert _balance(client) == 40


def test_industry_and_term_screens(client, writer):
    login_as(client, 1)
    _ask(client, "이 산업이 뭐예요?", {"page": "industry", "industry_id": "semiconductor"})
    industry = writer.calls[0][0]["industry"]
    assert industry["name"] == "반도체"
    assert industry["biggest_companies"][0]["name"] == "삼성전자"

    term_id = client.get("/api/terms").json()[0]["id"]
    _ask(client, "쉽게 알려줘요", {"page": "term", "term_id": term_id})
    assert writer.calls[1][0]["term"]["easy_explanation"]

    _ask(client, "주식이 뭐예요?", {"page": "home"})
    assert "note" in writer.calls[2][0]


def test_bad_requests(client, writer):
    login_as(client, 1)
    assert _ask(client, "   ").json()["error"]["code"] == "INVALID_LENGTH"
    assert _ask(client, "가" * 501).status_code == 422
    assert _ask(client, context={"page": "company", "stock_code": "999999"}).status_code == 404
    assert _ask(client, context={"page": "industry", "industry_id": "없는산업"}).status_code == 404
    assert _ask(client, context={"page": "term", "term_id": "없는용어"}).status_code == 404
    assert writer.calls == []
    assert _balance(client) == 50


def test_chat_without_ai_key(client):
    login_as(client, 1)
    res = _ask(client)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "CHAT_NOT_CONFIGURED"
    assert _balance(client) == 50
