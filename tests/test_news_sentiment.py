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
