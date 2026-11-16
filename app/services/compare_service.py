"""비교하기: 두 회사의 숫자를 같은 기간·같은 기준으로 맞춘다.

- 기간: 두 회사 재무제표에 모두 있는 가장 최근 사업연도. 한쪽만 새 사업보고서가 나왔으면 앞 연도로 맞춘다.
- 기준: 재무제표는 연결을 먼저 쓴다 (`dart_financials`). 종속회사가 없어 별도만 있는 회사와 비교하면
  기준이 다르다고 알려준다 (없는 연결 재무제표를 만들어 내지 않는다).
- 구할 수 없는 값은 None 으로 두고 이유를 `notes` 에 쉬운 말로 적는다.
"""

import math

from app.core.errors import invalid_param
from app.schemas.company import CompanyDetail
from app.schemas.compare import (
    CompareCompany,
    CompareMetrics,
    CompareYear,
    Comparison,
    SimilarCompany,
)
from app.schemas.financials import Financials
from app.schemas.industry import IndustryRef
from app.services import data_store

FINANCE_INDUSTRY = "finance"
SIMILAR_DEFAULT = 4
SIMILAR_MAX = 4


def is_financial(company: CompanyDetail) -> bool:
    """금융업인지. 금융업은 매출·영업이익·부채비율의 뜻이 달라서 다른 업종과 나란히 보기 어렵다."""
    if company.sector_group == "finance":
        return True
    return any(
        i.industry_id == FINANCE_INDUSTRY for i in data_store.industries_of(company.stock_code)
    )


def _percent(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator * 100, 1) if denominator else None


def _margin(revenue: float, operating_income: float) -> float | None:
    return _percent(operating_income, revenue) if revenue > 0 else None


def _metrics(financials: Financials | None, year: int | None) -> CompareMetrics:
    empty = CompareMetrics(
        revenue=None, operating_income=None, operating_margin=None, revenue_growth=None,
        debt_ratio=None,
    )  # fmt: skip
    if financials is None or year is None:
        return empty
    by_year = {a.fiscal_year: a for a in financials.annual}
    this = by_year.get(year)
    if this is None:
        return empty
    last = by_year.get(year - 1)
    growth = (
        _percent(this.revenue - last.revenue, abs(last.revenue))
        if last is not None and last.revenue
        else None
    )
    # 부채비율은 가장 최근 사업보고서 값만 있다. 비교 연도가 그 해일 때만 쓴다.
    latest_year = max(by_year)
    debt = financials.indicators.debt_ratio
    return CompareMetrics(
        revenue=this.revenue,
        operating_income=this.operating_income,
        operating_margin=_margin(this.revenue, this.operating_income),
        revenue_growth=growth,
        debt_ratio=debt.value if debt is not None and year == latest_year else None,
    )


def _company(
    company: CompanyDetail, financials: Financials | None, year: int | None, years: list[int]
) -> CompareCompany:
    by_year = {a.fiscal_year: a for a in financials.annual} if financials else {}
    return CompareCompany(
        stock_code=company.stock_code,
        name=company.name,
        sector=company.sector,
        market=company.market,
        market_cap=company.market_cap,
        is_financial=is_financial(company),
        has_financials=financials is not None,
        basis=financials.basis if financials else None,
        source=financials.source if financials else None,
        fiscal_year=year if financials and year in by_year else None,
        metrics=_metrics(financials, year),
        annual=[
            CompareYear(
                fiscal_year=y,
                revenue=by_year[y].revenue,
                operating_income=by_year[y].operating_income,
                operating_margin=_margin(by_year[y].revenue, by_year[y].operating_income),
            )
            for y in years
        ],
    )


def _money(value: float) -> str:
    """조 원 숫자를 읽기 쉬운 말로 (1조 원이 안 되면 억 원)."""
    if abs(value) >= 1:
        return f"{value:,.1f}조 원"
    return f"{round(value * 10_000):,}억 원"


def _times(big: float, small: float) -> str:
    ratio = big / small
    return f"{ratio:.1f}배" if ratio < 10 else f"{round(ratio):,}배"


def _differences(a: CompareCompany, b: CompareCompany, year: int) -> list[str]:
    """숫자로 본 차이를 쉬운 말로. 어느 회사가 더 좋다는 판단이나 투자 의견은 쓰지 않는다."""
    lines: list[str] = []
    ma, mb = a.metrics, b.metrics

    if ma.revenue is not None and mb.revenue is not None:
        line = (
            f"{year}년 매출은 {a.name} {_money(ma.revenue)}, {b.name} {_money(mb.revenue)}이에요."
        )
        if ma.revenue > 0 and mb.revenue > 0 and ma.revenue != mb.revenue:
            big, small = (a, b) if ma.revenue > mb.revenue else (b, a)
            line += f" {big.name} 쪽이 {_times(big.metrics.revenue, small.metrics.revenue)} 커요."
        lines.append(line)
    if ma.operating_income is not None and mb.operating_income is not None:
        lines.append(
            f"영업이익은 {a.name} {_money(ma.operating_income)}, {b.name} {_money(mb.operating_income)}"
            "이에요. (영업이익: 물건을 팔아 번 돈에서 만들고 파는 데 든 돈을 뺀 것)"
        )
    if ma.operating_margin is not None and mb.operating_margin is not None:
        lines.append(
            f"100원어치를 팔았을 때 남긴 돈은 {a.name} {ma.operating_margin:g}원, "
            f"{b.name} {mb.operating_margin:g}원이에요. (영업이익률)"
        )
    if ma.revenue_growth is not None and mb.revenue_growth is not None:

        def change(value: float) -> str:
            return f"{value:g}% 늘었" if value >= 0 else f"{abs(value):g}% 줄었"

        lines.append(
            f"한 해 전과 견주면 매출이 {a.name} 쪽은 {change(ma.revenue_growth)}고, "
            f"{b.name} 쪽은 {change(mb.revenue_growth)}어요."
        )
    if ma.debt_ratio is not None and mb.debt_ratio is not None:
        lines.append(
            f"부채비율은 {a.name} {ma.debt_ratio:g}%, {b.name} {mb.debt_ratio:g}%예요. "
            "(부채비율: 내 돈에 견줘 빌린 돈이 얼마나 되는지)"
        )
    return lines


