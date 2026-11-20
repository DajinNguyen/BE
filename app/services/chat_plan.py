"""챗봇 질문의 의도 읽기: 무엇에 대한 질문이고 어떤 자료가 필요한지 먼저 정한다.

AI에게 짧게 한 번 물어서 정하고(`Planner`), 그게 안 되면 낱말로 어림한다(`fallback_plan`).
여기서 정한 대로 `chat_data` 가 자료를 찾는다. 대답은 그 자료로 따로 쓴다.
"""

import json
import logging
import re
from functools import lru_cache
from typing import Any, Literal, Protocol

import anthropic
from google.genai import types as genai_types
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.services import data_store, gemini_keys

logger = logging.getLogger(__name__)

Subject = Literal["company", "industry", "market", "term", "general"]
Want = Literal[
    "overview",  # 요즘 어떤지, 전반적인 상황
    "price_move",  # 주가가 왜 오르고 떨어졌는지, 주가 흐름
    "news",  # 최근 소식, 무슨 일이 있었는지
    "level",  # 높은지 낮은지, 괜찮은 수준인지 (견줄 기준이 필요)
    "financials",  # 매출·이익·재무 숫자, 실적이 왜 그런지
    "business",  # 무슨 일을 하는 회사·산업인지
    "term",  # 낱말의 뜻
    "advice",  # 살지 팔지, 앞으로 오를지 (대답하지 않는 질문)
]


class Plan(BaseModel):
    subject: Subject = "general"
    companies: list[str] = []  # 질문이 가리키는 회사 이름 (사용자가 쓴 그대로 또는 정식 이름)
    industry_ids: list[str] = []  # 질문이 가리키는 산업 id
    wants: list[Want] = []


PLANNER_PROMPT = """\
당신은 주식 초보자용 앱의 챗봇이 대답하기 전에, 질문이 무엇을 묻는지 정리하는 역할이에요.
사용자 메시지(JSON)에는 question(질문), screen(지금 보고 있는 화면), previous(바로 앞 대화), industries(산업 목록)가 있어요.

정할 것:
- subject: 질문이 무엇에 대한 것인지.
  - company: 특정 회사 한두 곳 ("삼성전자 왜 떨어졌어", "이 회사 뭐 해요")
  - industry: 산업이나 그 산업의 여러 회사 ("반도체 회사들 왜 떨어져", "바이오 산업 어때", "2차전지주")
  - market: 주식 시장 전체 ("오늘 시장 왜 빠져", "코스피 왜 올랐어")
  - term: 낱말의 뜻 ("PER이 뭐예요")
  - general: 그 밖의 주식 공부 질문이나 잡담
- companies: 질문이 가리키는 회사 이름들. 줄임말은 정식 이름으로 바꿔요 (삼전→삼성전자, 하닉→SK하이닉스, 현차→현대차).
  질문에 회사 이름이 없고 "이 회사", "여기", "이 종목"처럼 화면의 회사를 가리키면 screen 의 회사 이름을 넣어요.
  "그럼 거기는?", "그 회사는?"처럼 앞 대화의 회사를 가리키면 previous 에서 찾아 넣어요.
  산업이나 시장 전체를 묻는 질문이면 비워 둬요. 화면에 회사가 있다는 이유만으로 넣지 않아요.
- industry_ids: 질문이 가리키는 산업. industries 목록의 id 만 써요. "이 산업"처럼 화면의 산업을 가리키면 screen 의 산업 id.
- wants: 대답에 필요한 자료를 모두 골라요.
  - overview: "어때?", "요즘 어때", "상황이 어때"처럼 전반적인 상황
  - price_move: 주가가 오르고 떨어진 것, 그 이유 ("왜 떨어졌어", "왜 올라", "주가 어때")
  - news: 최근 소식, 무슨 일
  - level: 높은지 낮은지, 괜찮은지, 많은 편인지
  - financials: 매출·영업이익·부채 같은 숫자, 실적이 왜 그런지
  - business: 무슨 일을 하는지, 무엇으로 돈을 버는지
  - term: 낱말의 뜻
  - advice: 살지 팔지, 앞으로 오를지 떨어질지 (이미 일어난 일의 이유를 묻는 것은 advice 가 아니에요)
"""


class Planner(Protocol):
    def plan(self, request: dict[str, Any]) -> Plan | None: ...


