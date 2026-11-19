"""재무 상세 "최근 사업 성과 분석"의 "사업별 성과": 최근 분기 사업부문별 매출·영업이익.

DART API 에는 사업별 분기 실적이 없어서, 분기·반기 보고서 원문의 재무제표 주석 "영업부문"에서 뽑는다.
이 주석에는 사업부문별 "당분기 3개월"과 "전분기(작년 같은 분기) 3개월" 값이 있다.

- 재무 상세를 처음 열면 백그라운드에서 만들고, 다음에 열 때부터 채워진다. 새 분기 보고서가 나오면 다시 만든다.
- AI는 표의 숫자를 **적힌 그대로** 옮기기만 한다. 서버는 그 숫자가 원문에 실제로 있는지 확인하고,
  사업별 매출 합계가 그 분기 DART 매출과 크게 다르면(누적 값을 잘못 읽은 경우 등) 버린다.
- 사업부문이 하나뿐인 회사(예: SK하이닉스)는 회사 전체 실적을 그 사업 하나로 보여준다.
- 한 줄 설명(comment)은 보고서 "사업의 내용"에 적힌 사실로만 쓰고, 예측·권유·금액이 있으면 버린다.
- 4분기(사업보고서)에는 3개월 값이 없어서 만들지 않는다.
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal, Protocol

import anthropic
from google.genai import types as genai_types
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.models.performance import CompanyQuarterSegmentsRecord
from app.schemas.performance import SegmentPerformance
from app.services import ai_retry, dart, gemini_keys

logger = logging.getLogger(__name__)

UNITS = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8, "십억원": 1e9, "조원": 1e12}
# 사업별 매출 합계 ÷ 그 분기 DART 매출. 부문 매출은 내부거래를 빼기 전이라 조금 클 수 있다.
# 누적(6·9개월) 값을 잘못 옮기면 2~3배가 되어 걸러진다.
SUM_RATIO_RANGE = (0.6, 1.6)
MAX_SEGMENTS = 6
MIN_SHARE = 0.01
MAX_COMMENT_CHARS = 40
MAX_BUSINESS_CHARS = 8_000
COMMENT_BANNED = ("매수", "매도", "추천", "목표가", "전망", "예정", "예상", "것으로 보")
GENERATION_TIMEOUT = timedelta(minutes=5)
RETRY_FAILED_AFTER = timedelta(minutes=30)

NO_SOURCE_REASON = "분기 보고서를 아직 불러오지 못했어요."
NOT_FOUND_REASON = "분기 보고서에서 사업별 실적을 찾지 못했어요."
FAILED_REASON = "사업별 실적을 정리하는 중에 문제가 생겼어요."

SYSTEM_PROMPT = """\
사용자 메시지로 한 회사 분기 보고서의 [영업부문 주석]과 [사업의 내용]이 주어져요 (표는 " | " 로 칸이 나뉜 글).
이번 분기 사업부문별 실적을 표에서 옮겨 적어요.

kind 고르기:
- "segments": 영업부문(보고부문)이 둘 이상이고 부문별 매출 표가 있어요.
- "products": 보고부문은 하나뿐이지만 [영업부문 주석]에 제품·용역별 매출 표가 있어요. 그 표를 써요.
- "single": 보고부문이 하나뿐("단일 영업부문" 등)이고 [영업부문 주석]에 제품·용역별 매출 표도 없어요.
- "none": 위 어느 것도 확인할 수 없어요.
지역별(국내·해외, 국가별) 표나 주요 고객 표는 쓰지 않아요.

반드시 지킬 규칙:
- 숫자는 표에 적힌 그대로 쉼표·괄호까지 옮겨요 (예: "52,054,489", "(825,216)"). 계산·반올림하지 마세요.
- 표는 한 줄이 한 행이에요. 머리 행의 "3개월"·"누적" 순서를 보고 숫자가 어느 열인지 맞춰요.
- "당분기"(또는 "당반기") 표에서:
  revenue = "3개월" 매출, revenue_cumulative = "누적" 매출 (열 이름 없이 하나만 있으면 누적으로 봐요)
  operating_income = "3개월" 영업이익, operating_income_cumulative = "누적" 영업이익
- "전분기"(또는 "전반기", 작년 같은 기간) 표에서:
  prior_revenue = "3개월" 매출, prior_revenue_cumulative = "누적" 매출
