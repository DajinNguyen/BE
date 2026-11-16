import pytest

from app.core.db import new_session
from app.main import app
from app.services import chat_data, chat_plan, chat_service, wallet_service
from tests.conftest import login_as

COMPANY = {
    "page": "company",
    "tab": "financials",
    "stock_code": "005930",
    "company_name": "삼성전자",
}


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
    return client.post("/api/chat", json=body)


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
    assert body == {
        "answer": "삼성전자는 2025년에 매출이 늘었어요.",
        "sources": [],  # 답변에 나온 기사가 없다
        "conversation_id": body["conversation_id"],
        "balance": 45,  # 5 ADK를 뺀 뒤 잔액
    }

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
    top = wallet["history"][0]
    assert (top["type"], top["amount"], top["reason"]) == ("spend", 5, "AI 챗봇 질문")


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
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
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
    assert (body["answer"], body["balance"]) == (chat_service.REFUSAL, 50)  # ADK는 빼지 않는다


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
    for rule in (
        "투자 추천을 하지 않아요",
        "예측하지 않아요",
        "자료에 없는 숫자는 한 개도 쓰지 않아요",
    ):
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


def test_screens_follow_the_frontend_context(client, writer, monkeypatch):
    monkeypatch.setattr(chat_service, "PER_MINUTE", 100)
    login_as(client, 1)

    def screen_for(context, question="알려줘요"):
        _ask(client, question, context)
        return writer.calls[-1][0]

    industry = screen_for(
        {"page": "industry", "industry_id": "semiconductor", "industry_name": "반도체"}
    )["industries_asked_about"][0]
    assert industry["name"] == "반도체"
    assert industry["biggest_companies"][0]["name"] == "삼성전자"

    term_id = client.get("/api/terms").json()[0]["id"]
    assert screen_for({"page": "learn", "term_id": term_id})["term"]["easy_explanation"]

    # AI 분석 화면은 리포트 탭으로 본다
    report = screen_for(
        {"page": "report", "stock_code": "005930", "report_id": None, "company_name": "삼성전자"}
    )
    assert (report["tab"], report["company"]["name"]) == ("report", "삼성전자")

    compare = screen_for(
        {
            "page": "compare",
            "left_code": "000660",
            "right_code": "005930",
            "left_name": "SK하이닉스",
        }
    )
    # 지금 보는 기업(right)이 먼저
    assert [c["name"] for c in compare["companies_being_compared"]] == ["삼성전자", "SK하이닉스"]

    for page in ("main", "learn", "other"):
        screen = screen_for({"page": page})
        assert set(screen) == {"page", chat_service.ASKED_KEY}


def test_unknown_screen_data_is_skipped_not_an_error(client, writer):
    """화면 정보가 이상해도 대답은 한다 (프론트는 오류를 모두 "답변을 받지 못했어요"로 보여준다)."""
    login_as(client, 1)
    for context in (
        {"page": "company", "tab": "overview", "stock_code": "999999", "company_name": "없는회사"},
        {"page": "industry", "industry_id": "없는산업"},
        {"page": "learn", "term_id": "없는용어"},
    ):
        assert _ask(client, context=context).status_code == 200
        assert not {"company", "industries_asked_about", "term"} & set(writer.calls[-1][0])


def test_bad_requests(client, writer):
    login_as(client, 1)
    assert _ask(client, "   ").json()["error"]["code"] == "INVALID_LENGTH"
    assert _ask(client, "가" * 501).status_code == 422
    assert _ask(client, context={"page": "없는화면"}).status_code == 422
    assert writer.calls == []
    assert _balance(client) == 50


def test_first_question_sends_null_conversation_id(client, writer):
    login_as(client, 1)
    res = client.post(
        "/api/chat",
        json={"message": "안녕하세요", "context": {"page": "main"}, "conversation_id": None},
    )
    assert res.status_code == 200 and res.json()["conversation_id"]


def test_chat_without_ai_key(client):
    login_as(client, 1)
    res = _ask(client)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "CHAT_NOT_CONFIGURED"
    assert _balance(client) == 50


