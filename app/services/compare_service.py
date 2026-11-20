"""비교하기: 두 회사의 숫자를 같은 기간·같은 기준으로 맞춘다. (프론트 문서 17장)

- 기간: 두 회사 재무제표에 모두 있는 가장 최근 사업연도. 한쪽만 새 사업보고서가 나왔으면 앞 연도로 맞춘다.
- 기준: 재무제표는 연결을 먼저 쓴다 (`dart_financials`). 종속회사가 없어 별도만 있는 회사와 비교하면
  회사별 `basis` 가 달라진다 (없는 연결 재무제표를 만들어 내지 않는다).
- 구할 수 없는 값은 None 으로 둔다.
- 한눈에 비교(`analysis`)는 AI가 쓴다. 처음 비교할 때 백그라운드에서 만들기 시작하고 다음 요청부터 채워진다.
  숫자는 서버가 넘긴 값만 쓰게 하고, 판단·추천·예측이 섞이거나 다른 숫자가 나오면 버린다.
"""

import json
import logging
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import anthropic
from google.genai import types as genai_types
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.errors import invalid_param
from app.models.compare import CompareAnalysisRecord
from app.schemas.company import CompanyDetail, CompanySummary
from app.schemas.compare import (
    CompanyCompare,
    CompareAnalysis,
    CompareCompany,
    ComparePoint,
    CompareSource,
)
from app.schemas.financials import Financials
from app.services import dart, data_store, gemini_keys

logger = logging.getLogger(__name__)

# Claude 호출 상한 (초). 기본값(약 10분)이면 멈춘 호출이 계속 붙잡혀 있어서 Gemini 쪽과 같게 둔다
CLAUDE_TIMEOUT_SECONDS = 45

FINANCE_INDUSTRY = "finance"
PEERS_DEFAULT = 4
PEERS_MAX = 4
RETRY_AFTER = timedelta(hours=1)  # 분석을 만들지 못했으면 이만큼 지나서 다시 해 본다
GENERATION_TIMEOUT = timedelta(minutes=3)
DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"


def is_financial(company: CompanyDetail) -> bool:
    """금융업인지. 금융업은 매출·영업이익·부채비율의 뜻이 달라서 다른 업종과 나란히 보기 어렵다."""
    if company.sector_group == "finance":
        return True
    return any(
        i.industry_id == FINANCE_INDUSTRY for i in data_store.industries_of(company.stock_code)
    )


def _percent(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator * 100, 1) if denominator else None


def _company(
    company: CompanyDetail, financials: Financials | None, year: int | None
) -> CompareCompany:
    by_year = {a.fiscal_year: a for a in financials.annual} if financials else {}
    this = by_year.get(year) if year is not None else None
    values: dict[str, float | None] = dict.fromkeys(
        ("revenue", "operating_income", "operating_margin", "revenue_growth", "debt_ratio")
    )
    if financials is not None and this is not None:
        last = by_year.get(year - 1)
        debt = financials.indicators.debt_ratio
        values = {
            "revenue": this.revenue,
            "operating_income": this.operating_income,
            "operating_margin": (
                _percent(this.operating_income, this.revenue) if this.revenue > 0 else None
            ),
            "revenue_growth": (
                _percent(this.revenue - last.revenue, abs(last.revenue))
                if last is not None and last.revenue
                else None
            ),
            # 부채비율은 가장 최근 사업보고서 값만 있다. 비교 연도가 그 해일 때만 쓴다.
            "debt_ratio": debt.value if debt is not None and year == max(by_year) else None,
        }
    return CompareCompany(
        stock_code=company.stock_code,
        name=company.name,
        sector=company.sector,
        market_cap=company.market_cap.value if company.market_cap else None,
        is_financial=is_financial(company),
        base_period=f"{year}년 연간" if this is not None else None,
        basis=financials.basis if financials else None,
        **values,
    )


