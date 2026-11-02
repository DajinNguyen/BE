"""AI 기업 개요: DART 사업보고서 "사업의 내용"을 쉬운 말로 정리한다.

시드(`app/data/companies.json`)에 사업부문·회사의 방향이 없는 회사만 만든다.
- 상세 화면을 처음 열면 백그라운드에서 만들기 시작하고, 다음에 열 때부터 채워진다.
- 새 사업보고서가 공시되면(접수번호가 바뀌면) 다시 만든다. 새로 만드는 동안이나 실패해도
  예전 개요를 그대로 보여준다.
- `python -m app.scripts.generate_overviews` 로 미리 한꺼번에 만들 수도 있다.
- 숫자는 주어진 자료(사업보고서 글, DART 연도별 재무)에 있는 값만 쓰게 하고,
  회사의 방향(timeline) 연도는 서버가 확인한 연도만 남긴다.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import anthropic
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import Settings
from app.models.overview import CompanyOverviewRecord
from app.schemas.company import BusinessSegment, CompanyDetail, TimelineItem
from app.services import dart, data_store

logger = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 40_000  # 사업의 내용이 이보다 길면 앞부분만 (사업 개요·주요 제품이 앞에 있다)
MAX_SEGMENTS = 5
MAX_TIMELINE = 5
GENERATION_TIMEOUT = timedelta(minutes=5)  # 서버 재시작 등으로 멈춘 생성은 다시 시작한다
RETRY_FAILED_AFTER = timedelta(hours=1)  # 실패하면 이 시간 동안은 다시 시도하지 않는다

SYSTEM_PROMPT = """\
당신은 주식을 처음 시작하는 10대 초보자에게 회사를 소개하는 선생님이에요.
사용자 메시지로 한 회사의 사업보고서 "사업의 내용" 글과 연도별 매출·영업이익(JSON)이 주어지면,
그 자료만 근거로 회사 소개를 써요.

반드시 지킬 규칙:
- 숫자는 주어진 자료에 있는 값만 그대로 쓰세요. 새 숫자를 만들거나 추정하지 마세요.
- "사세요", "파세요", "오를 거예요" 같은 매수·매도 권유나 주가 전망 표현은 쓰지 마세요.
- 모든 문장은 "~해요" 체로, 전문 용어는 쉬운 말로 풀어서 쓰세요.
- 자료에 없는 내용은 지어내지 마세요.

작성 가이드:
- business_description: 이 회사가 무엇을 만들거나 팔아서 돈을 버는지 2~3문장으로 써요.
  첫 문장은 "OOO는 ~하는 회사예요."처럼 시작해요.
- business_segments: 주요 사업부문이나 주요 제품군 2~5개예요.
  - code: 영문 대문자 짧은 이름 (예: "DRAM", "NAND", "AUTO"). 서로 겹치지 않게 써요.
  - name: 화면에 보일 이름 (예: "DRAM(메모리)").
  - description: 무엇을 만들고 어디에 쓰이는지 1~2문장.
- timeline: 회사의 최근 흐름 3~5개, 오래된 해부터 써요. year 는 "2025"처럼 숫자 네 자리만 써요.
  - 지난 일(is_plan: false)은 annual_financials 에 있는 연도만 써요. 그 해 매출·영업이익 변화와
    사업의 내용에 나온 일을 엮어서 써요. title 은 한 줄 결론(예: "이익이 크게 늘었어요"),
    description 은 1~2문장이에요.
  - 사업의 내용에 앞으로의 계획이 적혀 있으면 마지막에 1개만 is_plan: true 로 써요.
    year 는 계획 연도(모르면 가장 최근 연도 다음 해), 문장은 "~할 계획이라고 밝혔어요"처럼 사실과 구분해요.
