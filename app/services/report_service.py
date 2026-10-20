"""AI 기업 분석 리포트 생성·조회.

숫자(근거 자료)는 시드/공시 데이터에서 서버가 그대로 조립하고,
AI(Claude 또는 Gemini)는 문장(headline, title, reason, meaning 등)만 작성한다.

상태 흐름: none → generating → done / failed
- POST는 생성을 시작하고 바로 generating을 돌려준다. 실제 생성은 백그라운드에서 한다.
- GET은 마지막으로 성공한 내용 + 지금 상태를 돌려준다 (다시 만드는 중·실패여도 이전 내용 유지).
"""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import anthropic
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from sqlmodel import Session, col, select

from app.core.config import Settings
from app.core.errors import AppError, report_generation_failed
from app.models.report import ReportRecord
from app.schemas.company import CompanyDetail
from app.schemas.financials import Financials
from app.schemas.news import NewsItem
from app.schemas.report import (
    KEY_POINT_ORDER,
    CompanyReport,
    Evidence,
    EvidenceKind,
    EvidenceRef,
    FinancialHighlight,
    FinancialsEvidence,
    KeyPoint,
    NewsEvidence,
    ReportContent,
    ReportDataAsOf,
    RevenueMixItem,
    VisionEvidence,
)
from app.services.data_store import find_financials, get_company, get_news
from app.services.market_clock import KST, last_close_date

logger = logging.getLogger(__name__)

SOURCES_BASE = ["금융감독원 DART", "한국거래소"]

# 서버가 재시작되는 등으로 생성이 끝나지 못한 리포트를 실패로 돌리는 기준
GENERATION_TIMEOUT = timedelta(minutes=5)

NO_FINANCIALS_REASON = "이 기업의 재무제표를 아직 불러오지 못했어요."
TIMEOUT_REASON = "리포트를 만드는 데 너무 오래 걸려서 멈췄어요. 다시 시도해 주세요."

SYSTEM_PROMPT = """\
당신은 주식을 처음 시작하는 10대 초보자에게 기업을 설명하는 선생님이에요.
사용자 메시지로 한 회사의 확인된 데이터(JSON)가 주어지면, 그 데이터만 근거로 기업 분석 리포트를 작성해요.

반드시 지킬 규칙:
- 숫자는 주어진 데이터에 있는 값(또는 derived 항목에 미리 계산된 값)만 그대로 쓰세요. 새 숫자를 만들거나 추정하지 마세요.
- "사세요", "파세요", "지금이 기회" 같은 매수·매도 권유나 주가 전망 표현은 쓰지 마세요.
- 모든 문장은 "~해요" 체로, 전문 용어는 쉬운 말로 풀어서 쓰세요.
- 데이터가 부족한 부분은 지어내지 마세요.
- timeline에서 is_plan이 true인 항목은 아직 이뤄지지 않은 계획이에요. "~할 계획이라고 밝혔어요"처럼 사실과 구분해서 쓰세요.

작성 가이드:
- headline: 이 회사의 최근 흐름을 한 문장으로 정리해요.
- key_points: 정확히 5개, category마다 하나씩 이 순서로 써요.
  performance(실적 특징) → revenue_source(주요 수익원) → recent_change(최근 실적 변화) → risk(위험·변화 요인) → strength(핵심 경쟁력)
  - title: 한 줄 결론이에요.
  - before / after: 비교할 과거 값과 현재 값이에요. label(무엇), value(단위를 붙인 값, 예: "43.6조 원"), period(예: "2025")를 적어요.
    value는 데이터에 있는 숫자만 써요. 비교할 숫자가 없으면 null로 두고, 하나만 있어도 돼요.
  - reason: 왜 그런지 1~2문장으로 써요.
  - meaning: 초보자에게 무슨 뜻인지 1문장으로 풀어요.
  - evidence: 이 포인트의 근거 자료 종류예요. available_evidence에 있는 것(financials, news, vision) 중에서만 골라요.
    financials는 재무제표, news는 뉴스, vision은 회사 소개·사업부문·계획(timeline)이에요.
  - 근거가 부족한 category라도 빼지 말고, 있는 데이터 안에서 확인된 사실만으로 짧게 써요.
- vision_summary: 회사가 무엇을 하는지와 밝힌 계획을 1~2문장으로 써요.
- quiz: 리포트 내용으로 풀 수 있는 3지선다 문제 2~3개. answer_index는 0부터 시작하는 정답 번호예요.
"""