def compare(left_code: str, right_code: str) -> tuple[CompanyCompare, int | None]:
    """(비교 응답, 두 회사 공통 연도). left = 비교할 기업, right = 지금 보는 기업."""
    if not left_code or not right_code or left_code == right_code:
        raise invalid_param("서로 다른 회사 두 곳을 골라 주세요.")
    codes = [left_code, right_code]
    companies = [data_store.get_company(code) for code in codes]  # 없는 회사면 404
    financials = [data_store.find_financials(code) for code in codes]

    year_sets = [{a.fiscal_year for a in f.annual} if f else set() for f in financials]
    common = year_sets[0] & year_sets[1]
    year = max(common) if common else None
    # 겹치는 연도가 없으면 나란히 놓을 수는 없지만, 각 회사의 가장 최근 값은 보여준다
    left, right = (
        _company(c, f, year or (max(years) if years else None))
        for c, f, years in zip(companies, financials, year_sets, strict=True)
    )

    notes: list[str] = []
    for item in (left, right):
        if item.base_period is None:
            notes.append(f"{item.name}의 재무제표를 아직 불러오지 못했어요.")
    if left.base_period and right.base_period and year is None:
        notes.append("두 회사의 재무제표에 겹치는 연도가 없어서 같은 해끼리 비교할 수 없어요.")
    if left.basis and right.basis and left.basis != right.basis:
        notes.append(
            "재무제표 기준이 달라요. 연결은 자회사까지 합친 숫자이고 별도는 그 회사만의 숫자라서 "
            "그대로 견주기 어려워요."
        )
    if left.is_financial != right.is_financial:
        finance = left if left.is_financial else right
        notes.append(
            f"{finance.name} 쪽은 금융업이에요. 금융업은 돈을 빌려주고 맡아 두는 일이 사업이라 "
            "매출·영업이익률·부채비율을 다른 업종과 똑같이 견주기 어려워요."
        )
    if year is not None:
        for item, f in zip((left, right), financials, strict=True):
            if item.debt_ratio is None and f and f.indicators.debt_ratio is not None:
                notes.append(
                    f"{item.name}의 부채비율은 {max(a.fiscal_year for a in f.annual)}년 값만 있어서 "
                    f"{year}년 비교에는 넣지 않았어요."
                )

    result = CompanyCompare(
        base_period=f"{year}년 연간" if year is not None else "",
        basis=(right.basis or left.basis or ""),
        left=left,
        right=right,
        notes=notes,
    )
    return result, year


# --------------------------------------------------------------------------
# 비슷한 회사
# --------------------------------------------------------------------------


def _cap(company: CompanyDetail) -> float | None:
    return company.market_cap.value if company.market_cap and company.market_cap.value > 0 else None


def peers(stock_code: str, limit: int = PEERS_DEFAULT) -> list[CompanySummary]:
    """같은 산업이고 시가총액이 비슷한 회사. 시가총액 차이가 작은 순, 자기 자신은 뺀다.

    속한 산업이 없으면 빈 목록. 후보는 시드 시가총액으로 넉넉히 추리고 (2,000곳 시세를 다 받지 않으려고),
    추린 회사만 지금 시가총액으로 다시 줄을 세운다.
    """
    company = data_store.get_company(stock_code)  # 없는 회사면 404
    seeds = data_store.seed_companies()
    base = _cap(seeds[stock_code]) or _cap(company)

    candidates: list[str] = []
    for record in data_store.industry_records():
        if stock_code in record["companies"]:
            candidates += [
                c
                for c in record["companies"]
                if c != stock_code and c in seeds and c not in candidates
            ]

    def distance(cap: float | None, origin: float | None) -> float:
        if cap is None or origin is None:
            return math.inf
        return abs(math.log(cap / origin))  # 2배 크나 절반이나 같은 거리

    shortlist = sorted(candidates, key=lambda c: (distance(_cap(seeds[c]), base), c))[: limit * 3]
    details = {c.stock_code: c for c in data_store.company_details_by_codes(shortlist)}
    live_base = _cap(company) or base
    chosen = sorted(details, key=lambda c: (distance(_cap(details[c]), live_base), c))[:limit]
    return [data_store.to_summary(details[c]) for c in chosen]


# --------------------------------------------------------------------------
# 한눈에 비교 (AI)
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """\
당신은 주식을 처음 시작하는 10대 초보자에게 두 회사의 차이를 설명하는 선생님이에요.
사용자 메시지로 두 회사의 소개와 같은 해·같은 기준으로 맞춘 숫자(JSON)가 주어져요. 그 자료만 근거로 써요.

반드시 지킬 규칙:
- 숫자는 주어진 값만 그대로 쓰세요. 새 숫자를 만들거나 계산하거나 어림하지 마세요. 숫자 없이 "더 커요", "더 빨라요"처럼 써도 돼요.
- 어느 회사가 "더 좋은 회사"인지 판단하지 마세요. "사세요", "파세요" 같은 추천이나
  앞으로 주가·실적이 오를지 내릴지에 대한 예측도 쓰지 마세요. 있는 사실의 차이만 써요.
- 모든 문장은 "~해요" 체로, 전문 용어는 쉬운 말로 풀어서 쓰세요.
- 값이 null 인 항목은 언급하지 마세요.

작성 가이드:
- headline: 두 회사가 어떻게 다른지 한 문장 (45자 안쪽). 예: "삼성전자는 사업이 여러 갈래로 넓고, SK하이닉스는 메모리 반도체에 집중해요"
- points: 2~3개. title 은 "규모", "수익성", "성장 속도" 중에서 자료가 있는 것만. description 은 쉬운 말로 1~2문장.
- reason: 왜 숫자가 다른지 두 회사의 사업 구성 차이로 1~2문장. 소개 자료로 알 수 없으면 null.
"""

