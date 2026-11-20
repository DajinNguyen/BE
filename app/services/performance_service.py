"""재무 상세 "최근 사업 성과 분석": 최근 분기 실적과 그 이유.

숫자는 DART 주요계정(분기·반기·사업보고서)으로 그때그때 계산한다 (지어내지 않는다).
- 1~3분기는 각 보고서의 3개월 실적, 4분기는 사업보고서(연간)에서 3분기 누적을 뺀 값.
- 가장 최근 분기부터 이어지는 분기만 최대 8개. 매출이 10조 원 미만이면 억 원으로 보낸다.
- 발표일·출처는 그 분기 보고서의 DART 접수번호로 만든다.

"왜 이렇게 됐나요?"는 그 분기 실적 기사를 AI가 쉬운 말로 정리한다.
- 화면을 처음 열면 백그라운드에서 만들고, 다음에 열 때부터 채워진다. 새 분기 보고서가 나오면 다시 만든다.
- AI는 받은 숫자·기사에 있는 내용만 쓰고, 이유마다 근거 기사를 붙인다. 근거가 없는 이유는 버린다.
- 기사가 없거나 AI 키가 없으면 비워 둔다 (그 블록만 "자료가 없어요").

사업별 분기 실적(`segments`)은 DART API에 없어서 분기 보고서 원문에서 뽑는다 (`quarter_segment_service`).
"""

import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import anthropic
from google.genai import types as genai_types
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.errors import data_not_found
from app.models.performance import CompanyPerformanceRecord
from app.schemas.news import NewsArticle
from app.schemas.performance import (
    PerformanceChange,
    PerformanceDetail,
    PerformanceReason,
    PerformanceSource,
    PerformanceSummary,
    QuarterPerformance,
    ReasonEvidence,
)
from app.services import (
    ai_retry,
    dart,
    data_store,
    gemini_keys,
    news_search,
    quarter_segment_service,
)
from app.services.dart_financials import (
    BUSINESS_REPORT,
    OPERATING_INCOME_NAMES,
    REVENUE_NAMES,
    TRILLION,
    _Statement,
)
from app.services.market_clock import KST

logger = logging.getLogger(__name__)

# Claude 호출 상한 (초). 기본값(약 10분)이면 멈춘 호출이 계속 붙잡혀 있어서 Gemini 쪽과 같게 둔다
CLAUDE_TIMEOUT_SECONDS = 60

MAX_QUARTERS = 8
YEARS_BACK = 3  # 올해 포함 4개 해의 보고서를 본다 (연초에도 8개 분기가 나오도록)
# 분기 → (보고서 코드, 공시명)
REPORTS = {
    1: ("11013", "1분기보고서"),
    2: ("11012", "반기보고서"),
    3: ("11014", "3분기보고서"),
    4: (BUSINESS_REPORT, "사업보고서"),
}
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
HUNDRED_MILLION = 100_000_000
TRILLION_UNIT_FROM = 10 * TRILLION  # 최근 분기 매출이 이보다 작으면 억 원으로 보낸다
DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

NEWS_COUNT = 8  # AI에게 보여줄 기사 수
NEWS_WINDOW = timedelta(days=75)  # 분기가 끝난 뒤 이 기간 안에 나온 기사만 그 분기 실적 기사로 본다
NEWS_WORDS = ("분기", "실적", "영업이익", "영업익", "매출")  # 제목에 이 중 하나는 있어야 한다
FORECAST_WORDS = (
    "전망",
    "기대",
    "임박",
    "앞두고",
    "앞둔",
    "예상",
)  # 발표 전 예상 기사는 이유가 아니다
NEWS_SLICE = timedelta(days=15)  # 기사 기간을 이만큼씩 나눠서 찾는다
MAX_REASONS = 3
MAX_EVIDENCE = 2
MAX_SUMMARY_CHARS = 60  # 한 줄 요약이 이보다 길면 버린다
BANNED_WORDS = ("매수", "매도", "추천", "목표가", "사세요", "파세요")
GENERATION_TIMEOUT = timedelta(minutes=5)
RETRY_AFTER = timedelta(hours=6)  # 기사가 없었으면 이만큼 뒤에 다시 찾아본다
RETRY_FAILED_AFTER = timedelta(minutes=30)  # 검색·AI 가 실패했으면 이만큼 뒤에 다시 해 본다