class GeminiPlanner:
    _REQUEST_TIMEOUT_MS = 10_000  # Gemini 가 받는 가장 짧은 값

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: gemini_keys.FallbackClient | None = None

    def plan(self, request: dict[str, Any]) -> Plan | None:
        if self._client is None:
            self._client = gemini_keys.client(self._settings, self._REQUEST_TIMEOUT_MS)
        response = self._client.models.generate_content(
            model=self._settings.gemini_model,
            contents=json.dumps(request, ensure_ascii=False),
            config=genai_types.GenerateContentConfig(
                system_instruction=PLANNER_PROMPT,
                response_mime_type="application/json",
                response_schema=Plan,
            ),
        )
        return response.parsed if isinstance(response.parsed, Plan) else None


class ClaudePlanner:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.Anthropic | None = None

    def plan(self, request: dict[str, Any]) -> Plan | None:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        response = self._client.messages.parse(
            model=self._settings.anthropic_model,
            max_tokens=500,
            system=PLANNER_PROMPT,
            messages=[{"role": "user", "content": json.dumps(request, ensure_ascii=False)}],
            output_format=Plan,
        )
        return response.parsed_output


@lru_cache
def get_planner() -> Planner | None:
    """Anthropic > Gemini. 키가 없으면 None (낱말로 어림한다)."""
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudePlanner(settings)
    if settings.gemini_api_key:
        return GeminiPlanner(settings)
    return None


# --------------------------------------------------------------------------
# 낱말로 어림하기 (AI 없이, 또는 AI가 답하지 못했을 때)
# --------------------------------------------------------------------------

_WHY = re.compile(r"왜|이유|때문|원인|어째서|어쩌다|무슨 ?일|뉴스|소식|기사|이슈")
_OVERVIEW = re.compile(r"어때|어떤가|어떻|상황|분위기|전망|요즘|최근|지금")
_LEVEL = re.compile(
    r"높|낮|많은|적은|큰 ?편|작은 ?편|평균|수준|괜찮|양호|위험|안전|보통|좋은 ?(편|거|건)|나쁜|정도면|면 ?어때"
)
_PRICE = re.compile(
    r"주가|떨어졌|떨어진|떨어지|떨어져|내렸|내린|하락|급락|폭락|빠졌|빠진|빠지|올랐|오른|올라|오르|상승|급등|폭등|뛰었|움직"
)
_FINANCIALS = re.compile(r"매출|영업이익|이익|실적|부채|적자|흑자|재무|roe|per|pbr|벌었|벌어")
_BUSINESS = re.compile(r"뭐 ?하는|무슨 ?일을|어떤 ?회사|뭘 ?만|뭘 ?파|무엇으로|사업")
_TERM = re.compile(r"(이|가)? ?뭐(예요|야|임|지|에요)|뜻|의미|무슨 ?말")
_ADVICE = re.compile(
    r"살까|팔까|사도 ?(돼|될|되)|팔아도|(?<!회)사야 ?(해|돼|하|할|되)|팔아야|추천|찍어 ?줘|더 ?(오를|떨어질)|오를까|떨어질까|언제 ?사"
)
_MARKET = re.compile(r"코스피|코스닥|증시|시장|장이|주식 ?시장")


def fallback_plan(question: str, companies: list[str], industry_ids: list[str]) -> Plan:
    """질문의 낱말로 어림한 의도. 회사·산업은 `chat_data` 가 이름으로 찾은 것을 받는다."""
    text = question.lower()
    wants: list[Want] = []
    for want, pattern in (
        ("advice", _ADVICE),
        ("price_move", _PRICE),
        ("news", _WHY),
        ("overview", _OVERVIEW),
        ("level", _LEVEL),
        ("financials", _FINANCIALS),
        ("business", _BUSINESS),
        ("term", _TERM),
    ):
        if pattern.search(text):
            wants.append(want)
    if industry_ids and not companies:
        subject: Subject = "industry"
    elif companies:
        subject = "company"
    elif _MARKET.search(text):
        subject = "market"
    elif wants == ["term"]:
        subject = "term"
    else:
        subject = "general"
    return Plan(subject=subject, companies=companies, industry_ids=industry_ids, wants=wants)


def industry_list() -> list[dict[str, str]]:
    return [{"id": r["industry_id"], "name": r["name"]} for r in data_store.industry_records()]
