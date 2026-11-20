"""뉴스 호재/악재(sentiment) 판단.

AI(Claude 또는 Gemini)로 계산하고 DB에는 저장하지 않는다 (PM 결정).
기사 목록을 한 번에 AI에 보내 호재/악재 수준과 이유를 받아온다 (기사 하나당 호출하지 않는다).

- 같은 기사는 같은 판단이라, 판단한 결과를 서버 메모리에 `CACHE_TTL` 동안 기억한다.
  예전에는 화면을 열 때마다 모든 기사를 다시 물어봐서, 무료 한도(분당 15번)와 Gemini 혼잡(503)에
  자주 걸렸고, 그때마다 모든 기사가 "보통 · 지금은 판단하기 어려워요"로 보였다.
- 처음 보는 기사만 AI에 보낸다. 혼잡(503)·한도(429)면 잠깐 쉬고 한 번 더 물어본다.
- 그래도 실패하면 그 기사들만 "보통"으로 보여주고 기억하지 않는다 (다음 요청에 다시 물어본다).
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Literal

import anthropic
from google.genai import types as genai_types
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.schemas.news import NewsArticle
from app.services import gemini_keys

logger = logging.getLogger(__name__)

SentimentLevel = Literal["very_good", "good", "neutral", "bad", "very_bad"]

FALLBACK_SENTIMENT: SentimentLevel = "neutral"
FALLBACK_REASON = "지금은 판단하기 어려워요"

# AI 응답을 무한정 기다리지 않도록 호출 하나당 상한을 둔다 (단위: 초).
REQUEST_TIMEOUT_SECONDS = 20.0
RETRY_DELAY_SECONDS = 2.0  # 혼잡·한도 오류 뒤 다시 물어보기 전에 쉬는 시간
RETRY_ERRORS = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded", "rate_limit")
CACHE_TTL = 12 * 60 * 60  # 판단한 결과를 기억하는 시간 (초)
CACHE_SIZE = 5_000

# (기사 주소, 제목) → (기억한 시각, 수준, 이유)
_cache: "OrderedDict[tuple[str, str], tuple[float, str, str]]" = OrderedDict()

SYSTEM_PROMPT = """\
당신은 주식을 처음 시작하는 10대 초보자에게 뉴스가 좋은 소식인지 나쁜 소식인지 설명해 주는 선생님이에요.
사용자 메시지로 뉴스 기사 목록(JSON, 각 항목은 id·title·summary_short·company_stock_code)이 주어지면,
기사마다 아래 5단계 중 정확히 하나를 고르고, 왜 그렇게 판단했는지 쉬운 말로 1문장을 써요.

단계 기준:
- very_good (아주 좋음): 사상 최대 실적/이익, 대형 신규 계약·수주, 신용등급 상향 등
- good (좋음): 실적 개선, 신사업 진출, 목표가 상향, 배당 확대 등
- neutral (보통): 단순 사실 전달(인사·행사·정기 공시), 호재/악재로 보기 애매한 경우
- bad (나쁨): 실적 부진, 소송 제기, 일부 사업 철수, 경영진 경미한 구설수
- very_bad (아주 나쁨): 경영진 횡령·스캔들성 해임/사임, 대규모 리콜·사고, 중대한 제재·과징금, 부도설