"""

NO_SOURCE_REASON = "이 회사의 사업보고서를 아직 불러오지 못했어요."
FAILED_REASON = "회사 소개를 만드는 중에 문제가 생겼어요."
QUOTA_REASON = "AI 사용 한도를 다 써서 잠시 뒤에 다시 만들어요."


class OverviewContent(BaseModel):
    """AI가 쓰는 부분."""

    business_description: str
    business_segments: list[BusinessSegment]
    timeline: list[TimelineItem]


class OverviewUnavailable(Exception):
    """자료가 없거나 AI가 답하지 못해서 개요를 만들 수 없다. 메시지는 실패 이유로 저장한다."""


class OverviewQuotaExceeded(OverviewUnavailable):
    """AI 사용 한도(429)에 걸렸다. 다른 회사도 같은 결과라서 일괄 생성은 여기서 멈춘다."""

    def __init__(self) -> None:
        super().__init__(QUOTA_REASON)


class OverviewWriter(Protocol):
    async def write(self, context: dict[str, Any]) -> OverviewContent: ...


class GeminiOverviewWriter:
    _MAX_ATTEMPTS = 2
    _RETRY_SECONDS = 2.0
    _REQUEST_TIMEOUT_MS = 90_000

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(
                api_key=self._settings.gemini_api_key,
                http_options=genai_types.HttpOptions(timeout=self._REQUEST_TIMEOUT_MS),
            )
        return self._client

    async def write(self, context: dict[str, Any]) -> OverviewContent:
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                response = await self._get_client().aio.models.generate_content(
                    model=self._settings.gemini_model,
                    contents=json.dumps(context, ensure_ascii=False),
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=OverviewContent,
                    ),
                )
                if isinstance(response.parsed, OverviewContent):
                    return response.parsed
                logger.error("Unexpected Gemini overview response: no parsed output")
            except genai_errors.ClientError as e:
                if getattr(e, "code", None) == 429:
                    logger.warning("Gemini overview quota exceeded: %s", e.message)
                    raise OverviewQuotaExceeded() from e
                logger.warning("Gemini overview client error: %s", e)
                break  # 키·요청 문제는 다시 해도 같다
            except Exception as e:  # noqa: BLE001 - 서버 오류·타임아웃은 한 번 더
                logger.warning("Gemini overview failed (attempt %d): %s", attempt, e)
            if attempt < self._MAX_ATTEMPTS:
                await asyncio.sleep(self._RETRY_SECONDS)
        raise OverviewUnavailable(FAILED_REASON)


class ClaudeOverviewWriter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.AsyncAnthropic | None = None

    async def write(self, context: dict[str, Any]) -> OverviewContent:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        try:
            response = await self._client.messages.parse(
                model=self._settings.anthropic_model,
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                output_format=OverviewContent,
            )
        except anthropic.RateLimitError as e:
            raise OverviewQuotaExceeded() from e
        except (anthropic.AnthropicError, TypeError) as e:
            logger.warning("Claude overview failed: %s", e)
            raise OverviewUnavailable(FAILED_REASON) from e
        if response.parsed_output is None:
            raise OverviewUnavailable(FAILED_REASON)
        return response.parsed_output


# --------------------------------------------------------------------------
# 자료 모으기 · 결과 다듬기
# --------------------------------------------------------------------------


def _source_label(company_name: str, report: dart.AnnualReport) -> str:
    """예: "SK하이닉스 2025년 사업보고서 (2026년 3월 공시)"."""
    year = re.search(r"(\d{4})", report.report_nm)
    filed = report.rcept_dt
    label = f"{company_name} {year.group(1)}년 사업보고서" if year else f"{company_name} 사업보고서"
    if len(filed) == 8:
        label += f" ({filed[:4]}년 {int(filed[4:6])}월 공시)"
    return label


def load_source(stock_code: str) -> tuple[dict[str, Any], str, str]:
    """(AI에게 줄 자료, 출처 이름, 사업보고서 접수번호). 자료가 없으면 OverviewUnavailable."""
    company = data_store.get_company(stock_code)
    if not company.corp_code:
        raise OverviewUnavailable(NO_SOURCE_REASON)
    report = dart.latest_annual_report(company.corp_code)
    text = dart.business_section(report.rcept_no) if report else None
    if report is None or not text:
        raise OverviewUnavailable(NO_SOURCE_REASON)
    financials = data_store.find_financials(stock_code)
    annual = (
        [
            {
                "fiscal_year": a.fiscal_year,
                "revenue": a.revenue,
                "operating_income": a.operating_income,
            }
            for a in sorted(financials.annual, key=lambda a: a.fiscal_year)
        ]
        if financials
        else []
    )
    label = _source_label(company.name, report)
    context = {
        "company": {"name": company.name, "sector": company.sector},
        "annual_financials": {
            "unit": financials.unit if financials else None,
            "basis": financials.basis if financials else None,
            "years": annual,
        },
        "business_report": {"title": label, "text": text[:MAX_SOURCE_CHARS]},
    }
    return context, label, report.rcept_no


def clean(content: OverviewContent, context: dict[str, Any]) -> OverviewContent:
    """AI 결과를 화면 규칙에 맞게 다듬는다: 개수 제한, 겹치는 code, 확인 안 된 연도 빼기."""
    segments: list[BusinessSegment] = []
    codes: set[str] = set()
    for i, s in enumerate(content.business_segments):
        if not (s.name.strip() and s.description.strip()):
            continue
        code = re.sub(r"[^A-Z0-9_]", "", s.code.upper()) or f"SEG{i + 1}"
        while code in codes:
            code += "_"
        codes.add(code)
        segments.append(s.model_copy(update={"code": code}))

    timeline_items = []
    for t in content.timeline:  # "2025년" 처럼 와도 "2025" 로
        year = re.search(r"\d{4}", t.year)
        if year:
            timeline_items.append(t.model_copy(update={"year": year.group()}))

    known_years = {str(y["fiscal_year"]) for y in context["annual_financials"]["years"]}
    latest = max((int(y) for y in known_years), default=date.today().year - 1)
    past = [
        t for t in timeline_items if not t.is_plan and (t.year in known_years or not known_years)
    ]
    plans = [t for t in timeline_items if t.is_plan and int(t.year) > latest]
    timeline = sorted(past, key=lambda t: t.year)[-(MAX_TIMELINE - 1) :] + plans[:1]
    return OverviewContent(
        business_description=content.business_description.strip(),
        business_segments=segments[:MAX_SEGMENTS],
        timeline=timeline,
    )


# --------------------------------------------------------------------------
# 화면에 붙이기 · 생성 상태
# --------------------------------------------------------------------------


def needs_overview(company: CompanyDetail) -> bool:
    """시드에 사업부문·회사의 방향이 없는 회사."""
    return not company.business_segments and not company.timeline


def apply_overview(session: Session, company: CompanyDetail) -> CompanyDetail:
    """만들어 둔 AI 개요가 있으면 상세 응답에 채운다 (시드 값이 있으면 시드가 먼저)."""
    if not needs_overview(company):
        return company
    record = session.get(CompanyOverviewRecord, company.stock_code)
    if record is None or not record.content:  # 다시 만드는 중·실패해도 예전 개요는 보여준다
        return company
    content = OverviewContent(**record.content)
    update: dict[str, Any] = {
        "business_segments": content.business_segments,
        "timeline": content.timeline,
        "timeline_source": record.source or "",
    }
    if not company.business_description:
        update["business_description"] = content.business_description
    return company.model_copy(update=update)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def is_outdated(record: CompanyOverviewRecord, corp_code: str) -> bool:
    """개요를 만든 뒤에 새 사업보고서가 공시됐는지 (DART 공시 목록은 하루 캐시)."""
    report = dart.latest_annual_report(corp_code)
    return report is not None and bool(record.rcept_no) and report.rcept_no != record.rcept_no


def start_generation(session: Session, company: CompanyDetail, now: datetime | None = None) -> bool:
    """만들어야 하면 "만드는 중"으로 표시하고 True.
    최신 보고서로 이미 있거나·만드는 중이거나·방금 실패했으면 False."""
    if not needs_overview(company) or not company.corp_code:
        return False
    now = now or datetime.now(UTC)
    record = session.get(CompanyOverviewRecord, company.stock_code)
    if record is not None:
        if record.status == "done":
            if not is_outdated(record, company.corp_code):
                return False
        else:
            since = record.started_at if record.status == "generating" else record.updated_at
            wait = GENERATION_TIMEOUT if record.status == "generating" else RETRY_FAILED_AFTER
            if since is not None and now - _as_utc(since) < wait:
                return False
    else:
        record = CompanyOverviewRecord(stock_code=company.stock_code, status="generating")
    record.status = "generating"
    record.started_at = now
    record.failure_reason = None
    session.add(record)
    session.commit()
    return True


def _save(session_factory: Callable[[], Session], stock_code: str, **fields: Any) -> None:
    with session_factory() as session:
        record = session.get(CompanyOverviewRecord, stock_code) or CompanyOverviewRecord(
            stock_code=stock_code, status=fields["status"]
        )
        for key, value in fields.items():
            setattr(record, key, value)
        record.started_at = None
        record.updated_at = datetime.now(UTC)
        session.add(record)
        session.commit()


async def run_generation(
    stock_code: str, writer: OverviewWriter, session_factory: Callable[[], Session]
) -> str:
    """백그라운드 작업: 개요를 만들어 저장한다. "done" | "failed" | "quota"(AI 사용 한도)."""
    try:
        context, label, rcept_no = await asyncio.to_thread(load_source, stock_code)
        content = clean(await writer.write(context), context)
    except OverviewQuotaExceeded as e:
        _save(session_factory, stock_code, status="failed", failure_reason=str(e))
        return "quota"
    except OverviewUnavailable as e:
        _save(session_factory, stock_code, status="failed", failure_reason=str(e))
        return "failed"
    except Exception:
        logger.exception("Overview generation crashed: %s", stock_code)
        _save(session_factory, stock_code, status="failed", failure_reason=FAILED_REASON)
        return "failed"
    _save(
        session_factory,
        stock_code,
        status="done",
        content=content.model_dump(mode="json"),
        source=label,
        rcept_no=rcept_no,
        failure_reason=None,
    )
    return "done"