_JUDGEMENT = re.compile(
    r"더 ?좋은 ?회사|더 ?나은 ?회사|매수|매도|사세요|파세요|추천|유망|저평가|고평가"
    r"|(오를|떨어질|내릴|상승할|하락할|성장할|늘어날|줄어들) ?(거예요|것으로|것 ?같|전망)"
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class AnalysisDraft(BaseModel):
    headline: str
    points: list[ComparePoint]
    reason: str | None = None


class CompareWriter(Protocol):
    def write(self, context: dict[str, Any]) -> AnalysisDraft | None: ...


class GeminiCompareWriter:
    _REQUEST_TIMEOUT_MS = 45_000

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: gemini_keys.FallbackClient | None = None

    def write(self, context: dict[str, Any]) -> AnalysisDraft | None:
        if self._client is None:
            self._client = gemini_keys.client(self._settings, self._REQUEST_TIMEOUT_MS)
        response = self._client.models.generate_content(
            model=self._settings.gemini_model,
            contents=json.dumps(context, ensure_ascii=False),
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=AnalysisDraft,
            ),
        )
        return response.parsed if isinstance(response.parsed, AnalysisDraft) else None


class ClaudeCompareWriter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.Anthropic | None = None

    def write(self, context: dict[str, Any]) -> AnalysisDraft | None:
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=self._settings.anthropic_api_key, timeout=CLAUDE_TIMEOUT_SECONDS
            )
        response = self._client.messages.parse(
            model=self._settings.anthropic_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
            output_format=AnalysisDraft,
        )
        return response.parsed_output


@lru_cache
def get_compare_writer() -> CompareWriter | None:
    """Anthropic > Gemini. 키가 없으면 None (분석을 만들지 않는다)."""
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeCompareWriter(settings)
    if settings.gemini_api_key:
        return GeminiCompareWriter(settings)
    return None


def _pair(result: CompanyCompare) -> str:
    return ":".join(sorted((result.left.stock_code, result.right.stock_code)))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _can_analyze(result: CompanyCompare, year: int | None) -> bool:
    """같은 해 숫자가 두 회사 모두 있을 때만 분석을 만든다."""
    return year is not None and result.left.revenue is not None and result.right.revenue is not None


def apply_analysis(session: Session, result: CompanyCompare, year: int | None) -> CompanyCompare:
    """만들어 둔 분석이 있으면 붙인다 (지금 비교 연도로 쓴 글만)."""
    if not _can_analyze(result, year):
        return result
    record = session.get(CompareAnalysisRecord, _pair(result))
    if record is None or record.fiscal_year != year or not record.content:
        return result
    return result.model_copy(update={"analysis": CompareAnalysis(**record.content)})


def start_analysis(session: Session, result: CompanyCompare, year: int | None) -> bool:
    """분석을 만들어야 하면 "만드는 중"으로 적고 True. (이미 있거나 만드는 중이면 False)"""
    if not _can_analyze(result, year):
        return False
    now = datetime.now(UTC)
    record = session.get(CompareAnalysisRecord, _pair(result))
    if record is not None and record.fiscal_year == year:
        age = now - _as_utc(record.started_at)
        if record.status == "done":
            return False
        if record.status == "generating" and age < GENERATION_TIMEOUT:
            return False
        if record.status == "failed" and age < RETRY_AFTER:
            return False
    record = record or CompareAnalysisRecord(
        pair=_pair(result), fiscal_year=year, status="generating", started_at=now
    )
    record.fiscal_year, record.status, record.started_at, record.content = (
        year,
        "generating",
        now,
        None,
    )
    session.add(record)
    session.commit()
    return True


