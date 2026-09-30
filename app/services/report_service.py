"""AI 기업 분석 리포트 생성·조회.

숫자는 시드/공시 데이터에서 그대로 가져오고, AI(Claude)는 설명 문장만 작성한다.
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import anthropic
from sqlmodel import Session

from app.core.config import Settings
from app.core.errors import report_generation_failed, report_not_found, report_not_ready
from app.models.report import StoredReport
from app.schemas.company import CompanyDetail
from app.schemas.financials import Financials
from app.schemas.report import CompanyReport, ReportContent
from app.services.data_store import get_company, get_financials, get_news

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

DISCLAIMERS = [
    "이 리포트는 기업을 이해하도록 돕는 교육용 정보이며 투자 추천이 아니에요.",
    "숫자는 공식 공시 자료를 그대로 사용하고, AI는 설명만 작성해요.",
]
DATA_SOURCES_BASE = ["금융감독원 DART", "한국거래소"]

SYSTEM_PROMPT = """\
당신은 주식을 처음 시작하는 10대 초보자에게 기업을 설명하는 선생님이에요.
사용자 메시지로 한 회사의 확인된 데이터(JSON)가 주어지면, 그 데이터만 근거로 기업 분석 리포트를 작성해요.

반드시 지킬 규칙:
- 숫자는 주어진 데이터에 있는 값(또는 derived 항목에 미리 계산된 값)만 그대로 쓰세요. 새 숫자를 만들거나 추정하지 마세요.
- "사세요", "파세요", "지금이 기회" 같은 매수·매도 권유나 주가 전망 표현은 쓰지 마세요.
- 모든 문장은 "~해요" 체로, 전문 용어는 쉬운 말로 풀어서 쓰세요.
- 데이터가 부족한 부분은 지어내지 말고 다루지 마세요.

작성 가이드:
- summary: 회사의 최근 흐름을 2~3문장으로 요약해요.
- key_points: 3~5개. category는 performance(실적)·profitability(수익성)·stability(재무 안정성)·business(사업)·news(뉴스) 중 하나,
  status는 good·neutral·caution 중 하나예요. source에는 근거 자료와 기준(예: "2025년 연간 · 연결 기준"),
  base_date에는 근거 자료 날짜(YYYY-MM-DD)를 적어요. id는 "kp-<category>" 형식이에요.
- charts: 설명에 도움이 되는 차트 1~3개. type은 annual_revenue·annual_operating_income·operating_margin·segment_revenue 중 하나이고,
  segment_revenue는 사업부문 매출 데이터가 있을 때만 써요. id는 "chart-1"처럼 번호를 붙여요.
- connection: 회사의 계획·뉴스·실제 숫자가 어떻게 이어지는지 chain(짧은 문구 3개 안팎)과 conclusion으로 보여줘요.
- strengths / watch_points: 각각 1~3개, 숫자 근거가 있는 문장으로 써요.
- quiz: 리포트 내용으로 풀 수 있는 3지선다 문제 2~3개. answer_index는 0부터 시작하는 정답 번호예요. id는 "q1"처럼 번호를 붙여요.
"""


class ReportWriter(Protocol):
    async def write(self, context: dict[str, Any]) -> ReportContent: ...


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


def build_context(company: CompanyDetail, financials: Financials) -> dict[str, Any]:
    news = [n.model_dump() for n in get_news(company.stock_code) if not n.is_placeholder]
    return {
        "company": company.model_dump(
            include={
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
    }


def _analyzed_sources(context: dict[str, Any]) -> list[str]:
    sources = ["재무제표"]
    if context["news"]:
        sources.append("뉴스")
    if context["company"]["business_segments"] or context["company"]["timeline"]:
        sources.append("사업보고서")
    return sources


def _sanitize(content: ReportContent, financials: Financials) -> ReportContent:
    """모델 출력 중 화면에서 깨질 수 있는 항목을 걸러낸다."""
    charts = [
        c for c in content.charts if c.type != "segment_revenue" or financials.segments is not None
    ]
    quiz = [q for q in content.quiz if 0 <= q.answer_index < len(q.options)]
    return content.model_copy(update={"charts": charts, "quiz": quiz})


def _ensure_supported(stock_code: str) -> CompanyDetail:
    company = get_company(stock_code)
    if not company.has_report:
        raise report_not_ready()
    return company


def get_stored_report(session: Session, stock_code: str) -> CompanyReport:
    _ensure_supported(stock_code)
    stored = session.get(StoredReport, stock_code)
    if stored is None:
        raise report_not_found()
    return CompanyReport(**{**stored.payload, "is_cached": True})


async def generate_report(session: Session, stock_code: str, writer: ReportWriter) -> CompanyReport:
    company = _ensure_supported(stock_code)
    financials = get_financials(stock_code)
    context = build_context(company, financials)

    started = time.perf_counter()
    content = _sanitize(await writer.write(context), financials)
    elapsed = round(time.perf_counter() - started, 1)

    generated_at = datetime.now(KST).replace(microsecond=0)
    report = CompanyReport(
        **content.model_dump(),
        stock_code=stock_code,
        generated_at=generated_at,
        generation_seconds=elapsed,
        is_cached=False,
        analyzed_sources=_analyzed_sources(context),
        disclaimers=DISCLAIMERS,
        data_sources=DATA_SOURCES_BASE + ([financials.source] if financials.source else []),
    )

    stored = session.get(StoredReport, stock_code)
    payload = report.model_dump(mode="json")
    if stored is None:
        session.add(StoredReport(stock_code=stock_code, generated_at=generated_at, payload=payload))
    else:
        stored.generated_at = generated_at
        stored.payload = payload
        session.add(stored)
    session.commit()
    return report
