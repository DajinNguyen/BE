"""재무 탭 "무엇으로 돈을 버나요?": 사업부문·제품별 매출을 DART 사업보고서 표에서 뽑는다.

시드(`app/data/financials.json`)에 사업부문별 매출이 없는 회사만 만든다.
- 재무 탭을 처음 열면 백그라운드에서 만들기 시작하고, 다음에 열 때부터 채워진다.
- 새 사업보고서가 공시되면(접수번호가 바뀌면) 다시 만든다.
- `python -m app.scripts.generate_segments` 로 미리 한꺼번에 만들 수도 있다.

AI는 사업보고서 "주요 제품"·"매출실적" 표에서 부문 이름과 금액을 **표에 적힌 그대로** 옮기기만 한다.
서버는 그 금액이 원문에 실제로 있는지 확인하고, 표의 단위를 조 원으로 바꾼 뒤,
합계가 DART 연간 매출과 크게 다르면(단위를 잘못 읽은 경우 등) 버린다.
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import anthropic
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.models.overview import CompanySegmentsRecord
from app.schemas.financials import Financials, SegmentRevenue, Segments
from app.services import dart, data_store

logger = logging.getLogger(__name__)

EXCERPT_ANCHORS = ("주요 제품", "매출실적", "매출 실적", "매출 및 수주", "매출현황", "부문별 매출")
EXCERPT_WINDOW = 7_000  # 표 제목부터 이만큼 (표가 길면 잘릴 수 있다)
MAX_EXCERPT_CHARS = 24_000
MAX_SEGMENTS = 6
MIN_SHARE = 0.01  # 전체의 1% 안 되는 항목은 화면에 "0.0조 원"으로 보여서 뺀다
# 부문 합계 ÷ DART 연간 매출이 이 범위를 벗어나면 단위·기간을 잘못 읽은 것으로 본다.
# 부문 매출은 내부거래를 빼기 전이라 연간 매출보다 클 수 있다.
SUM_RATIO_RANGE = (0.5, 1.5)
UNITS = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8, "십억원": 1e9, "조원": 1e12}
GENERATION_TIMEOUT = timedelta(minutes=5)
RETRY_FAILED_AFTER = timedelta(hours=6)

NO_SOURCE_REASON = "이 회사의 사업보고서를 아직 불러오지 못했어요."
NOT_FOUND_REASON = "사업보고서에서 사업별 매출 표를 찾지 못했어요."
FAILED_REASON = "사업별 매출을 정리하는 중에 문제가 생겼어요."

SYSTEM_PROMPT = """\
사용자 메시지로 한 회사 사업보고서의 "주요 제품"·"매출실적" 부분(표는 " | " 로 칸이 나뉜 글)이 주어져요.
가장 최근 한 해(당기) 연결 기준 매출을 사업부문별(없으면 주요 제품·서비스별)로 표에서 옮겨 적어요.

