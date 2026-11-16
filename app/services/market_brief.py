"""메인 "오늘의 시장" 한마디·움직인 이유를 AI로 쓴다.

숫자(지수·산업 등락률)와 기사는 서버가 실제 자료로 넘기고, AI는 문장만 쓴다.
산업과 기사는 넘겨준 목록에서 번호·id로만 고르게 해서, 없는 산업이나 기사를 붙이지 않는다.
"""

import json
import logging
from functools import lru_cache
from typing import Any, Protocol

import anthropic
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
당신은 주식을 처음 시작하는 10대 초보자에게 오늘 주식 시장을 알려주는 선생님이에요.
사용자 메시지로 오늘의 코스피·코스닥 지수, 산업별 등락률, 시가총액 큰 회사의 등락률, 시장 기사 목록(JSON)이 주어져요.
그 자료만 근거로 시장 한마디와 시장이 움직인 이유를 써요.

반드시 지킬 규칙:
- 숫자는 주어진 값만 그대로 쓰세요. 새 숫자를 만들거나 추정하지 마세요.
- "사세요", "파세요", "지금이 기회" 같은 매수·매도 권유나 앞으로의 주가 전망은 쓰지 마세요.
- 모든 문장은 "~해요" 체로, 전문 용어는 쉬운 말로 풀어서 쓰세요.
- is_open 이 true 면 아직 장이 열려 있어요. "올랐어요" 대신 "오르고 있어요", "떨어졌어요" 대신 "떨어지고 있어요"처럼 진행형으로 써요.

작성 가이드:
- headline: 오늘 시장이 "왜" 그렇게 움직였는지를 한눈에 읽히는 짧은 한 문장(공백 포함 30자 안쪽)으로 써요.
  - 지수가 오르고 내린 결과만 쓰면 안 돼요. 등락률은 바로 옆에 숫자로 보여요.
    나쁜 예: "코스피는 내리고 코스닥은 올랐어요", "코스피가 1.2% 올랐어요"
  - 이유 하나만 골라요. 더 크게 움직인 지수의 가장 큰 이유 하나면 돼요. 두 지수를 다 설명하려고 길게 쓰지 않아요.
  - 기사에서 확인되는 이유가 있으면 그것을, 없으면 biggest_companies(시장별 시가총액 큰 회사의 오늘 등락률)와
    most_rising_industries / most_falling_industries 에서 찾아요. 지수는 큰 회사를 따라 움직여요.
  - 무엇이 오르고 내렸는지 분명하게 "주가가 떨어졌다", "주가가 올랐다"고 써요.
    좋은 예: "큰 반도체 회사 주가가 떨어지면서 코스피도 떨어졌어요" (29자)
    좋은 예: "로봇 회사 주가가 올라 코스닥이 올랐어요" (22자)
    좋은 예: "미국 금리 인하 기대에 코스피가 올랐어요" (22자)
    나쁜 예: "큰 반도체 회사가 떨어져서 코스피는 떨어지고, 코스닥은 올랐어요" (무엇이 내렸는지, 코스닥이 왜 올랐는지 알 수 없어요)
  - 지수나 주가가 낮아진 것은 "내렸어요"가 아니라 "떨어졌어요"(장중이면 "떨어지고 있어요")라고 써요.
  - 앞뒤가 맞아야 해요. 지수가 내렸으면 내리게 한 이유를, 올랐으면 오르게 한 이유를 써요.
  - "혼조세", "강세", "약세", "차익 실현", "매수세" 같은 증권 기사 말투와 퍼센트 숫자는 쓰지 않아요.
- reasons: 시장이 움직인 이유 0~3개. 기사에서 확인되는 이유만 쓰고, 근거 기사가 없으면 빈 목록으로 둬요.
  - title: 한 줄 결론 (25자 안쪽)
  - description: 쉬운 말로 1문장
  - industry_ids: 관련 산업. industries 목록에 있는 industry_id 만 0~2개 골라요.
  - news_numbers: 근거 기사. news 목록의 number 만 1~2개 골라요.
"""


class BriefReason(BaseModel):
    title: str
    description: str
    industry_ids: list[str]
    news_numbers: list[int]


class MarketBrief(BaseModel):
    """AI가 쓰는 부분."""

    headline: str
    reasons: list[BriefReason]


class BriefWriter(Protocol):
    def write(self, context: dict[str, Any]) -> MarketBrief | None: ...


class GeminiBriefWriter:
    _REQUEST_TIMEOUT_MS = 45_000

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: genai.Client | None = None

    def write(self, context: dict[str, Any]) -> MarketBrief | None:
        if self._client is None:
            self._client = genai.Client(
                api_key=self._settings.gemini_api_key,
                http_options=genai_types.HttpOptions(timeout=self._REQUEST_TIMEOUT_MS),
            )
        try:
            response = self._client.models.generate_content(
                model=self._settings.gemini_model,
                contents=json.dumps(context, ensure_ascii=False),
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=MarketBrief,
                ),
            )
        except (
            Exception
        ) as e:  # noqa: BLE001 - 한도·서버 오류·타임아웃 모두 다음 갱신 때 다시 해 본다
            logger.warning("Gemini 시장 한마디를 만들지 못했어요: %s", e)
            return None
        return response.parsed if isinstance(response.parsed, MarketBrief) else None


class ClaudeBriefWriter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.Anthropic | None = None

    def write(self, context: dict[str, Any]) -> MarketBrief | None:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        try:
            response = self._client.messages.parse(
                model=self._settings.anthropic_model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                output_format=MarketBrief,
            )
        except (anthropic.AnthropicError, TypeError) as e:
            logger.warning("Claude 시장 한마디를 만들지 못했어요: %s", e)
            return None
        return response.parsed_output


@lru_cache
def get_brief_writer() -> BriefWriter | None:
    """Anthropic > Gemini. 키가 없으면 None (지수로 만든 기본 문장을 쓴다)."""
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeBriefWriter(settings)
    if settings.gemini_api_key:
        return GeminiBriefWriter(settings)
    return None
