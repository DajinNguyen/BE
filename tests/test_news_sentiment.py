import asyncio

from app.core.config import Settings
from app.schemas.news import NewsArticle
from app.services.news_sentiment import (
    FALLBACK_REASON,
    FALLBACK_SENTIMENT,
    classify_news_sentiment,
)


def _article(article_id: str) -> NewsArticle:
    return NewsArticle(
        id=article_id,
        title="삼성전자, 3분기 최대 실적 발표",
        press="예시 뉴스",
        published_at="2026-10-24T09:00:00+09:00",
        url="https://example.com/news/1",
        summary_short="영업이익이 크게 늘었어요.",
        company_stock_code="005930",
        sentiment=FALLBACK_SENTIMENT,
        sentiment_reason=FALLBACK_REASON,
    )


def test_empty_list_is_returned_unchanged_without_ai_call():
    result = asyncio.run(classify_news_sentiment([], settings=Settings(anthropic_api_key="x")))
    assert result == []


def test_falls_back_to_neutral_without_any_api_key():
    articles = [_article("1"), _article("2")]
    settings = Settings(anthropic_api_key=None, gemini_api_key=None)

    result = asyncio.run(classify_news_sentiment(articles, settings=settings))

    assert len(result) == 2
    assert all(a.sentiment == "neutral" for a in result)
    assert all(a.sentiment_reason == FALLBACK_REASON for a in result)
    # 원본 객체는 바뀌지 않고, 새 객체를 돌려준다
    assert articles[0] is not result[0]


def test_falls_back_to_neutral_when_ai_call_raises(monkeypatch):
    import app.services.news_sentiment as module

    async def boom(settings, articles):
        raise RuntimeError("AI 호출 실패")

    monkeypatch.setattr(module, "_call_claude", boom)
    articles = [_article("1")]
    settings = Settings(anthropic_api_key="fake-key", gemini_api_key=None)

    result = asyncio.run(classify_news_sentiment(articles, settings=settings))

    assert result[0].sentiment == "neutral"
    assert result[0].sentiment_reason == FALLBACK_REASON


def test_merges_ai_result_into_articles(monkeypatch):
    import app.services.news_sentiment as module

    async def fake_call(settings, articles):
        return module.NewsSentimentBatch(
            items=[
                module.NewsSentimentResult(
                    id="1", sentiment="very_good", reason="실적이 크게 늘어서 좋은 소식이에요."
                )
            ]
        )

    monkeypatch.setattr(module, "_call_claude", fake_call)
    articles = [_article("1")]
    settings = Settings(anthropic_api_key="fake-key", gemini_api_key=None)

    result = asyncio.run(classify_news_sentiment(articles, settings=settings))

    assert result[0].sentiment == "very_good"
    assert result[0].sentiment_reason == "실적이 크게 늘어서 좋은 소식이에요."


def test_missing_article_in_ai_result_falls_back_to_neutral(monkeypatch):
    import app.services.news_sentiment as module

    async def fake_call(settings, articles):
        return module.NewsSentimentBatch(items=[])  # 모델이 기사를 빠뜨린 경우

    monkeypatch.setattr(module, "_call_claude", fake_call)
    articles = [_article("1")]
    settings = Settings(anthropic_api_key="fake-key", gemini_api_key=None)

    result = asyncio.run(classify_news_sentiment(articles, settings=settings))

    assert result[0].sentiment == "neutral"
    assert result[0].sentiment_reason == FALLBACK_REASON


def test_saved_report_news_without_sentiment_reads_as_neutral():
    """호재/악재를 넣기 전에 저장한 리포트의 상세 시트 관련 뉴스도 "보통"으로 읽힌다."""
    from app.schemas.report import KeyPointDetail, _migrate_point

    old = {
        "id": "old-1",
        "title": "옛 기사",
        "press": "한국경제",
        "published_at": "2026-11-01T09:00:00+09:00",
        "url": "https://www.hankyung.com/1",
        "summary_short": "",
        "company_stock_code": "005930",
    }
    point = _migrate_point({"title": "실적", "detail": {"related_news": [old]}}, "005930")
    detail = KeyPointDetail(**point["detail"])
    assert detail.related_news[0].sentiment == "neutral"


def _good(module, ids):
    return module.NewsSentimentBatch(
        items=[module.NewsSentimentResult(id=i, sentiment="good", reason="좋아요") for i in ids]
    )


def test_same_articles_are_not_asked_again(monkeypatch):
    """화면을 열 때마다 다시 물어보면 무료 한도·혼잡에 걸려서 전부 "보통"이 됐다."""
    import app.services.news_sentiment as module

    asked = []

    async def fake_call(settings, articles):
        asked.append([a.id for a in articles])
        return _good(module, [a.id for a in articles])

    monkeypatch.setattr(module, "_call_claude", fake_call)
    settings = Settings(anthropic_api_key="fake-key", gemini_api_key=None)
    first = _article("1")
    second = _article("2").model_copy(update={"url": "https://example.com/news/2"})

    asyncio.run(classify_news_sentiment([first], settings=settings))
    result = asyncio.run(classify_news_sentiment([first, second], settings=settings))

    assert [a.sentiment for a in result] == ["good", "good"]
    assert asked == [["1"], ["2"]]  # 두 번째에는 처음 보는 기사만 물어본다


def test_failed_result_is_not_remembered(monkeypatch):
    import app.services.news_sentiment as module

    calls = {"n": 0}

    async def flaky(settings, articles):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("AI 호출 실패")
        return _good(module, [a.id for a in articles])

    monkeypatch.setattr(module, "_call_claude", flaky)
    settings = Settings(anthropic_api_key="fake-key", gemini_api_key=None)
    assert asyncio.run(classify_news_sentiment([_article("1")], settings=settings))[
        0
    ].sentiment == ("neutral")
    # 실패는 기억하지 않아서 다음 요청에 다시 물어본다
    assert asyncio.run(classify_news_sentiment([_article("1")], settings=settings))[
        0
    ].sentiment == ("good")


def test_busy_model_is_asked_once_more(monkeypatch):
    import app.services.news_sentiment as module

    calls = {"n": 0}

    async def busy_then_ok(settings, articles):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
        return _good(module, [a.id for a in articles])

    monkeypatch.setattr(module, "_call_claude", busy_then_ok)
    monkeypatch.setattr(module, "RETRY_DELAY_SECONDS", 0)
    settings = Settings(anthropic_api_key="fake-key", gemini_api_key=None)
    result = asyncio.run(classify_news_sentiment([_article("1")], settings=settings))
    assert (result[0].sentiment, calls["n"]) == ("good", 2)