@pytest.mark.parametrize(
    ("question", "names"),
    [
        ("삼성전자랑 하이닉스 매출 비교해줘", ["삼성전자", "SK하이닉스"]),
        ("삼성전자랑 sk하이닉스 매출 비교좀해줘", ["삼성전자", "SK하이닉스"]),
        ("삼전이랑 현대차 중에 어디가 매출이 커요?", ["삼성전자", "현대차"]),
        ("005930 은 뭐하는 회사예요?", ["삼성전자"]),
        ("네이버보다 카카오가 커요?", ["NAVER", "카카오"]),
        (
            "반도체 회사는 왜 매출이 늘었어요?",
            [],
        ),  # 여러 회사 이름에 들어 있는 낱말은 회사로 보지 않는다
        ("매출이랑 영업이익 차이가 뭐예요?", []),
    ],
)
def test_companies_named_in_the_question_are_found(question, names):
    seeds = chat_data.data_store._seed_companies()
    found = chat_data.mentioned_companies(question, exclude=set())
    assert [seeds[code].name for code in found] == names


def test_question_about_a_company_not_on_screen_gets_its_data(client, writer):
    """메인이나 다른 회사 화면에서 물어도, 질문이 가리키는 회사의 자료를 찾아 넘긴다."""
    login_as(client, 1)
    _ask(client, "삼성전자랑 하이닉스 매출 비교해줘", {"page": "main"})
    screen = writer.calls[-1][0]
    assert [c["name"] for c in screen["companies_asked_about"]] == ["삼성전자", "SK하이닉스"]

    # 화면에 이미 있는 회사는 또 붙이지 않는다
    _ask(client, "삼성전자랑 하이닉스 매출 비교해줘", COMPANY)
    screen = writer.calls[-1][0]
    assert screen["company"]["name"] == "삼성전자"
    assert [c["name"] for c in screen["companies_asked_about"]] == ["SK하이닉스"]

    # 다른 회사만 물으면 화면의 회사는 어느 화면인지만 알려준다 (그 회사 얘기로 새지 않게)
    _ask(client, "카카오는 뭐 하는 회사예요?", COMPANY)
    screen = writer.calls[-1][0]
    assert "company" not in screen
    assert screen["company_on_screen"]["name"] == "삼성전자"
    assert [c["name"] for c in screen["companies_asked_about"]] == ["카카오"]


def test_data_is_put_right_before_the_question():
    message = chat_service._last_message(
        {"page": "main", "tab": None, "company": {"name": "삼성전자", "one_liner": "", "news": []}},
        "뭐하는 회사예요?",
    )
    # 빈 값은 빼고 넘긴다
    assert message == (
        '[자료]\n{"page": "main", "company": {"name": "삼성전자"}}\n\n[질문]\n뭐하는 회사예요?'
    )


def test_yearly_numbers_come_with_changes_already_calculated(client, writer):
    """변화율·영업이익률은 서버가 계산해서 넘긴다 (AI가 직접 계산하지 않게)."""
    login_as(client, 1)
    _ask(client)
    years = writer.calls[-1][0]["company"]["financials"]["annual"]
    first, second = years[0], years[1]
    assert first["revenue_change_from_last_year_percent"] is None  # 앞 연도가 없다
    assert second["revenue_change_from_last_year_percent"] == round(
        (second["revenue"] - first["revenue"]) / first["revenue"] * 100, 1
    )
    assert second["operating_margin_percent"] == round(
        second["operating_income"] / second["revenue"] * 100, 1
    )


def test_why_questions_get_recent_news(client, writer):
    login_as(client, 1)
    _ask(client, "매출이 얼마예요?")
    assert "recent_news" not in writer.calls[-1][0]["company"]
    _ask(client, "매출이 왜 늘었어요?")
    news = writer.calls[-1][0]["company"]["recent_news"]
    assert news and set(news[0]) == {"title", "press", "date", "summary", "url"}


def test_level_questions_get_same_industry_companies(client, writer, monkeypatch):
    from app.schemas.common import SampleValue
    from app.schemas.financials import AnnualResult, Financials, Indicators

    def financials(code):
        return Financials(
            stock_code=code,
            unit="조 원",
            basis="연결 기준",
            source="DART",
            annual=[
                AnnualResult(fiscal_year=2024, revenue=10.0, operating_income=1.0),
                AnnualResult(fiscal_year=2025, revenue=12.0, operating_income=3.0),
            ],
            latest_quarter=None,
            segments=None,
            indicators=Indicators(debt_ratio=SampleValue(value=40.0, is_sample=False)),
        )

    monkeypatch.setattr(chat_data.data_store, "find_financials", financials)
    login_as(client, 1)
    _ask(client, "부채비율이 얼마예요?")
    assert "same_industry_companies_for_reference" not in writer.calls[-1][0]["company"]
    _ask(client, "부채비율 40%면 높은 편이에요?")
    peers = writer.calls[-1][0]["company"]["same_industry_companies_for_reference"]
    assert len(peers) == 3
    assert "삼성전자" not in [p["name"] for p in peers]  # 자기 자신은 뺀다
    assert peers[0]["debt_ratio_percent"] == 40.0
    assert peers[0]["operating_margin_percent"] == 25.0
    assert peers[0]["revenue_change_from_last_year_percent"] == 20.0