반드시 지킬 규칙:
- 입력으로 받은 기사 전부에 대해 결과를 하나씩 돌려줘요. id는 입력과 똑같이 맞춰요.
- reason은 "~해요" 체로, 전문 용어 없이 한 문장으로 짧게 써요.
- 제목·요약에 없는 내용을 지어내지 마세요.
"""


class NewsSentimentResult(BaseModel):
    id: str
    sentiment: SentimentLevel
    reason: str


class NewsSentimentBatch(BaseModel):
    items: list[NewsSentimentResult]


def _fallback(articles: list[NewsArticle]) -> list[NewsArticle]:
    return [
        a.model_copy(update={"sentiment": FALLBACK_SENTIMENT, "sentiment_reason": FALLBACK_REASON})
        for a in articles
    ]


def _request_payload(articles: list[NewsArticle]) -> str:
    return json.dumps(
        [
            {
                "id": a.id,
                "title": a.title,
                "summary_short": a.summary_short,
                "company_stock_code": a.company_stock_code,
            }
            for a in articles
        ],
        ensure_ascii=False,
    )


def _max_tokens(count: int) -> int:
    # 기사 하나당 넉넉하게 토큰을 잡아 둔다 (id + sentiment + 짧은 reason 한 문장).
    return min(16000, 500 + 200 * count)


async def _call_claude(settings: Settings, articles: list[NewsArticle]) -> NewsSentimentBatch:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.parse(
        model=settings.anthropic_model,
        max_tokens=_max_tokens(len(articles)),
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _request_payload(articles)}],
        output_format=NewsSentimentBatch,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        raise ValueError(
            f"Unexpected Claude sentiment response: stop_reason={response.stop_reason}"
        )
    return response.parsed_output


async def _call_gemini(settings: Settings, articles: list[NewsArticle]) -> NewsSentimentBatch:
    client = gemini_keys.client(settings)
    response = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=_request_payload(articles),
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=NewsSentimentBatch,
        ),
    )
    if response.parsed is None:
        raise ValueError("Unexpected Gemini sentiment response: no parsed output")
    return response.parsed


def _key(article: NewsArticle) -> tuple[str, str]:
    return (article.url, article.title)


def _remembered(article: NewsArticle) -> tuple[str, str] | None:
    hit = _cache.get(_key(article))
    if hit is None or time.time() - hit[0] > CACHE_TTL:
        return None
    return hit[1], hit[2]


def _remember(article: NewsArticle, sentiment: str, reason: str) -> None:
    _cache[_key(article)] = (time.time(), sentiment, reason)
    _cache.move_to_end(_key(article))
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def clear_cache() -> None:
    _cache.clear()


def _retryable(error: Exception) -> bool:
    """혼잡·한도 오류만 다시 물어본다 (시간 초과는 다시 기다리면 화면이 너무 늦어진다)."""
    if isinstance(error, asyncio.TimeoutError):
        return False
    return any(word in str(error) for word in RETRY_ERRORS)


async def _ask(settings: Settings, articles: list[NewsArticle]) -> NewsSentimentBatch | None:
    """AI에 물어본다. 키가 없으면 None. 혼잡·한도 오류면 한 번 더 물어본다."""
    if settings.anthropic_api_key:
        call = _call_claude
    elif settings.gemini_api_key:
        call = _call_gemini
    else:
        return None
    try:
        return await asyncio.wait_for(call(settings, articles), timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001
        if not _retryable(e):
            raise
        logger.warning("뉴스 호재/악재 판단이 혼잡해서 다시 물어봐요: %s", str(e)[:80])
    await asyncio.sleep(RETRY_DELAY_SECONDS)
    return await asyncio.wait_for(call(settings, articles), timeout=REQUEST_TIMEOUT_SECONDS)


async def classify_news_sentiment(
    articles: list[NewsArticle], settings: Settings | None = None
) -> list[NewsArticle]:
    """`sentiment`/`sentiment_reason` 이 채워진 새 목록을 돌려준다.

    목록이 비어 있으면 AI를 부르지 않고 그대로 돌려준다. 기억해 둔 기사는 AI에 다시 묻지 않는다.
    AI 키가 없거나 호출이 실패·시간 초과하면 새 기사만 "보통"으로 대체한다 (항상 화면이 뜨도록).
    """
    if not articles:
        return articles

    settings = settings or get_settings()
    known = {a.id: _remembered(a) for a in articles}
    unknown = list({a.id: a for a in articles if known[a.id] is None}.values())

    results: dict[str, NewsSentimentResult] = {}
    if unknown:
        try:
            batch = await _ask(settings, unknown)
        except Exception:  # noqa: BLE001 - AI 실패는 화면을 막지 않고 "보통"으로 대체한다
            logger.warning("News sentiment classification failed, falling back to neutral")
            batch = None
        if batch is not None:
            results = {r.id: r for r in batch.items}
            for a in unknown:
                if a.id in results:
                    _remember(a, results[a.id].sentiment, results[a.id].reason)

    updated = []
    for a in articles:
        if known[a.id] is not None:
            sentiment, reason = known[a.id]
        elif a.id in results:
            sentiment, reason = results[a.id].sentiment, results[a.id].reason
        else:  # AI가 실패했거나 일부 기사를 빠뜨렸으면 그 기사만 "보통"
            sentiment, reason = FALLBACK_SENTIMENT, FALLBACK_REASON
        updated.append(a.model_copy(update={"sentiment": sentiment, "sentiment_reason": reason}))
    return updated