SYSTEM_PROMPT = """\
당신은 주식을 처음 시작하는 10대 초보자에게 회사의 지난 분기 실적을 설명해 주는 선생님이에요.
사용자 메시지로 회사 이름, 분기, 실적 숫자, 그 분기 실적을 다룬 기사 목록(JSON)이 주어져요.
"왜 이렇게 됐나요?"에 들어갈 이유 2~3개와, 이번 분기를 한 문장으로 정리한 요약을 써요.

반드시 지킬 규칙:
- 주어진 숫자와 기사 제목·요약에 있는 내용만 써요. 없는 내용을 지어내지 마세요.
- 이유마다 근거가 된 기사 번호를 news_numbers 에 1~2개 넣어요. 근거 기사가 없는 이유는 쓰지 마세요.
- 기사에서 실적의 이유를 찾을 수 없으면 reasons 를 빈 배열로 돌려줘요.
- 지난 실적을 설명하기만 해요. 앞으로의 주가·실적 예측, 사라·팔라는 말, 목표 주가는 쓰지 마세요.
- "~해요" 체로, 전문 용어 없이 써요. 꼭 필요한 용어는 괄호로 짧게 풀어 줘요.
- title 은 30자 이내 한 문장, description 은 1~2문장이에요.
- summary 는 이번 분기 실적을 한눈에 읽히게 정리한 한 문장(40자 이내)이에요.
  숫자가 늘었다·줄었다는 결과만 쓰지 말고, 기사에서 확인되는 가장 큰 이유를 같이 써요.
  예: "AI용 메모리가 많이 팔려서 매출과 이익이 함께 늘었어요"
  퍼센트·금액 숫자는 쓰지 않아요. 기사에서 이유를 찾을 수 없으면 빈 문자열로 둬요.
"""


class ReasonDraft(BaseModel):
    title: str
    description: str
    news_numbers: list[int]


class ReasonsDraft(BaseModel):
    reasons: list[ReasonDraft]
    summary: str = ""


class ReasonWriter(Protocol):
    def write(self, context: dict[str, Any]) -> ReasonsDraft | None: ...


def _prompt(context: dict[str, Any]) -> str:
    return json.dumps(context, ensure_ascii=False)


class GeminiReasonWriter:
    _REQUEST_TIMEOUT_MS = 60_000

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: gemini_keys.FallbackClient | None = None

    def write(self, context: dict[str, Any]) -> ReasonsDraft | None:
        if self._client is None:
            self._client = gemini_keys.client(self._settings, self._REQUEST_TIMEOUT_MS)
        response = self._client.models.generate_content(
            model=self._settings.gemini_model,
            contents=_prompt(context),
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=ReasonsDraft,
            ),
        )
        return response.parsed if isinstance(response.parsed, ReasonsDraft) else None


class ClaudeReasonWriter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.Anthropic | None = None

    def write(self, context: dict[str, Any]) -> ReasonsDraft | None:
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=self._settings.anthropic_api_key, timeout=CLAUDE_TIMEOUT_SECONDS
            )
        response = self._client.messages.parse(
            model=self._settings.anthropic_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _prompt(context)}],
            output_format=ReasonsDraft,
        )
        return response.parsed_output


@lru_cache
def get_reason_writer() -> ReasonWriter | None:
    """Anthropic > Gemini. 키가 없으면 None (이유를 만들지 않는다)."""
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeReasonWriter(settings)
    if settings.gemini_api_key:
        return GeminiReasonWriter(settings)
    return None