def compare(stock_codes: list[str]) -> Comparison:
    if len(stock_codes) != 2 or stock_codes[0] == stock_codes[1]:
        raise invalid_param("서로 다른 회사 두 곳을 골라 주세요.")
    companies = [data_store.get_company(code) for code in stock_codes]  # 없는 회사면 404
    financials = [data_store.find_financials(code) for code in stock_codes]

    year_sets = [{a.fiscal_year for a in f.annual} if f else set() for f in financials]
    common = sorted(year_sets[0] & year_sets[1])
    year = common[-1] if common else None

    # 겹치는 연도가 없으면 나란히 놓을 수는 없지만, 각 회사의 가장 최근 값은 보여준다
    own = [sorted(years) for years in year_sets]
    a, b = (
        _company(c, f, year or (mine[-1] if mine else None), common or mine)
        for c, f, mine in zip(companies, financials, own, strict=True)
    )
    notes: list[str] = []
    for item in (a, b):
        if not item.has_financials:
            notes.append(f"{item.name}의 재무제표를 아직 불러오지 못했어요.")
    if a.has_financials and b.has_financials and year is None:
        notes.append("두 회사의 재무제표에 겹치는 연도가 없어서 나란히 비교할 수 없어요.")

    basis = a.basis if a.basis and a.basis == b.basis else None
    if a.basis and b.basis and a.basis != b.basis:
        notes.append(
            f"재무제표 기준이 달라요. {a.name} {a.basis}, {b.name} {b.basis}이에요. "
            "연결은 자회사까지 합친 숫자이고 별도는 그 회사만의 숫자라서 그대로 견주기 어려워요."
        )
    if a.is_financial != b.is_financial:
        finance = a if a.is_financial else b
        notes.append(
            f"{finance.name} 쪽은 금융업이에요. 금융업은 돈을 빌려주고 맡아 두는 일이 사업이라 "
            "매출·영업이익률·부채비율을 다른 업종과 똑같이 견주기 어려워요."
        )
    elif a.is_financial and b.is_financial:
        notes.append(
            "두 회사 모두 금융업이에요. 금융업은 고객이 맡긴 돈이 빚으로 잡혀서 부채비율이 원래 높아요."
        )
    if year is not None:  # 겹치는 연도가 있을 때만
        for item, f in zip((a, b), financials, strict=True):
            if item.metrics.debt_ratio is None and f and f.indicators.debt_ratio is not None:
                notes.append(
                    f"{item.name}의 부채비율은 {max(x.fiscal_year for x in f.annual)}년 값만 있어서 "
                    f"{year}년 비교에는 넣지 않았어요."
                )

    comparable = year is not None and basis is not None and a.is_financial == b.is_financial
    return Comparison(
        fiscal_year=year,
        basis=basis,
        comparable=comparable,
        notes=notes,
        differences=_differences(a, b, year) if year is not None else [],
        companies=[a, b],
    )


def _seed_cap(company: CompanyDetail) -> float | None:
    return company.market_cap.value if company.market_cap and company.market_cap.value > 0 else None


def similar_companies(stock_code: str, limit: int = SIMILAR_DEFAULT) -> list[SimilarCompany]:
    """같은 산업이고 시가총액이 비슷한 회사. 시가총액 차이가 작은 순으로 고른다.

    회사가 여러 산업에 속하면 앞 산업부터 본다. 속한 산업이 없으면 빈 목록.
    후보를 고를 때는 시드 시가총액을 쓰고 (2,000곳 시세를 다 받지 않으려고), 고른 회사만 지금 시세를 붙인다.
    """
    company = data_store.get_company(stock_code)  # 없는 회사면 404
    seeds = data_store.seed_companies()
    base = _seed_cap(seeds[stock_code]) or _seed_cap(company)

    picked: dict[str, IndustryRef] = {}
    for record in data_store.industry_records():
        if stock_code not in record["companies"]:
            continue
        ref = IndustryRef(industry_id=record["industry_id"], name=record["name"])
        for code in record["companies"]:
            if code != stock_code and code in seeds and code not in picked:
                picked[code] = ref

    def distance(code: str) -> float:
        cap = _seed_cap(seeds[code])
        if base is None or cap is None:
            return math.inf
        return abs(math.log(cap / base))  # 2배 크나 절반이나 같은 거리

    # 시드 시가총액으로 넉넉히 추린 뒤, 지금 시가총액으로 다시 줄을 세운다
    shortlist = sorted(picked, key=lambda code: (distance(code), code))[: limit * 3]
    details = {c.stock_code: c for c in data_store.company_details_by_codes(shortlist)}
    live_base = _seed_cap(company) or base

    def live_distance(code: str) -> float:
        cap = _seed_cap(details[code])
        if live_base is None or cap is None:
            return math.inf
        return abs(math.log(cap / live_base))

    codes = sorted(details, key=lambda code: (live_distance(code), code))[:limit]
    return [
        SimilarCompany(**details[code].model_dump(exclude={"industry"}), industry=picked[code])
        for code in codes
    ]