def test_prompt_tells_how_to_answer_each_kind_of_question():
    prompt = chat_service.SYSTEM_PROMPT
    for rule in (
        "첫 문장이 곧 답이어야 해요",
        "회사를 하나씩 나열하지 않아요",  # 산업 질문
        "지어내지 말고",  # 실적 이유
        "수준을 묻는 질문",
        "시장 전체를 묻는 질문",
    ):
        assert rule in prompt


def test_questions_about_price_moves_get_price_news(client, writer, monkeypatch):
    """주가가 왜 움직였는지 물으면 주가 흐름과 크게 움직인 날의 기사를 찾아 넘긴다."""
    trend = {
        "biggest_daily_drops_in_last_month": [{"date": "2026-11-09", "change_percent": -17.0}],
        "biggest_daily_rises_in_last_month": [{"date": "2026-11-11", "change_percent": 4.1}],
    }
    asked = []

    def around(stock_code, got_trend, question):
        asked.append((stock_code, got_trend))
        return [{"date": "2026-11-09", "articles": []}]

    monkeypatch.setattr(chat_data, "_price_trend", lambda code: trend)
    monkeypatch.setattr(chat_data, "_news_around_big_moves", around)
    monkeypatch.setattr(chat_data, "_news_about_price", lambda code: [])
    login_as(client, 1)

    _ask(client, "매출이 얼마예요?")
    assert "price_trend" not in writer.calls[-1][0]["company"]

    _ask(client, "주가가 떨어졌던데 왜 그런 거예요?")
    company = writer.calls[-1][0]["company"]
    assert company["price_trend"] == trend
    assert company["news_on_days_price_moved_most"][0]["date"] == "2026-11-09"
    assert asked == [("005930", trend)]


def test_only_articles_named_in_the_answer_become_sources(client, writer, monkeypatch):
    news = [
        {
            "title": "알테오젠, 美 약가 규정에 급락",
            "press": "히트뉴스",
            "date": "2026-11-10",
            "summary": None,
            "url": "https://example.com/a",
        },
        {
            "title": "알테오젠 비만약 개발",
            "press": "한국경제",
            "date": "2026-11-16",
            "summary": None,
            "url": "https://example.com/b",
        },
    ]
    monkeypatch.setattr(chat_data, "_recent_news", lambda code: news)
    writer.reply = "11월 10일 히트뉴스 기사에 따르면 미국 약값 규정 때문에 떨어졌어요."
    login_as(client, 1)
    body = _ask(client, "왜 그런 거예요?").json()
    assert body["sources"] == [
        {
            "title": "알테오젠, 美 약가 규정에 급락",
            "press": "히트뉴스",
            "date": "2026-11-10",
            "url": "https://example.com/a",
        }
    ]
    # AI에게 넘기는 글에는 주소를 넣지 않는다
    assert "example.com" not in chat_service._last_message(writer.calls[-1][0], "질문")


def test_prompt_separates_past_reasons_from_predictions():
    prompt = chat_service.SYSTEM_PROMPT
    assert "이미 일어난 일의 이유를 묻는 것은 예측이 아니에요" in prompt
    assert "정말로 추천이나 앞날을 물었을 때만" in prompt


@pytest.mark.parametrize(
    ("question", "industries"),
    [
        ("반도체 회사들 지금 떨어지고있던데 왜그런거야", ["semiconductor"]),
        ("바이오주는 요즘 어때?", ["bio"]),
        ("배터리 회사랑 자동차 회사 중에 어디가 커요?", ["battery", "auto"]),
        ("인공지능 관련 회사가 뭐가 있어요?", ["ai"]),
        ("삼성전자 매출이 얼마예요?", []),
    ],
)
def test_industries_named_in_the_question_are_found(question, industries):
    assert chat_data.mentioned_industries(question, exclude=set()) == industries