class ReportWriter(Protocol):
    async def write(self, context: dict[str, Any]) -> ReportContent: ...


class FixtureReportWriter:
    """API 키 없이 데모할 때 쓰는 목데이터 작성자.

    클라이언트 미팅 등에서 실제 AI 호출 없이 화면만 보여줘야 할 때 쓴다.
    API 키가 설정되면 `get_report_writer()`가 자동으로 실제 작성자로 바꾸므로,
    이 클래스를 쓰기 위해 별도로 되돌릴 코드는 없다.
    목데이터가 없는 회사는 다른 회사 내용을 보여주지 않고 실패로 처리한다.
    """

    _FIXTURE_DIR = Path(__file__).resolve().parent.parent / "data"
    _FIXTURES = {"005930": "report_samsung.json"}

    async def write(self, context: dict[str, Any]) -> ReportContent:
        name = self._FIXTURES.get(context["company"]["stock_code"])
        if name is None:
            raise report_generation_failed(
                "AI 연결 없이 보여줄 수 있는 예시 리포트가 없는 회사예요."
            )
        data = json.loads((self._FIXTURE_DIR / name).read_text(encoding="utf-8"))
        return ReportContent(**data)


class GeminiReportWriter:
    """Gemini가 과부하(503)일 때를 대비해 재시도하고, 그래도 안 되면 목데이터로 대체한다.

    클라이언트 미팅 등 데모 중에는 "리포트를 만들지 못했어요" 화면이 뜨는 것 자체가 치명적이라,
    실제 생성이 끝내 실패해도 화면은 항상 뜨도록 안전망을 둔다.
    """

    _MAX_ATTEMPTS = 3
    _RETRY_SECONDS = 2.0
    # 응답이 없을 때 무한정 기다리지 않도록 요청 하나당 상한을 둔다 (단위: ms).
    _REQUEST_TIMEOUT_MS = 20_000

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: genai.Client | None = None
        self._fallback = FixtureReportWriter()

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(
                api_key=self._settings.gemini_api_key,
                http_options=genai_types.HttpOptions(timeout=self._REQUEST_TIMEOUT_MS),
            )
        return self._client

    async def _call(self, context: dict[str, Any]) -> ReportContent:
        response = await self._get_client().aio.models.generate_content(
            model=self._settings.gemini_model,
            contents=json.dumps(context, ensure_ascii=False),
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=ReportContent,
            ),
        )
        if response.parsed is None:
            logger.error("Unexpected Gemini response: no parsed output")
            raise report_generation_failed()
        return response.parsed

    async def write(self, context: dict[str, Any]) -> ReportContent:
        rate_limited = False
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                return await self._call(context)
            except genai_errors.ClientError as e:
                if (
                    getattr(e, "status", None) == "RESOURCE_EXHAUSTED"
                    or getattr(e, "code", None) == 429
                ):
                    logger.warning("Gemini rate limited (attempt %d): %s", attempt, e)
                    rate_limited = True
                else:
                    logger.exception("Gemini client error, falling back to fixture report")
                    return await self._fallback.write(context)
            except genai_errors.ServerError as e:
                logger.warning("Gemini server error (attempt %d): %s", attempt, e)
            except (
                Exception
            ) as e:  # noqa: BLE001 - 타임아웃·연결 끊김 등 네트워크 레벨 장애도 재시도 대상
                logger.warning("Gemini request failed (attempt %d): %s", attempt, e)
            if attempt < self._MAX_ATTEMPTS:
                await asyncio.sleep(self._RETRY_SECONDS)

        if rate_limited:
            raise report_generation_failed(
                "지금은 리포트 요청이 많아요. 잠시 후 다시 시도해 주세요."
            )
        logger.error(
            "Gemini unavailable after %d attempts, falling back to fixture report",
            self._MAX_ATTEMPTS,
        )
        return await self._fallback.write(context)