def _numbers(company: CompareCompany) -> dict[str, Any]:
    return {
        "name": company.name,
        "sector": company.sector,
        "revenue": company.revenue,
        "operating_income": company.operating_income,
        "operating_margin_percent": company.operating_margin,
        "revenue_growth_percent": company.revenue_growth,
        "debt_ratio_percent": None if company.is_financial else company.debt_ratio,
        "unit": company.unit,
    }


def build_context(result: CompanyCompare, year: int) -> dict[str, Any]:
    context: dict[str, Any] = {"fiscal_year": year, "basis": result.basis, "companies": []}
    for item in (result.left, result.right):
        detail = data_store.get_company(item.stock_code)
        context["companies"].append(
            _numbers(item)
            | {
                "one_liner": detail.one_liner,
                "business_description": detail.business_description or None,
                "businesses": [s.name for s in detail.business_segments],
            }
        )
    return context


def _allowed_numbers(value: Any) -> set[float]:
    """AI에게 넘긴 자료에 들어 있는 숫자 전부. ("100원어치를 팔면"처럼 쓰는 100 은 늘 쓸 수 있다)"""
    found: set[float] = {100.0}
    if isinstance(value, bool):
        return found
    if isinstance(value, int | float):
        found.add(abs(float(value)))
    elif isinstance(value, str):
        found |= {float(n.replace(",", "")) for n in _NUMBER.findall(value)}
    elif isinstance(value, dict):
        for v in value.values():
            found |= _allowed_numbers(v)
    elif isinstance(value, list):
        for v in value:
            found |= _allowed_numbers(v)
    return found


def _is_clean(text: str, allowed: set[float]) -> bool:
    """판단·추천·예측이 없고, 넘긴 자료에 없는 숫자를 쓰지 않았는지."""
    if _JUDGEMENT.search(text):
        return False
    return all(float(n.replace(",", "")) in allowed for n in _NUMBER.findall(text))


def clean(draft: AnalysisDraft, context: dict[str, Any]) -> AnalysisDraft | None:
    """규칙을 어긴 문장을 뺀다. 한 줄 결론이 어겼거나 남는 포인트가 없으면 None (분석을 쓰지 않는다)."""
    allowed = _allowed_numbers(context)
    headline = draft.headline.strip()
    if not headline or not _is_clean(headline, allowed):
        return None
    points = [
        ComparePoint(title=p.title.strip(), description=p.description.strip())
        for p in draft.points
        if p.title.strip() and p.description.strip() and _is_clean(p.description, allowed)
    ][:3]
    if not points:
        return None
    reason = (draft.reason or "").strip() or None
    if reason and not _is_clean(reason, allowed):
        reason = None
    return AnalysisDraft(headline=headline, points=points, reason=reason)


def _sources(result: CompanyCompare) -> list[CompareSource]:
    """두 회사의 최근 사업보고서 (DART 원문)."""
    sources = []
    for item in (result.left, result.right):
        corp_code = data_store.get_company(item.stock_code).corp_code
        report = dart.latest_annual_report(corp_code) if corp_code else None
        if report is None or len(report.rcept_dt) != 8:
            continue
        filed = report.rcept_dt
        sources.append(
            CompareSource(
                title=f"{item.name} {report.report_nm.strip()}",
                url=DART_VIEWER.format(report.rcept_no),
                date=f"{filed[:4]}-{filed[4:6]}-{filed[6:]}",
            )
        )
    return sources


def run_analysis(
    left_code: str, right_code: str, writer: CompareWriter, session_factory: Callable[[], Session]
) -> None:
    """백그라운드 작업: 분석을 써서 저장한다. 쓰지 못했거나 규칙을 어겼으면 실패로 적는다."""
    result, year = compare(left_code, right_code)
    content: dict[str, Any] | None = None
    if year is not None:
        try:
            context = build_context(result, year)
            draft = writer.write(context)
            cleaned = clean(draft, context) if draft else None
            if cleaned is not None:
                content = CompareAnalysis(
                    **cleaned.model_dump(), sources=_sources(result)
                ).model_dump()
        except Exception as e:  # noqa: BLE001 - 한도·서버 오류·타임아웃 모두 나중에 다시 해 본다
            logger.warning("비교 분석을 만들지 못했어요 (%s, %s): %s", left_code, right_code, e)
    with session_factory() as session:
        record = session.get(CompareAnalysisRecord, _pair(result))
        if record is None:
            return
        record.status = "done" if content else "failed"
        record.content = content
        session.add(record)
        session.commit()