def test_question_about_an_industry_gets_the_whole_industry(client, writer, monkeypatch):
    """회사 화면에서 "반도체 회사들"을 물으면 그 회사가 아니라 산업 전체의 자료를 넘긴다."""
    news = [
        {
            "title": "반도체주 급락",
            "press": "이투데이",
            "date": "2026-11-16",
            "summary": None,
            "url": "https://example.com/c",
        }
    ]
    today = {"companies_counted": 164, "companies_that_fell_today": 154}
    monkeypatch.setattr(chat_data, "_one_month_change", lambda code: -12.0)
    monkeypatch.setattr(chat_data, "_industry_news_about_price", lambda name: news)
    monkeypatch.setattr(chat_data, "_today_summary", lambda codes, seeds: today)
    login_as(client, 1)
    _ask(client, "반도체 회사들 지금 떨어지고있던데 왜그런거야")
    screen = writer.calls[-1][0]
    # 화면의 회사(삼성전자)는 깊은 자료 없이 어느 화면인지만
    assert "company" not in screen
    assert screen["company_on_screen"]["name"] == "삼성전자"
    industry = screen["industries_asked_about"][0]
    assert industry["name"] == "반도체"
    assert industry["all_companies_today"] == today
    month = industry["biggest_companies_over_1_month"]
    assert month["average_change_percent"] == -12.0
    assert month["companies_counted"] == chat_data.INDUSTRY_MOVERS
    assert 1 <= len(month["notable_companies"]) <= 3  # 회사를 다 나열하지 않는다
    assert industry["news_about_this_industry_price"] == news
    assert screen[chat_service.ASKED_KEY]["subject"] == "industry"

    # "바이오 산업 지금 어때"처럼 낱말에 "왜"가 없어도 산업 전체 자료를 붙인다 (숫자를 지어내지 않게)
    _ask(client, "바이오 산업 지금 어때", {"page": "main"})
    bio = writer.calls[-1][0]["industries_asked_about"][0]
    assert bio["name"] == "바이오" and "biggest_companies_over_1_month" in bio


def test_today_summary_counts_the_whole_industry(monkeypatch):
    """산업 전체의 오늘 모습: 평균, 오르고 떨어진 회사 수, 가장 많이 움직인 곳."""
    from datetime import datetime

    from app.services.market_clock import KST
    from app.services.naver import Quote

    rates = {"005930": -5.0, "000660": -8.0, "035420": 2.0, "035720": 0.0}
    quotes = {
        code: Quote(code, 1000, rate, 0, 0, datetime(2026, 11, 16, 15, 30, tzinfo=KST))
        for code, rate in rates.items()
    }
    monkeypatch.setattr(chat_data.naver, "quotes", lambda codes: quotes)
    summary = chat_data._today_summary(list(rates), chat_data.data_store._seed_companies())
    assert summary["companies_counted"] == 4
    assert summary["average_change_today_percent"] == -2.75
    assert (summary["companies_that_rose_today"], summary["companies_that_fell_today"]) == (1, 2)
    assert summary["fell_the_most_today"][0] == {"name": "SK하이닉스", "change_today_percent": -8.0}
    assert summary["rose_the_most_today"] == [{"name": "NAVER", "change_today_percent": 2.0}]


def test_hype_articles_are_not_used_as_evidence():
    class Article:
        def __init__(self, title):
            self.title = title

    kept = chat_data._usable(
        [
            Article("AI주 급락할 때 수익률 624%…“10배 오른다” 바이오주 5곳"),
            Article("알테오젠 목표주가 20% 낮춰 32만원"),
            Article("아시아증시, AI 거품 우려에 반도체주 급락"),
        ]
    )
    assert [a.title for a in kept] == ["아시아증시, AI 거품 우려에 반도체주 급락"]


# ---------------------------------------------------------------- 질문 의도


@pytest.mark.parametrize(
    ("question", "subject", "wants"),
    [
        ("바이오 산업 지금 어때", "industry", {"overview"}),
        ("반도체 회사들 왜 떨어졌어?", "industry", {"price_move", "news"}),
        ("알테오젠 떨어졌던데 왜그런거야?", "company", {"price_move", "news"}),
        ("삼성전자 지금 사도 돼?", "company", {"advice", "overview"}),
        ("이 회사 뭐하는 회사야?", "general", {"business"}),  # "회사야"를 "사야"로 읽지 않는다
        ("오늘 코스피 왜 빠졌어?", "market", {"price_move", "news"}),
        ("부채비율 45%면 높은 거예요?", "general", {"level", "financials"}),
    ],
)
def test_fallback_plan_reads_the_intent_from_words(question, subject, wants):
    """AI가 의도를 읽지 못했을 때 낱말로 어림한 의도."""
    seeds = chat_data.data_store._seed_companies()
    plan = chat_plan.fallback_plan(
        question,
        [seeds[c].name for c in chat_data.mentioned_companies(question, set())],
        chat_data.mentioned_industries(question, set()),
    )
    assert plan.subject == subject
    assert wants <= set(plan.wants)