class ClaudeReportWriter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            key = self._settings.anthropic_api_key
            self._client = (
                anthropic.AsyncAnthropic(api_key=key) if key else anthropic.AsyncAnthropic()
            )
        return self._client

    async def write(self, context: dict[str, Any]) -> ReportContent:
        try:
            response = await self._get_client().messages.parse(
                model=self._settings.anthropic_model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                output_format=ReportContent,
                thinking={"type": "adaptive"},
                output_config={"effort": self._settings.anthropic_effort},
                # 안전 분류기가 요청을 거절하면 서버에서 권장 모델로 다시 시도한다.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
        except anthropic.RateLimitError as e:
            logger.warning("Claude rate limited: %s", e)
            raise report_generation_failed(
                "지금은 리포트 요청이 많아요. 잠시 후 다시 시도해 주세요."
            ) from e
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            logger.exception("Claude API request failed")
            raise report_generation_failed() from e
        except (anthropic.AnthropicError, TypeError) as e:
            # SDK는 인증 정보가 없으면 TypeError를 던진다
            logger.exception("Claude client error (API 키 설정 확인)")
            raise report_generation_failed() from e

        if response.stop_reason != "end_turn" or response.parsed_output is None:
            logger.error("Unexpected report response: stop_reason=%s", response.stop_reason)
            raise report_generation_failed()
        return response.parsed_output


# --------------------------------------------------------------------------
# 근거 자료 조립 (숫자는 모두 서버가 데이터에서 그대로 가져온다)
# --------------------------------------------------------------------------


def _fmt(value: float) -> str:
    """333.60 → "333.6", 44.0 → "44" """
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _derived_metrics(financials: Financials) -> dict[str, Any]:
    annual = sorted(financials.annual, key=lambda a: a.fiscal_year)
    margins = {
        str(a.fiscal_year): round(a.operating_income / a.revenue * 100, 1)
        for a in annual
        if a.revenue
    }
    growth = {
        str(cur.fiscal_year): round((cur.revenue - prev.revenue) / prev.revenue * 100, 1)
        for prev, cur in zip(annual, annual[1:], strict=False)
        if prev.revenue
    }
    return {"operating_margin_percent": margins, "revenue_growth_percent": growth}


def _financials_evidence(financials: Financials) -> FinancialsEvidence | None:
    annual = sorted(financials.annual, key=lambda a: a.fiscal_year)
    if not annual:
        return None
    latest = annual[-1]
    source = financials.source or ""
    highlights = [
        FinancialHighlight(
            label="매출", value=f"{_fmt(latest.revenue)}{financials.unit}", source=source
        ),
        FinancialHighlight(
            label="영업이익",
            value=f"{_fmt(latest.operating_income)}{financials.unit}",
            source=source,
        ),
    ]
    if latest.revenue:
        margin = round(latest.operating_income / latest.revenue * 100, 1)
        highlights.append(
            FinancialHighlight(
                label="영업이익률", value=f"{_fmt(margin)}%", source="매출과 영업이익으로 계산"
            )
        )
    debt_ratio = financials.indicators.debt_ratio
    if debt_ratio is not None:
        highlights.append(
            FinancialHighlight(
                label="부채비율",
                value=f"{_fmt(debt_ratio.value)}%",
                source="예시 값 (확인 예정)" if debt_ratio.is_sample else source,
                is_sample=debt_ratio.is_sample,
            )
        )
    return FinancialsEvidence(
        unit=financials.unit,
        years=[str(a.fiscal_year) for a in annual],
        revenue=[a.revenue for a in annual],
        operating_income=[a.operating_income for a in annual],
        highlights=highlights,
        base_date=f"{latest.fiscal_year}년 연간 · {financials.basis}",
    )


def _news_evidence(news: list[NewsItem]) -> list[NewsEvidence]:
    """원문 링크가 있는 실제 뉴스만 보낸다."""
    return [
        NewsEvidence(
            title=n.title,
            press=n.source or "",
            published_at=n.published_at or "",
            summary=n.summary,
            url=n.url,
        )
        for n in news
        if not n.is_placeholder and n.url
    ]


def _has_vision(company: CompanyDetail, financials: Financials) -> bool:
    return bool(company.business_description or company.timeline or financials.segments)


def _vision_evidence(
    company: CompanyDetail, financials: Financials, summary: str
) -> VisionEvidence | None:
    if not _has_vision(company, financials):
        return None
    segments = financials.segments
    revenue_mix = (
        [
            RevenueMixItem(
                name=item.name,
                amount=item.revenue,
                unit=financials.unit,
                period=segments.base_label,
            )
            for item in segments.items
        ]
        if segments
        else []
    )
    return VisionEvidence(
        summary=summary or company.business_description,
        revenue_mix=revenue_mix,
        source=company.timeline_source or financials.source or "",
    )


def _financials_basis(financials: Financials) -> str:
    """재무 자료 기준. 예: "2025년 사업보고서" (없으면 "2025년 연간")"""
    latest = max(financials.annual, key=lambda a: a.fiscal_year, default=None)
    if financials.indicators_as_of:
        return financials.indicators_as_of
    return f"{latest.fiscal_year}년 연간" if latest else financials.basis


def _data_as_of(
    company: CompanyDetail, financials: Financials, news: list[NewsEvidence]
) -> ReportDataAsOf:
    return ReportDataAsOf(
        financials=_financials_basis(financials),
        price=company.price_as_of or last_close_date().isoformat(),
        news=(news[0].published_at or None) if news else None,  # 뉴스는 최신 순
    )


def _evidence_refs(data_as_of: ReportDataAsOf) -> dict[EvidenceKind, EvidenceRef]:
    """근거 칩. 이름에는 출처를, 날짜에는 자료 날짜를 넣는다."""
    return {
        "financials": EvidenceRef(type="financials", label=f"재무제표 {data_as_of.financials}"),
        "news": EvidenceRef(type="news", label="뉴스", date=data_as_of.news),
        "vision": EvidenceRef(type="vision", label="기업 비전"),
    }


def build_context(company: CompanyDetail, financials: Financials) -> dict[str, Any]:
    news = [n.model_dump() for n in get_news(company.stock_code) if not n.is_placeholder]
    available = ["financials"]
    if news:
        available.append("news")
    if _has_vision(company, financials):
        available.append("vision")
    return {
        "company": company.model_dump(
            include={
                "stock_code",
                "name",
                "sector",
                "business_description",
                "business_segments",
                "timeline",
                "timeline_source",
            }
        ),
        "financials": financials.model_dump(exclude={"indicators"}),
        "derived": _derived_metrics(financials),
        "news": news,
        "available_evidence": available,
    }


def _sanitize(content: ReportContent, available: list[str]) -> ReportContent:
    """모델 출력 중 화면에서 깨질 수 있는 항목을 걸러낸다."""
    by_category = {}
    for point in content.key_points:
        by_category.setdefault(point.category, point)  # 같은 분류가 여러 개면 첫 번째만
    key_points = [
        point.model_copy(
            update={"evidence": [e for e in dict.fromkeys(point.evidence) if e in available]}
        )
        for category in KEY_POINT_ORDER
        if (point := by_category.get(category)) is not None
    ]
    quiz = [q for q in content.quiz if 0 <= q.answer_index < len(q.options)]
    return content.model_copy(update={"key_points": key_points, "quiz": quiz})


async def write_report(stock_code: str, writer: ReportWriter) -> tuple[CompanyReport, datetime]:
    """AI로 리포트 내용을 만들고 근거 자료를 붙인다. 실패하면 AppError."""
    company = get_company(stock_code)
    financials = find_financials(stock_code)
    if financials is None:
        raise report_generation_failed(NO_FINANCIALS_REASON)
    context = build_context(company, financials)
    content = _sanitize(await writer.write(context), context["available_evidence"])

    news = _news_evidence(get_news(stock_code))
    data_as_of = _data_as_of(company, financials, news)
    refs = _evidence_refs(data_as_of)
    generated_at = datetime.now(KST).replace(microsecond=0)
    report = CompanyReport(
        stock_code=stock_code,
        status="done",
        generated_at=generated_at,
        headline=content.headline,
        key_points=[
            KeyPoint(**p.model_dump(exclude={"evidence"}), evidence=[refs[k] for k in p.evidence])
            for p in content.key_points
        ],
        evidence=Evidence(
            financials=_financials_evidence(financials),
            news=news,
            vision=_vision_evidence(company, financials, content.vision_summary),
        ),
        quiz=content.quiz,
        sources=SOURCES_BASE + ([financials.source] if financials.source else []),
        data_as_of=data_as_of,
    )
    return report, generated_at


# --------------------------------------------------------------------------
# 상태 저장·조회
# --------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    # SQLite는 시간대 정보를 버리므로, 시간대가 없으면 UTC로 저장한 값으로 본다.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _expire_if_stuck(session: Session, record: ReportRecord) -> None:
    if record.status != "generating" or record.started_at is None:
        return
    if datetime.now(UTC) - _as_utc(record.started_at) > GENERATION_TIMEOUT:
        record.status = "failed"
        record.failure_reason = TIMEOUT_REASON
        record.started_at = None
        session.add(record)
        session.commit()


def _to_response(stock_code: str, record: ReportRecord | None) -> CompanyReport:
    if record is None:
        return CompanyReport(stock_code=stock_code, status="none")
    base = (
        CompanyReport(**record.content)
        if record.content
        else CompanyReport(stock_code=stock_code, status="none")
    )
    return base.model_copy(
        update={
            "status": record.status,
            "failure_reason": record.failure_reason if record.status == "failed" else None,
        }
    )


def get_report(session: Session, stock_code: str) -> CompanyReport:
    get_company(stock_code)
    record = session.get(ReportRecord, stock_code)
    if record is not None:
        _expire_if_stuck(session, record)
    return _to_response(stock_code, record)


def start_generation(session: Session, stock_code: str) -> tuple[CompanyReport, bool]:
    """생성을 시작한다. (응답, 백그라운드 작업을 띄워야 하는지)를 돌려준다."""
    get_company(stock_code)
    record = session.get(ReportRecord, stock_code)
    if record is not None:
        _expire_if_stuck(session, record)
        if record.status == "generating":
            return _to_response(stock_code, record), False  # 이미 만드는 중
    else:
        record = ReportRecord(stock_code=stock_code, status="generating")

    if find_financials(stock_code) is None:
        # AI를 부를 근거가 없으면 기다리게 하지 않고 바로 실패로 알려준다.
        record.status = "failed"
        record.failure_reason = NO_FINANCIALS_REASON
        record.started_at = None
        should_run = False
    else:
        record.status = "generating"
        record.failure_reason = None
        record.started_at = datetime.now(UTC)
        should_run = True
    session.add(record)
    session.commit()
    return _to_response(stock_code, record), should_run


async def run_generation(
    stock_code: str, writer: ReportWriter, session_factory: Callable[[], Session]
) -> None:
    """백그라운드 작업: 리포트를 만들어 저장하고, 실패하면 이유를 남긴다."""
    try:
        report, generated_at = await write_report(stock_code, writer)
    except AppError as e:
        _save_failure(stock_code, e.detail, session_factory)
        return
    except Exception:
        logger.exception("Report generation crashed: %s", stock_code)
        _save_failure(stock_code, report_generation_failed().detail, session_factory)
        return

    with session_factory() as session:
        record = session.get(ReportRecord, stock_code) or ReportRecord(
            stock_code=stock_code, status="done"
        )
        record.status = "done"
        record.content = report.model_dump(mode="json")
        record.generated_at = generated_at
        record.started_at = None
        record.failure_reason = None
        session.add(record)
        session.commit()


def _save_failure(stock_code: str, reason: str, session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        record = session.get(ReportRecord, stock_code) or ReportRecord(
            stock_code=stock_code, status="failed"
        )
        record.status = "failed"
        record.failure_reason = reason
        record.started_at = None
        session.add(record)
        session.commit()


def list_generated(session: Session, limit: int) -> list[CompanyReport]:
    """내용이 있는 리포트를 최신 순으로 (홈 화면 "AI가 분석한 기업"용)."""
    records = session.exec(
        select(ReportRecord)
        .where(col(ReportRecord.generated_at).is_not(None))
        .order_by(col(ReportRecord.generated_at).desc())
        .limit(limit)
    ).all()
    return [CompanyReport(**r.content) for r in records if r.content]


def latest_headlines(session: Session, stock_codes: list[str]) -> dict[str, str]:
    """회사별 마지막으로 성공한 리포트의 한 줄 결론 (홈 거래대금 목록용). 리포트가 없으면 빠진다."""
    records = session.exec(
        select(ReportRecord).where(
            col(ReportRecord.stock_code).in_(stock_codes),
            col(ReportRecord.generated_at).is_not(None),
        )
    ).all()
    return {
        r.stock_code: r.content["headline"]
        for r in records
        if r.content and r.content.get("headline")
    }