- 표에 없는 칸은 "" 로 둬요. 3개월 열이 없는 표도 많아요. 그때는 누적 칸만 채워요.
- table_name 은 표에 적힌 부문·제품 이름 그대로예요 (예: "DS 부문").
- unit 은 그 표의 금액 단위 그대로예요 (예: "백만원", "원").
- 합계·부문 합계·내부거래·연결조정 줄과 칸은 빼요. items 는 매출이 큰 순서로 최대 6개예요.
- name 은 표의 이름을 그대로 쓰지 말고 초보자가 알아보는 짧은 말로 바꿔요 (12자 안쪽).
  영어 약자는 괄호로 풀고, "부문"·"매출액"·"수익" 같은 말은 붙이지 않아요.
  예: "DS 부문" → "DS(반도체)", "DX 부문" → "DX(가전·스마트폰)", "용역의 제공으로 인한 수익" → "기술·용역",
  "상품매출액" → "상품 판매"
- comment 는 [사업의 내용]에 적힌 사실로 한 문장(30자 안쪽, "~해요" 체)을 써요.
  이번 분기에 그 사업에 있었던 일이 적혀 있으면 그것을, 없으면 그 사업이 무엇을 파는지 써요.
  "영위", "제공으로 인한" 같은 어려운 말 대신 쉬운 말을 써요. 근거가 없으면 "" 로 둬요.
  앞으로의 예측, 사라·팔라는 말, 금액·퍼센트 숫자는 쓰지 마세요.