class FakePlanner:
    def __init__(self, plan):
        self.result = plan
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_planner_decides_what_data_to_get(client, writer):
    """의도 읽기가 정한 회사·산업의 자료를 찾는다 (질문에 이름이 없어도)."""
    planner = FakePlanner(
        chat_plan.Plan(subject="company", companies=["SK하이닉스"], wants=["financials"])
    )
    app.dependency_overrides[chat_plan.get_planner] = lambda: planner
    login_as(client, 1)
    first = _ask(client, "삼성전자 매출 얼마야", {"page": "main"}).json()
    _ask(client, "그럼 거기는?", {"page": "main"}, first["conversation_id"])

    screen = writer.calls[-1][0]
    assert [c["name"] for c in screen["companies_asked_about"]] == ["SK하이닉스"]
    request = planner.requests[-1]
    assert request["question"] == "그럼 거기는?"
    assert request["previous"][0]["text"] == "삼성전자 매출 얼마야"  # 앞 대화도 같이 넘긴다
    assert {"id": "bio", "name": "바이오"} in request["industries"]


def test_broken_planner_falls_back_to_words(client, writer):
    app.dependency_overrides[chat_plan.get_planner] = lambda: FakePlanner(RuntimeError("AI 고장"))
    login_as(client, 1)
    assert _ask(client, "삼성전자랑 하이닉스 매출 비교해줘", {"page": "main"}).status_code == 200
    names = [c["name"] for c in writer.calls[-1][0]["companies_asked_about"]]
    assert names == ["삼성전자", "SK하이닉스"]


def test_no_target_question_gets_the_market(client, writer, monkeypatch):
    """ "요즘 뭐가 올랐어?"처럼 누구를 가리키지 않는 질문은 시장 전체 자료로 대답한다."""
    market = {"indices": [{"name": "코스피", "change_today_percent": -3.59}]}
    monkeypatch.setattr(chat_data, "_market_screen", lambda: market)
    login_as(client, 1)
    _ask(client, "요즘 뭐가 많이 올랐어?", {"page": "main"})
    assert writer.calls[-1][0]["stock_market_today"] == market
    _ask(client, "오늘 코스피 왜 떨어졌어?", COMPANY)
    screen = writer.calls[-1][0]
    assert screen["stock_market_today"] == market
    assert "company" not in screen  # 시장 질문에 화면의 회사 숫자를 길게 붙이지 않는다


# ---------------------------------------------------------------- 지어낸 숫자


SCREEN = {"company": {"name": "삼성전자", "price": 195000, "revenue": 333.61, "change": -12.8}}


@pytest.mark.parametrize(
    "answer",
    [
        "삼성전자 매출은 333.61조 원이에요.",
        "매출은 333.6조 원, 약 334조 원이에요.",  # 반올림
        "주가는 19만 5000원이고 한 달 동안 12.8% 내렸어요.",  # 우리말 단위, 부호 없이
        "2025년 11월 16일 기준이에요. 100원어치를 팔면 3곳 중 1곳이에요.",  # 날짜·개수·100
    ],
)
def test_numbers_from_the_data_are_fine(answer):
    assert chat_service.unknown_numbers(answer, SCREEN, "삼성전자 어때?") == []


def test_made_up_numbers_are_caught():
    answer = "삼성바이오로직스는 5.2% 올랐고, 셀트리온은 3.1% 내렸어요. 매출은 333.61조 원이에요."
    assert chat_service.unknown_numbers(answer, SCREEN, "어때?") == ["5.2", "3.1"]
    # 질문에 나온 숫자는 쓸 수 있다
    assert chat_service.unknown_numbers("부채비율 45%는 보통이에요.", SCREEN, "45%면 높아요?") == []


def test_answer_with_made_up_numbers_is_rewritten(client, writer):
    """자료에 없는 숫자가 있으면 한 번 다시 쓰게 하고, 그래도 있으면 그 문장만 뺀다."""
    replies = iter(
        [
            "삼성전자 매출은 999.9조 원이에요.",
            "삼성전자는 매출이 늘었어요. 영업이익은 888.8조 원이에요.",
        ]
    )
    writer.answer = lambda screen, history, question: next(replies)
    login_as(client, 1)
    body = _ask(client).json()
    assert body["answer"] == "삼성전자는 매출이 늘었어요."
    assert body["balance"] == 45


def test_answer_that_is_only_made_up_numbers_fails_and_is_free(client, writer):
    writer.reply = "매출은 999.9조 원이에요."
    login_as(client, 1)
    res = _ask(client)
    assert (res.status_code, res.json()["error"]["code"]) == (502, "CHAT_FAILED")
    assert _balance(client) == 50
