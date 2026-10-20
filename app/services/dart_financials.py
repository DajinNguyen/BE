"""DART 재무제표(단일회사 주요계정) → Financials.

- 연간 실적: 가장 최근 사업보고서 하나로 3년치(당기·전기·전전기)를 받는다. 아직 공시 전이면 그 전 해 것.
- 최근 분기: 사업보고서보다 새로운 분기·반기 보고서 중 가장 최근 것의 3개월 실적.
- 연결재무제표를 먼저 쓰고, 없으면 별도재무제표를 쓴다.
- ROE·부채비율은 순이익·부채·자본으로, PER·PBR은 시가총액으로 계산한다.

DART에 자료가 없거나 받지 못하면 None을 돌려주고, 호출하는 쪽은 시드 데이터로 동작한다.
"""

from datetime import date, datetime

from app.schemas.common import SampleValue
from app.schemas.financials import AnnualResult, Financials, Indicators, LatestQuarter
from app.services import dart
from app.services.market_clock import KST

BUSINESS_REPORT = "11011"
# 최근 것부터 찾는다. 값: (보고서 이름, 분기)
QUARTERLY_REPORTS = {
    "11014": ("3분기보고서", 3),
    "11012": ("반기보고서", 2),
    "11013": ("1분기보고서", 1),
}

# 금융회사는 매출액 대신 영업수익을 쓴다
REVENUE_NAMES = ("매출액", "수익(매출액)", "영업수익")
OPERATING_INCOME_NAMES = ("영업이익", "영업이익(손실)")
NET_INCOME_NAMES = ("당기순이익", "당기순이익(손실)")
LIABILITIES_NAMES = ("부채총계",)
EQUITY_NAMES = ("자본총계",)

TRILLION = 1_000_000_000_000

# (금액 칸, 몇 년 전)
PERIOD_COLUMNS = (("bfefrmtrm_amount", 2), ("frmtrm_amount", 1), ("thstrm_amount", 0))


def _today() -> date:
    return datetime.now(KST).date()


def _amount(text: str | None) -> int | None:
    """ "1,234" → 1234, "" → None"""
    text = (text or "").replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


class _Statement:
    """보고서 하나의 계정 행들. 연결이 있으면 연결만, 없으면 별도만 본다."""

    def __init__(self, rows: list[dict]):
        consolidated = [r for r in rows if r.get("fs_div") == "CFS"]
        self.consolidated = bool(consolidated)
        chosen = consolidated or [r for r in rows if r.get("fs_div") == "OFS"]
        self._rows: dict[str, dict] = {}
        for row in chosen:
            self._rows.setdefault("".join((row.get("account_nm") or "").split()), row)

    def row(self, names: tuple[str, ...]) -> dict | None:
        return next((self._rows[n] for n in names if n in self._rows), None)

    def value(self, names: tuple[str, ...], column: str = "thstrm_amount") -> int | None:
        row = self.row(names)
        return _amount(row.get(column)) if row else None


def _latest_business_report(corp_code: str, today: date) -> tuple[int, _Statement] | None:
    for year in (today.year - 1, today.year - 2):
        rows = dart.single_accounts(corp_code, year, BUSINESS_REPORT)
        if rows is None:  # DART 오류
            return None
        statement = _Statement(rows)
        if statement.row(REVENUE_NAMES) and statement.row(OPERATING_INCOME_NAMES):
            return year, statement
    return None


def _latest_quarter(
    corp_code: str, annual_year: int, today: date
) -> tuple[int, str, _Statement] | None:
    for year in range(today.year, annual_year, -1):
        for reprt_code in QUARTERLY_REPORTS:
            rows = dart.single_accounts(corp_code, year, reprt_code)
            if not rows:
                continue
            statement = _Statement(rows)
            if statement.value(REVENUE_NAMES) is not None:
                return year, reprt_code, statement
    return None


def _ratio(numerator: float | None, denominator: float | None, digits: int = 1) -> float | None:
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator * 100, digits)


def _indicators(statement: _Statement, market_cap: SampleValue | None) -> Indicators:
    net_income = statement.value(NET_INCOME_NAMES)
    liabilities = statement.value(LIABILITIES_NAMES)
    equity = statement.value(EQUITY_NAMES)

    def real(value: float | None) -> SampleValue | None:
        return None if value is None else SampleValue(value=value, is_sample=False)

    def per_market_cap(denominator: int | None) -> SampleValue | None:
        if market_cap is None or not denominator:
            return None
        value = round(market_cap.value * TRILLION / denominator, 1)
        return SampleValue(value=value, is_sample=market_cap.is_sample)

    return Indicators(
        roe=real(_ratio(net_income, equity)),
        debt_ratio=real(_ratio(liabilities, equity)),
        per=per_market_cap(net_income),
        pbr=per_market_cap(equity),
    )


def fetch_financials(
    stock_code: str, corp_code: str, market_cap: SampleValue | None
) -> Financials | None:
    today = _today()
    found = _latest_business_report(corp_code, today)
    if found is None:
        return None
    year, statement = found

    annual_won = []
    for column, years_ago in PERIOD_COLUMNS:
        revenue = statement.value(REVENUE_NAMES, column)
        operating_income = statement.value(OPERATING_INCOME_NAMES, column)
        if revenue is not None and operating_income is not None:
            annual_won.append((year - years_ago, revenue, operating_income))
    if not annual_won:
        return None

    def scale(won: int) -> float:
        # 프론트가 금액에 항상 "조 원"을 붙여 보여줘서 작은 회사도 조 단위로 보낸다
        return round(won / TRILLION, 2)

    source = f"DART {year}년 사업보고서"
    latest_quarter = None
    quarter = _latest_quarter(corp_code, year, today)
    if quarter:
        q_year, reprt_code, q_statement = quarter
        report_name, q = QUARTERLY_REPORTS[reprt_code]
        q_revenue = q_statement.value(REVENUE_NAMES)
        q_operating = q_statement.value(OPERATING_INCOME_NAMES)
        if q_revenue is not None and q_operating is not None:
            latest_quarter = LatestQuarter(
                label=f"{q_year}년 {q}분기",
                revenue=scale(q_revenue),
                operating_income=scale(q_operating),
            )
            source += f" · {q_year}년 {report_name}"

    return Financials(
        stock_code=stock_code,
        unit="조 원",
        basis="연결 기준" if statement.consolidated else "별도 기준",
        source=source,
        annual=[
            AnnualResult(fiscal_year=y, revenue=scale(r), operating_income=scale(o))
            for y, r, o in annual_won
        ],
        latest_quarter=latest_quarter,
        segments=None,  # DART API에는 사업부문별 매출이 없다
        indicators_as_of=f"{year}년 사업보고서",
        indicators=_indicators(statement, market_cap),
    )