- kind 가 "single" 이면 items 는 비우고 single_name(사업 이름)과 single_comment 를 채워요.
"""


class QuarterSegmentDraft(BaseModel):
    name: str
    table_name: str = ""  # 표에 적힌 이름 (앞 분기 보고서와 맞춰 볼 때 쓴다)
    revenue: str = ""  # 당분기 3개월
    revenue_cumulative: str = ""  # 당분기 누적
    prior_revenue: str = ""  # 작년 같은 분기 3개월
    prior_revenue_cumulative: str = ""  # 작년 같은 기간 누적
    operating_income: str = ""
    operating_income_cumulative: str = ""
    comment: str = ""


class QuarterSegmentExtraction(BaseModel):
    """AI가 쓰는 부분."""

    kind: Literal["segments", "products", "single", "none"]
    unit: str = ""
    items: list[QuarterSegmentDraft] = []
    single_name: str = ""
    single_comment: str = ""


class SegmentsUnavailable(Exception):
    """자료가 없거나 표를 믿을 수 없다. 같은 보고서로는 다시 해도 같다."""


class QuarterSegmentWriter(Protocol):
    def write(self, source: str) -> QuarterSegmentExtraction | None: ...


class GeminiQuarterSegmentWriter:
    _REQUEST_TIMEOUT_MS = 60_000

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: gemini_keys.FallbackClient | None = None

    def write(self, source: str) -> QuarterSegmentExtraction | None:
        if self._client is None:
            self._client = gemini_keys.client(self._settings, self._REQUEST_TIMEOUT_MS)
        response = self._client.models.generate_content(
            model=self._settings.gemini_model,
            contents=source,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=QuarterSegmentExtraction,
            ),
        )
        parsed = response.parsed
        return parsed if isinstance(parsed, QuarterSegmentExtraction) else None


class ClaudeQuarterSegmentWriter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.Anthropic | None = None

    def write(self, source: str) -> QuarterSegmentExtraction | None:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        response = self._client.messages.parse(
            model=self._settings.anthropic_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": source}],
            output_format=QuarterSegmentExtraction,
        )
        return response.parsed_output


@lru_cache
def get_quarter_segment_writer() -> QuarterSegmentWriter | None:
    """Anthropic > Gemini. 키가 없으면 None (사업별 성과를 만들지 않는다)."""
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeQuarterSegmentWriter(settings)
    if settings.gemini_api_key:
        return GeminiQuarterSegmentWriter(settings)
    return None


# --------------------------------------------------------------------------
# 결과 확인
# --------------------------------------------------------------------------


def source_text(note: str, business: str) -> str:
    return f"[영업부문 주석]\n{note}\n\n[사업의 내용]\n{business[:MAX_BUSINESS_CHARS]}"


def _unit_factor(unit: str) -> float | None:
    return UNITS.get(re.sub(r"[\s()（）:단위]", "", unit))


def _amount(raw: str, source: str, factor: float) -> int | None:
    """표에 적힌 숫자 → 원. 원문에 없거나 숫자가 아니면 None. "(825,216)" 은 음수."""
    raw = raw.strip()
    if not raw or raw not in source:
        return None
    match = re.fullmatch(r"(\(|-|△)?\s*(\d{1,3}(?:,\d{3})*|\d+)\)?", raw)
    if match is None:
        return None
    value = int(match.group(2).replace(",", "")) * factor
    return round(-value if match.group(1) else value)


def _comment(text: str) -> str:
    """보고서에 근거한 한 줄 설명. 길거나 예측·권유·금액이 있으면 버린다."""
    text = " ".join(text.split())
    if len(text) > MAX_COMMENT_CHARS or any(w in text for w in COMMENT_BANNED):
        return ""
    if re.search(r"\d\s*(%|퍼센트|조|억|만|원)", text):
        return ""
    return text


def needs_previous(extraction: QuarterSegmentExtraction, quarter: int) -> bool:
    """3개월 열이 없는 표라서 앞 분기 보고서의 누적 값을 빼야 하는지 (2·3분기만)."""
    return (
        quarter in (2, 3)
        and extraction.kind in ("segments", "products")
        and any(not d.revenue.strip() and d.revenue_cumulative.strip() for d in extraction.items)
    )


AMOUNT_FIELDS = ("revenue", "prior_revenue", "operating_income")


def as_cumulative(extraction: QuarterSegmentExtraction) -> QuarterSegmentExtraction:
    """3개월 칸에 누적 값을 잘못 옮긴 표로 보고, 모든 값을 누적으로 다시 읽는다.

    누적 칸이 비어 있으면 3개월 칸 값을 누적으로 옮기고 3개월 칸은 비운다.
    """
    items = []
    for d in extraction.items:
        changes: dict[str, str] = {}
        for field in AMOUNT_FIELDS:
            changes[f"{field}_cumulative"] = getattr(d, f"{field}_cumulative").strip() or getattr(
                d, field
            )
            changes[field] = ""
        items.append(d.model_copy(update=changes))
    return extraction.model_copy(update={"items": items})


def _revenue_sum(extraction: QuarterSegmentExtraction, source: str) -> int:
    factor = _unit_factor(extraction.unit)
    if factor is None:
        return 0
    values = (_amount(d.revenue, source, factor) for d in extraction.items)
    return sum(v for v in values if v and v > 0)


def suspect_cumulative(
    extraction: QuarterSegmentExtraction, source: str, revenue: int, quarter: int
) -> bool:
    """2·3분기인데 3개월 칸 합계가 분기 매출보다 훨씬 크면 누적 값을 옮긴 것으로 의심한다.

    (예: SK텔레콤 3분기 "무선 9.86조" — 분기 매출 4.4조의 약 3배)
    """
    return (
        quarter in (2, 3)
        and extraction.kind in ("segments", "products")
        and revenue > 0
        and _revenue_sum(extraction, source) / revenue > SUM_RATIO_RANGE[1]
    )


def _key(draft: QuarterSegmentDraft) -> str:
    return "".join((draft.table_name or draft.name).split())


def to_items(
    extraction: QuarterSegmentExtraction,
    source: str,
    *,
    revenue: int,
    operating_income: int,
    prior_revenue: int | None,
    company_name: str,
    quarter: int = 1,
    previous: QuarterSegmentExtraction | None = None,
    previous_source: str = "",
) -> list[dict[str, Any]]:
    """AI가 옮긴 표를 확인해서 저장할 사업별 실적(원 단위)으로. 믿을 수 없으면 SegmentsUnavailable.

    revenue·operating_income·prior_revenue 는 그 분기(와 작년 같은 분기) DART 회사 전체 값이다.
    표에 3개월 열이 없으면 누적에서 앞 분기 보고서(previous)의 누적을 빼서 구한다 (1분기는 누적 = 3개월).
    표 전체를 누적으로 옮긴 것 같으면(합계가 분기 매출의 몇 배) 누적으로 다시 읽어서 앞 분기 누적을 뺀다.
    """
    totals = {
        "revenue": revenue,
        "operating_income": operating_income,
        "prior_revenue": prior_revenue,
        "company_name": company_name,
        "quarter": quarter,
        "previous_source": previous_source,
    }
    try:
        return _to_items(extraction, source, previous=previous, **totals)
    except SegmentsUnavailable:
        if previous is None or not suspect_cumulative(extraction, source, revenue, quarter):
            raise
    logger.info("3개월 칸에 누적 값이 들어간 것 같아서 누적으로 다시 읽어요")
    return _to_items(as_cumulative(extraction), source, previous=as_cumulative(previous), **totals)


def _to_items(
    extraction: QuarterSegmentExtraction,
    source: str,
    *,
    revenue: int,
    operating_income: int,
    prior_revenue: int | None,
    company_name: str,
    quarter: int,
    previous: QuarterSegmentExtraction | None,
    previous_source: str,
) -> list[dict[str, Any]]:
    if extraction.kind == "single":
        return [
            {
                "name": (extraction.single_name.strip() or company_name)[:20],
                "revenue": revenue,
                "operating_income": operating_income,
                "prior_revenue": prior_revenue,
                "comment": _comment(extraction.single_comment),
            }
        ]
    factor = _unit_factor(extraction.unit)
    if extraction.kind == "none" or factor is None:
        raise SegmentsUnavailable(NOT_FOUND_REASON)
    previous_factor = _unit_factor(previous.unit) if previous is not None else None
    earlier = {_key(d): d for d in previous.items} if previous and previous_factor else {}

    def three_months(draft: QuarterSegmentDraft, field: str) -> int | None:
        value = _amount(getattr(draft, field), source, factor)
        if value is not None:
            return value
        cumulative = _amount(getattr(draft, f"{field}_cumulative"), source, factor)
        if cumulative is None:
            return None
        if quarter == 1:
            return cumulative
        before = earlier.get(_key(draft))
        if before is None or previous_factor is None:
            return None
        before_cumulative = _amount(
            getattr(before, f"{field}_cumulative"), previous_source, previous_factor
        )
        return None if before_cumulative is None else cumulative - before_cumulative

    items: list[dict[str, Any]] = []
    names: set[str] = set()
    for draft in extraction.items:
        name = " ".join(draft.name.split())[:20]
        value = three_months(draft, "revenue")
        if not name or name in names or value is None or value <= 0:
            continue
        names.add(name)
        prior = three_months(draft, "prior_revenue")
        items.append(
            {
                "name": name,
                "revenue": value,
                "operating_income": three_months(draft, "operating_income"),
                "prior_revenue": prior if prior and prior > 0 else None,
                "comment": _comment(draft.comment),
            }
        )
    total = sum(i["revenue"] for i in items)
    if not items or not revenue:
        raise SegmentsUnavailable(NOT_FOUND_REASON)
    ratio = total / revenue
    if not SUM_RATIO_RANGE[0] <= ratio <= SUM_RATIO_RANGE[1]:
        logger.warning("사업별 분기 매출 합계가 분기 매출과 달라서 버려요 (비율 %.2f)", ratio)
        raise SegmentsUnavailable(NOT_FOUND_REASON)
    items = [i for i in items if i["revenue"] / total >= MIN_SHARE]
    return sorted(items, key=lambda i: i["revenue"], reverse=True)[:MAX_SEGMENTS]


def _rate(current: int, base: int | None) -> float | None:
    if base is None or base <= 0:
        return None
    return round((current - base) / base * 100, 1)


def to_segments(
    items: list[dict[str, Any]], scale: Callable[[int], float]
) -> list[SegmentPerformance]:
    """저장한 원 단위 값을 화면 단위(조 원·억 원)로."""
    return [
        SegmentPerformance(
            name=i["name"],
            revenue=scale(i["revenue"]),
            operating_income=(
                None if i.get("operating_income") is None else scale(i["operating_income"])
            ),
            yoy_rate=_rate(i["revenue"], i.get("prior_revenue")),
            comment=i.get("comment") or "",
        )
        for i in items
    ]


# --------------------------------------------------------------------------
# 화면에 붙이기 · 생성 상태
# --------------------------------------------------------------------------


def saved_items(session: Session, stock_code: str, rcept_no: str | None) -> list[dict[str, Any]]:
    """그 분기 보고서로 만들어 둔 사업별 실적 (없으면 빈 목록)."""
    record = session.get(CompanyQuarterSegmentsRecord, stock_code)
    if record is None or record.rcept_no != rcept_no or not isinstance(record.content, dict):
        return []
    return list(record.content.get("items") or [])


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def start_generation(
    session: Session,
    stock_code: str,
    rcept_no: str | None,
    quarter: int,
    now: datetime | None = None,
) -> bool:
    """만들어야 하면 "만드는 중"으로 표시하고 True. 4분기(사업보고서)는 3개월 값이 없어서 만들지 않는다."""
    if not rcept_no or quarter == 4:
        return False
    now = now or datetime.now(UTC)
    record = session.get(CompanyQuarterSegmentsRecord, stock_code)
    if record is not None and record.rcept_no == rcept_no:
        if record.status == "done" or record.failure_reason == NOT_FOUND_REASON:
            return False  # 같은 보고서로는 다시 해도 같다
        since = record.started_at if record.status == "generating" else record.updated_at
        wait = GENERATION_TIMEOUT if record.status == "generating" else RETRY_FAILED_AFTER
        if since is not None and now - _as_utc(since) < wait:
            return False
    record = record or CompanyQuarterSegmentsRecord(stock_code=stock_code, status="generating")
    record.status = "generating"
    record.rcept_no = rcept_no
    record.started_at = now
    record.failure_reason = None
    session.add(record)
    session.commit()
    return True


def _save(session_factory: Callable[[], Session], stock_code: str, **fields: Any) -> None:
    with session_factory() as session:
        record = session.get(CompanyQuarterSegmentsRecord, stock_code) or (
            CompanyQuarterSegmentsRecord(stock_code=stock_code, status=fields["status"])
        )
        for key, value in fields.items():
            setattr(record, key, value)
        record.started_at = None
        record.updated_at = datetime.now(UTC)
        session.add(record)
        session.commit()


def _read_source(rcept_no: str) -> str:
    report = dart.quarter_report(rcept_no)
    if report is None:
        raise RuntimeError(NO_SOURCE_REASON)  # 받지 못했다 → 곧 다시 해 본다
    note, business = report
    if not note and not business:
        raise SegmentsUnavailable(NOT_FOUND_REASON)
    # 영업부문 주석이 없는 회사도 있다 (사업이 하나뿐인 작은 회사 등). 사업의 내용으로 판단하게 한다
    return source_text(note or "(영업부문 주석이 없어요)", business)


def run_generation(
    stock_code: str,
    writer: QuarterSegmentWriter,
    session_factory: Callable[[], Session],
    *,
    company_name: str,
    rcept_no: str,
    revenue: int,
    operating_income: int,
    prior_revenue: int | None,
    quarter: int = 1,
    previous_rcept_no: str | None = None,
) -> str:
    """백그라운드 작업: 사업별 분기 실적을 만들어 저장한다. "done" | "failed".

    표에 3개월 열이 없거나 3개월 칸에 누적 값이 들어간 것 같으면 앞 분기 보고서(previous_rcept_no)도
    읽어서 누적끼리 뺀다 (AI 호출 2번).
    """
    try:
        source = _read_source(rcept_no)
        extraction = ai_retry.call(writer.write, source)
        if extraction is None:
            raise RuntimeError(FAILED_REASON)
        previous, previous_source = None, ""
        if previous_rcept_no and (
            needs_previous(extraction, quarter)
            or suspect_cumulative(extraction, source, revenue, quarter)
        ):
            previous_source = _read_source(previous_rcept_no)
            previous = ai_retry.call(writer.write, previous_source)
        items = to_items(
            extraction,
            source,
            revenue=revenue,
            operating_income=operating_income,
            prior_revenue=prior_revenue,
            company_name=company_name,
            quarter=quarter,
            previous=previous,
            previous_source=previous_source,
        )
    except SegmentsUnavailable as e:
        _save(
            session_factory,
            stock_code,
            status="failed",
            failure_reason=str(e),
            rcept_no=rcept_no,
            content=None,
        )
        return "failed"
    except Exception:  # noqa: BLE001 - 백그라운드 작업이라 실패해도 나머지 화면은 그대로 보인다
        logger.exception("Quarter segment generation crashed: %s", stock_code)
        _save(
            session_factory,
            stock_code,
            status="failed",
            failure_reason=FAILED_REASON,
            rcept_no=rcept_no,
        )
        return "failed"
    _save(
        session_factory,
        stock_code,
        status="done",
        content=json.loads(json.dumps({"kind": extraction.kind, "items": items})),
        rcept_no=rcept_no,
        failure_reason=None,
    )
    return "done"
