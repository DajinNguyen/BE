"""뉴스 호재/악재(sentiment) 판단.

요청마다 AI(Claude 또는 Gemini)로 새로 계산한다 — 저장하지 않는다 (PM 결정).
기사 목록 전체를 한 번에 AI에 보내 호재/악재 수준과 이유를 받아온다 (기사 하나당 호출하지 않는다).
AI 키가 없거나 호출이 실패·시간 초과하면 화면이 항상 뜨도록 모든 기사를 "보통"으로 표시한다.
"""

import asyncio
import json
import logging
from typing import Literal

import anthropic
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.schemas.news import NewsArticle

logger = logging.getLogger(__name__)

SentimentLevel = Literal["very_good", "good", "neutral", "bad", "very_bad"]

FALLBACK_SENTIMENT: SentimentLevel = "neutral"
FALLBACK_REASON = "지금은 판단하기 어려워요"

# AI 응답을 무한정 기다리지 않도록 호출 하나당 상한을 둔다 (단위: 초).
REQUEST_TIMEOUT_SECONDS = 20.0

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
    client = genai.Client(api_key=settings.gemini_api_key)
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


async def classify_news_sentiment(
    articles: list[NewsArticle], settings: Settings | None = None
) -> list[NewsArticle]:
    """기사 목록 전체를 한 번에 분류해서 `sentiment`/`sentiment_reason` 이 채워진 새 목록을 돌려준다.

    목록이 비어 있으면 AI를 부르지 않고 그대로 돌려준다.
    AI 키가 없거나 호출이 실패·시간 초과하면 모든 기사를 "보통"으로 대체한다 (항상 화면이 뜨도록).
    """
    if not articles:
        return articles

    settings = settings or get_settings()

    try:
        if settings.anthropic_api_key:
            batch = await asyncio.wait_for(
                _call_claude(settings, articles), timeout=REQUEST_TIMEOUT_SECONDS
            )
        elif settings.gemini_api_key:
            batch = await asyncio.wait_for(
                _call_gemini(settings, articles), timeout=REQUEST_TIMEOUT_SECONDS
            )
        else:
            return _fallback(articles)
    except Exception:  # noqa: BLE001 - AI 실패는 화면을 막지 않고 "보통"으로 대체한다
        logger.exception("News sentiment classification failed, falling back to neutral")
        return _fallback(articles)

    results = {r.id: r for r in batch.items}
    updated = []
    for a in articles:
        result = results.get(a.id)
        if result is None:
            # 모델이 일부 기사를 빠뜨렸으면 그 기사만 "보통"으로 대체한다
            updated.append(
                a.model_copy(
                    update={"sentiment": FALLBACK_SENTIMENT, "sentiment_reason": FALLBACK_REASON}
                )
            )
        else:
            updated.append(
                a.model_copy(
                    update={"sentiment": result.sentiment, "sentiment_reason": result.reason}
                )
            )
    return updated