반드시 지킬 규칙:
- revenue 는 표에 적힌 숫자를 쉼표까지 그대로 옮겨요 (예: "102,268"). 계산하거나 더하거나 반올림하지 마세요.
- unit 은 그 표의 금액 단위를 그대로 적어요 (예: "백만원", "억원", "천원", "원").
- 합계·소계·내부거래·연결조정·기타 조정 줄은 빼요. 비율(%)이나 수량은 금액이 아니에요.
- 총매출액과 내부매출을 뺀 순매출액(외부 매출)이 같이 있으면 순매출액을 써요.
- 수출·내수, 국내·해외처럼 지역으로만 나눈 표밖에 없으면 found 를 false 로 해요.
- 사업부문별 매출을 찾지 못하면 found 를 false, items 를 빈 목록으로 둬요.
- name 은 초보자가 알아보기 쉬운 짧은 한국어 이름이에요 (예: "조선·해양", "보안 소프트웨어"). 20자 안쪽.
- period 는 그 금액의 사업연도 네 자리예요 (예: "2025").
- items 는 금액이 큰 순서로 최대 6개예요.
"""


class SegmentDraft(BaseModel):
    name: str
    revenue: str  # 표에 적힌 그대로


class SegmentExtraction(BaseModel):
    """AI가 쓰는 부분."""

    found: bool
    period: str
    unit: str
    items: list[SegmentDraft]


class SegmentsUnavailable(Exception):
    """자료가 없거나 표를 찾지 못했다. 메시지는 실패 이유로 저장한다."""


class SegmentWriter(Protocol):
    def write(self, excerpt: str) -> SegmentExtraction: ...


class GeminiSegmentWriter:
    _REQUEST_TIMEOUT_MS = 60_000

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: genai.Client | None = None

    def write(self, excerpt: str) -> SegmentExtraction:
        if self._client is None:
            self._client = genai.Client(
                api_key=self._settings.gemini_api_key,
                http_options=genai_types.HttpOptions(timeout=self._REQUEST_TIMEOUT_MS),
            )
        response = self._client.models.generate_content(
            model=self._settings.gemini_model,
            contents=excerpt,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=SegmentExtraction,
            ),
        )
        if not isinstance(response.parsed, SegmentExtraction):
            raise SegmentsUnavailable(FAILED_REASON)
        return response.parsed


class ClaudeSegmentWriter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.Anthropic | None = None

    def write(self, excerpt: str) -> SegmentExtraction:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        response = self._client.messages.parse(
            model=self._settings.anthropic_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": excerpt}],
            output_format=SegmentExtraction,
        )
        if response.parsed_output is None:
            raise SegmentsUnavailable(FAILED_REASON)
        return response.parsed_output


@lru_cache
def get_segment_writer() -> SegmentWriter | None:
    """Anthropic > Gemini. 키가 없으면 None (사업별 매출을 만들지 않는다)."""
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeSegmentWriter(settings)
    if settings.gemini_api_key:
        return GeminiSegmentWriter(settings)
    return None


# --------------------------------------------------------------------------
# 자료 모으기 · 결과 확인
# --------------------------------------------------------------------------


def excerpt(text: str) -> str:
    """사업의 내용에서 "주요 제품"·"매출실적" 표 부분만 (못 찾으면 앞부분)."""
    spans: list[tuple[int, int]] = []
    for anchor in EXCERPT_ANCHORS:
        start = text.find(anchor)
        if start >= 0:
            spans.append((max(start - 200, 0), start + EXCERPT_WINDOW))
    if not spans:
        return text[:MAX_EXCERPT_CHARS]
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return "\n...\n".join(text[s:e] for s, e in merged)[:MAX_EXCERPT_CHARS]


def _tidy_name(name: str) -> str:
    """ "용 역" 처럼 표에서 글자마다 띄운 이름은 붙인다."""
    name = name.strip()
    if re.fullmatch(r"\S( \S)+", name):
        name = name.replace(" ", "")
    return name[:20]


def _tidy(items: list[SegmentRevenue]) -> list[SegmentRevenue]:
    """이름을 다듬고, 큰 순서로 최대 MAX_SEGMENTS 개, 아주 작은 항목은 뺀다."""
    total = sum(s.revenue for s in items)
    kept = [
        s.model_copy(update={"name": _tidy_name(s.name)})
        for s in items
        if total and s.revenue / total >= MIN_SHARE
    ]
    return sorted(kept, key=lambda s: s.revenue, reverse=True)[:MAX_SEGMENTS]


def _unit_factor(unit: str) -> float | None:
    return UNITS.get(re.sub(r"[\s()（）:단위]", "", unit))


def to_segments(
    extraction: SegmentExtraction, source: str, annual_revenue: float | None
) -> Segments:
    """AI가 옮긴 표를 확인해서 조 원 단위 사업별 매출로. 믿을 수 없으면 SegmentsUnavailable."""
    factor = _unit_factor(extraction.unit)
    if not extraction.found or factor is None:
        raise SegmentsUnavailable(NOT_FOUND_REASON)
    items: list[SegmentRevenue] = []
    names: set[str] = set()
    for i, draft in enumerate(extraction.items):
        raw = draft.revenue.strip()
        name = _tidy_name(draft.name)
        if not re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d+)?|\d+(\.\d+)?", raw) or raw not in source:
            continue  # 원문에 없는 숫자는 쓰지 않는다
        value = float(raw.replace(",", "")) * factor / 1e12
        if value <= 0 or not name or name in names:
            continue
        names.add(name)
        items.append(SegmentRevenue(code=f"SEG{i + 1}", name=name, revenue=round(value, 4)))
    items = _tidy(items)
    if not items:
        raise SegmentsUnavailable(NOT_FOUND_REASON)
    if annual_revenue:
        ratio = sum(s.revenue for s in items) / annual_revenue
        if not SUM_RATIO_RANGE[0] <= ratio <= SUM_RATIO_RANGE[1]:
            logger.warning("사업별 매출 합계가 연간 매출과 달라서 버려요 (비율 %.2f)", ratio)
            raise SegmentsUnavailable(NOT_FOUND_REASON)
    year = re.search(r"\d{4}", extraction.period)
    label = f"{year.group()}년 연간 기준" if year else "최근 사업보고서 기준"
    return Segments(base_label=label, items=items)


def _annual_revenue(financials: Financials | None, period: str) -> float | None:
    """표의 사업연도와 같은 해 DART 연간 매출 (조 원). 없으면 가장 최근 해."""
    if not financials or not financials.annual:
        return None
    by_year = {str(a.fiscal_year): a.revenue for a in financials.annual}
    year = re.search(r"\d{4}", period)
    if year and year.group() in by_year:
        return by_year[year.group()]
    return max(financials.annual, key=lambda a: a.fiscal_year).revenue


# --------------------------------------------------------------------------
# 화면에 붙이기 · 생성 상태
# --------------------------------------------------------------------------


def needs_segments(financials: Financials) -> bool:
    return financials.segments is None or not financials.segments.items


def apply_segments(session: Session, financials: Financials) -> Financials:
    """만들어 둔 사업별 매출이 있으면 채운다 (시드 값이 있으면 시드가 먼저)."""
    if not needs_segments(financials):
        return financials
    record = session.get(CompanySegmentsRecord, financials.stock_code)
    if record is None or not record.content:  # 다시 만드는 중·실패해도 예전 값은 보여준다
        return financials
    segments = Segments(**record.content)
    segments = segments.model_copy(update={"items": _tidy(segments.items)})
    return financials.model_copy(update={"segments": segments})


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def is_outdated(record: CompanySegmentsRecord, corp_code: str) -> bool:
    report = dart.latest_annual_report(corp_code)
    return report is not None and bool(record.rcept_no) and report.rcept_no != record.rcept_no


def start_generation(
    session: Session, financials: Financials, corp_code: str | None, now: datetime | None = None
) -> bool:
    """만들어야 하면 "만드는 중"으로 표시하고 True."""
    if not needs_segments(financials) or not corp_code:
        return False
    now = now or datetime.now(UTC)
    record = session.get(CompanySegmentsRecord, financials.stock_code)
    if record is not None:
        if record.status == "done" or record.failure_reason == NOT_FOUND_REASON:
            # 표가 없던 보고서는 새 사업보고서가 나올 때까지 다시 해 봐도 같다
            if not is_outdated(record, corp_code):
                return False
        else:
            since = record.started_at if record.status == "generating" else record.updated_at
            wait = GENERATION_TIMEOUT if record.status == "generating" else RETRY_FAILED_AFTER
            if since is not None and now - _as_utc(since) < wait:
                return False
    else:
        record = CompanySegmentsRecord(stock_code=financials.stock_code, status="generating")
    record.status = "generating"
    record.started_at = now
    record.failure_reason = None
    session.add(record)
    session.commit()
    return True


def _save(session_factory: Callable[[], Session], stock_code: str, **fields: Any) -> None:
    with session_factory() as session:
        record = session.get(CompanySegmentsRecord, stock_code) or CompanySegmentsRecord(
            stock_code=stock_code, status=fields["status"]
        )
        for key, value in fields.items():
            setattr(record, key, value)
        record.started_at = None
        record.updated_at = datetime.now(UTC)
        session.add(record)
        session.commit()


def run_generation(
    stock_code: str, writer: SegmentWriter, session_factory: Callable[[], Session]
) -> str:
    """백그라운드 작업: 사업별 매출을 만들어 저장한다. "done" | "failed"."""
    rcept_no = None
    try:
        company = data_store.get_company(stock_code)
        report = dart.latest_annual_report(company.corp_code) if company.corp_code else None
        text = dart.business_section(report.rcept_no) if report else None
        if report is None or not text:
            raise SegmentsUnavailable(NO_SOURCE_REASON)
        rcept_no = report.rcept_no
        source = excerpt(text)
        extraction = writer.write(source)
        financials = data_store.find_financials(stock_code)
        segments = to_segments(extraction, source, _annual_revenue(financials, extraction.period))
    except SegmentsUnavailable as e:
        # 같은 사업보고서에서 다시 봐도 없으면 예전 값도 지운다 (AI 오류와 달리 다시 해도 같다)
        _save(
            session_factory,
            stock_code,
            status="failed",
            failure_reason=str(e),
            rcept_no=rcept_no,
            content=None,
        )
        return "failed"
    except Exception:
        logger.exception("Segment generation crashed: %s", stock_code)
        _save(session_factory, stock_code, status="failed", failure_reason=FAILED_REASON)
        return "failed"
    _save(
        session_factory,
        stock_code,
        status="done",
        content=json.loads(segments.model_dump_json()),
        rcept_no=rcept_no,
        failure_reason=None,
    )
    return "done"