# --------------------------------------------------------------------------
# 분기 실적 (DART)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Quarter:
    year: int
    q: int
    revenue: int  # 원
    operating_income: int  # 원
    rcept_no: str | None  # 그 분기 보고서 접수번호

    @property
    def label(self) -> str:
        return f"{self.year % 100:02d}.{self.q}Q"

    @property
    def end(self) -> date:
        return date(self.year, *QUARTER_END[self.q])


def _today() -> date:
    return datetime.now(KST).date()


def _statements(corp_code: str, today: date) -> dict[tuple[int, int], _Statement]:
    """(연도, 분기) → 보고서. 없는 보고서·받지 못한 보고서는 뺀다."""
    keys = [(y, q) for y in range(today.year - YEARS_BACK, today.year + 1) for q in REPORTS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = pool.map(lambda k: dart.single_accounts(corp_code, k[0], REPORTS[k[1]][0]), keys)
    return {k: _Statement(r) for k, r in zip(keys, rows, strict=True) if r}


def _fourth_quarter(
    names: tuple[str, ...], annual: _Statement, third: _Statement | None, earlier: list[int | None]
) -> int | None:
    """연간에서 3분기 누적을 뺀다. 누적이 없으면 1~3분기를 더해서 뺀다."""
    total = annual.value(names)
    cumulative = third.value(names, "thstrm_add_amount") if third else None
    if cumulative is None and all(v is not None for v in earlier):
        cumulative = sum(earlier)
    return None if total is None or cumulative is None else total - cumulative


def quarters(corp_code: str, today: date | None = None) -> list[Quarter]:
    """가장 최근 분기부터 끊기지 않고 이어지는 분기들 (오래된 분기 → 최근 분기, 최대 8개)."""
    statements = _statements(corp_code, today or _today())
    found: dict[tuple[int, int], Quarter] = {}
    for (year, q), statement in sorted(statements.items()):
        if q < 4:
            revenue = statement.value(REVENUE_NAMES)
            operating = statement.value(OPERATING_INCOME_NAMES)
        else:
            third = statements.get((year, 3))
            before = [found.get((year, n)) for n in (1, 2, 3)]
            revenue = _fourth_quarter(
                REVENUE_NAMES, statement, third, [b.revenue if b else None for b in before]
            )
            operating = _fourth_quarter(
                OPERATING_INCOME_NAMES,
                statement,
                third,
                [b.operating_income if b else None for b in before],
            )
        if revenue is None or operating is None:
            continue
        row = statement.row(REVENUE_NAMES) or {}
        found[(year, q)] = Quarter(year, q, revenue, operating, row.get("rcept_no"))
    if not found:
        return []
    year, q = max(found)
    chain = []
    while (year, q) in found and len(chain) < MAX_QUARTERS:
        chain.append(found[(year, q)])
        year, q = (year, q - 1) if q > 1 else (year - 1, 4)
    return chain[::-1]


def _rate(current: int, base: int | None) -> float | None:
    """변화율(%). 기준이 없거나 0 이하(적자)면 비율이 뜻이 없어서 None."""
    if base is None or base <= 0:
        return None
    return round((current - base) / base * 100, 1)


def _change(current: Quarter, base: Quarter | None) -> PerformanceChange | None:
    if base is None:
        return None
    return PerformanceChange(
        revenue_rate=_rate(current.revenue, base.revenue),
        operating_income_rate=_rate(current.operating_income, base.operating_income),
    )


def _margin(q: Quarter) -> float:
    return round(q.operating_income / q.revenue * 100, 1) if q.revenue else 0.0


def _source(company_name: str, latest: Quarter) -> PerformanceSource | None:
    if not latest.rcept_no:
        return None
    report = REPORTS[latest.q][1]
    return PerformanceSource(
        title=f"{company_name} {latest.year}년 {report}",
        date=_report_date(latest.rcept_no),
        url=DART_VIEWER + latest.rcept_no,
    )


def _report_date(rcept_no: str) -> str:
    """접수번호 앞 8자리가 공시일이다."""
    return f"{rcept_no[:4]}-{rcept_no[4:6]}-{rcept_no[6:8]}"


def _scaler(latest: Quarter) -> tuple[str, Callable[[int], float]]:
    """화면 단위와 원 → 화면 단위 변환. 최근 분기 매출이 10조 원 미만이면 억 원."""
    in_trillion = latest.revenue >= TRILLION_UNIT_FROM
    unit, divisor, digits = ("조 원", TRILLION, 2) if in_trillion else ("억 원", HUNDRED_MILLION, 0)
    return unit, lambda won: round(won / divisor, digits)


def same_quarter_last_year(chain: list[Quarter]) -> Quarter | None:
    latest = chain[-1]
    return next((c for c in chain if (c.year, c.q) == (latest.year - 1, latest.q)), None)


def previous_quarter(chain: list[Quarter]) -> Quarter | None:
    """바로 앞 분기 (같은 해 2·3분기일 때 누적 빼기에 쓴다)."""
    return chain[-2] if len(chain) > 1 else None


def build_detail(company_name: str, chain: list[Quarter]) -> PerformanceDetail:
    latest = chain[-1]
    unit, scale = _scaler(latest)

    by_key = {(c.year, c.q): c for c in chain}
    previous = (latest.year, latest.q - 1) if latest.q > 1 else (latest.year - 1, 4)
    source = _source(company_name, latest)
    return PerformanceDetail(
        period_label=f"{latest.year}년 {latest.q}분기",
        announced_at=source.date if source else latest.end.isoformat(),
        unit=unit,
        summary=PerformanceSummary(
            revenue=scale(latest.revenue),
            operating_income=scale(latest.operating_income),
            operating_margin=_margin(latest),
        ),
        yoy=_change(latest, by_key.get((latest.year - 1, latest.q))),
        qoq=_change(latest, by_key.get(previous)),
        quarterly=[
            QuarterPerformance(
                quarter=c.label,
                revenue=scale(c.revenue),
                operating_income=scale(c.operating_income),
                operating_margin=_margin(c),
            )
            for c in chain
        ],
        segments=[],
        reasons=[],
        source=source,
    )


def _chain(stock_code: str) -> tuple[str, list[Quarter]]:
    company = data_store.get_company(stock_code)  # 없는 회사면 404
    chain = quarters(company.corp_code) if company.corp_code else []
    if not chain:
        raise data_not_found()
    return company.name, chain


def get_detail(session: Session, stock_code: str) -> tuple[PerformanceDetail, str | None]:
    """응답과 근거 보고서 접수번호. 실적 자료가 없으면 404 `DATA_NOT_FOUND`."""
    name, chain = _chain(stock_code)
    detail = build_detail(name, chain)
    rcept_no = chain[-1].rcept_no
    record = session.get(CompanyPerformanceRecord, stock_code)
    if record is not None and record.rcept_no == rcept_no:
        reasons, summary = saved(record)
        detail = detail.model_copy(update={"reasons": reasons, "ai_summary": summary})
    items = quarter_segment_service.saved_items(session, stock_code, rcept_no)
    if items:
        segments = quarter_segment_service.to_segments(items, _scaler(chain[-1])[1])
        detail = detail.model_copy(update={"segments": segments})
    return detail, rcept_no


def latest_quarter(stock_code: str) -> tuple[str, list[Quarter]]:
    """(회사 이름, 분기 목록). 사업별 성과를 만들 때 쓴다."""
    return _chain(stock_code)


def saved(record: CompanyPerformanceRecord) -> tuple[list[PerformanceReason], str | None]:
    """저장해 둔 (이유 목록, 한 줄 요약). 예전 기록은 이유 목록만 있다."""
    content = record.content
    if isinstance(content, list):
        content = {"reasons": content}
    if not isinstance(content, dict):
        return [], None
    reasons = [PerformanceReason(**r) for r in content.get("reasons") or []]
    return reasons, content.get("summary") or None


def to_summary(draft: ReasonsDraft, reasons: list[PerformanceReason]) -> str | None:
    """한 줄 요약. 근거가 된 이유가 하나도 없거나 예측·권유 표현, 숫자가 있으면 버린다."""
    text = " ".join(draft.summary.split())
    if not text or not reasons or len(text) > MAX_SUMMARY_CHARS:
        return None
    if any(w in text for w in BANNED_WORDS) or re.search(r"\d", text):
        return None
    return text


# --------------------------------------------------------------------------
# 왜 이렇게 됐나요? (AI, 백그라운드)
# --------------------------------------------------------------------------


def _compact(text: str) -> str:
    return "".join(text.split())


def _slices(first: date, last: date) -> list[tuple[date, date]]:
    """[first, last] 를 NEWS_SLICE 씩 나눈다."""
    slices, start = [], first
    while start < last:
        end = min(start + NEWS_SLICE, last)
        slices.append((start, end))
        start = end
    return slices


def _news_score(title: str, name: str, q: int) -> int:
    """실적 발표 기사일수록 높다: 그 분기를 말하고, 영업이익을 말하고, 회사 이름으로 시작한다."""
    score = 0
    if f"{q}분기" in title or f"{q}Q" in title:
        score += 2
    if "영업이익" in title or "영업익" in title:
        score += 1
    if title.startswith(name):
        score += 1
    return score


def quarter_news(stock_code: str, latest: Quarter) -> list[NewsArticle] | None:
    """그 분기 실적을 다룬 기사 (실적 발표 기사 먼저). 검색이 모두 실패하면 None.

    분기가 끝난 뒤 실적 발표 철에 나왔고, 제목에 회사 이름과 실적 관련 말이 있는 기사만 쓴다.
    발표 전 "전망" 기사는 뺀다. Google 뉴스는 한 번에 최대 50개만, 그것도 최근 기사 위주로 줘서
    기간을 한 번에 찾으면 정작 실적 발표 무렵 기사가 빠진다. 그래서 기간을 나눠서 찾는다.
    """
    name = data_store.news_query(stock_code)
    compact_name = _compact(name)
    first = latest.end
    last = min(latest.end + NEWS_WINDOW, _today())
    slices = _slices(first, last)
    if not slices:
        return []

    def fits(a: NewsArticle) -> bool:
        title = _compact(a.title)
        return (
            first < date.fromisoformat(a.published_at[:10]) <= last
            and compact_name in title
            and any(w in title for w in NEWS_WORDS)
            and not any(w in title for w in FORECAST_WORDS)
        )

    found: dict[str, NewsArticle] = {}
    failed = 0
    # 실적 발표가 몰리는 기간(분기 끝 15~45일 뒤)부터 찾고, 발표 기사가 충분히 모이면 멈춘다
    # (회사마다 검색을 여러 번 하면 Google 뉴스가 막힌다)
    order = slices[1:3] + slices[3:] + slices[:1]
    for between in order:
        result = news_search.search(
            f"{name} {latest.q}분기 실적", 1, 50, stock_code, sort="sim", between=between
        )
        if result is None:
            failed += 1
            continue
        for a in result.articles:
            if fits(a):
                found.setdefault(_compact(a.title), a)  # 같은 제목은 한 번만
        strong = [
            a for a in found.values() if _news_score(_compact(a.title), compact_name, latest.q) >= 3
        ]
        if len(strong) >= NEWS_COUNT:
            break
    if failed == len(order):
        return None
    ranked = sorted(
        found.values(),
        key=lambda a: (-_news_score(_compact(a.title), compact_name, latest.q), a.published_at),
    )
    return ranked[:NEWS_COUNT]


def to_reasons(draft: ReasonsDraft, news: list[NewsArticle]) -> list[PerformanceReason]:
    """근거 기사가 없거나 예측·권유 표현이 있는 이유는 버린다."""
    reasons = []
    for r in draft.reasons:
        text = f"{r.title} {r.description}"
        cited = [news[n - 1] for n in dict.fromkeys(r.news_numbers) if 1 <= n <= len(news)]
        if not r.title.strip() or not cited or any(w in text for w in BANNED_WORDS):
            continue
        reasons.append(
            PerformanceReason(
                title=r.title.strip(),
                description=r.description.strip(),
                evidence=[
                    ReasonEvidence(type="news", label=a.press, date=a.published_at[:10], url=a.url)
                    for a in cited[:MAX_EVIDENCE]
                ],
            )
        )
    return reasons[:MAX_REASONS]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def start_generation(
    session: Session, stock_code: str, rcept_no: str | None, now: datetime | None = None
) -> bool:
    """이유를 만들어야 하면 "만드는 중"으로 표시하고 True."""
    now = now or datetime.now(UTC)
    record = session.get(CompanyPerformanceRecord, stock_code)
    if record is not None and record.rcept_no == rcept_no:
        reasons, summary = saved(record)
        if record.status == "done" and reasons and summary:
            return False  # 다 만들었다. 요약만 없으면(예전 기록 등) 아래 간격으로 다시 만들어 본다
        since = record.started_at if record.status == "generating" else record.updated_at
        wait = {"generating": GENERATION_TIMEOUT, "failed": RETRY_FAILED_AFTER}.get(
            record.status, RETRY_AFTER
        )
        if since is not None and now - _as_utc(since) < wait:
            return False
    record = record or CompanyPerformanceRecord(stock_code=stock_code, status="generating")
    record.status = "generating"
    record.rcept_no = rcept_no
    record.started_at = now
    session.add(record)
    session.commit()
    return True


def run_generation(
    stock_code: str, writer: ReasonWriter, session_factory: Callable[[], Session]
) -> str:
    """백그라운드 작업: 이유를 만들어 저장한다. "done" | "failed"."""
    status, reasons, summary, rcept_no = "failed", [], None, None
    try:
        name, chain = _chain(stock_code)
        latest = chain[-1]
        rcept_no = latest.rcept_no
        news = quarter_news(stock_code, latest)
        if news is None:  # 검색이 막혔다 (기사가 없는 것과 달리 곧 다시 해 본다)
            logger.warning("실적 기사 검색이 막혔어요: %s", stock_code)
        elif news:
            detail = build_detail(name, chain)
            context = {
                "company": name,
                **detail.model_dump(include={"period_label", "unit", "summary", "yoy", "qoq"}),
                "quarterly": [q.model_dump() for q in detail.quarterly],
                "news": [
                    {
                        "number": n,
                        "title": a.title,
                        "summary": a.summary_short,
                        "press": a.press,
                        "date": a.published_at[:10],
                    }
                    for n, a in enumerate(news, start=1)
                ],
            }
            draft = ai_retry.call(writer.write, context)
            if draft is not None:
                reasons = to_reasons(draft, news)
                summary = to_summary(draft, reasons)
                status = "done"
        else:
            status = "done"  # 기사가 없으면 비워 두고 나중에 다시 찾아본다
    except Exception:  # noqa: BLE001 - 백그라운드 작업이라 실패해도 숫자는 그대로 보인다
        logger.exception("Performance reasons crashed: %s", stock_code)
    with session_factory() as session:
        record = session.get(CompanyPerformanceRecord, stock_code) or CompanyPerformanceRecord(
            stock_code=stock_code, status=status
        )
        record.status = status
        if status == "done" or record.rcept_no != rcept_no:
            record.content = {"reasons": [r.model_dump() for r in reasons], "summary": summary}
        record.rcept_no = rcept_no
        record.started_at = None
        record.updated_at = datetime.now(UTC)
        session.add(record)
        session.commit()
    return status
