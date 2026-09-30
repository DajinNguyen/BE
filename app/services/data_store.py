"""Part 1 데모용 시드 데이터 저장소.

OpenDART / pykrx 연동 전까지는 `app/data/*.json` 에 확인된 값만 넣어두고 읽어서 쓴다.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.errors import company_not_found
from app.schemas.company import CompanyDetail, CompanySummary
from app.schemas.financials import Financials, Indicators
from app.schemas.news import NewsItem
from app.schemas.term import Term

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache
def _companies() -> dict[str, CompanyDetail]:
    return {c["stock_code"]: CompanyDetail(**c) for c in _load("companies.json")}


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def _to_summary(company: CompanyDetail) -> CompanySummary:
    return CompanySummary(**company.model_dump(include=set(CompanySummary.model_fields)))


def search_companies(query: str) -> list[CompanySummary]:
    needle = _normalize(query)
    if not needle:
        return []
    return [
        _to_summary(c)
        for c in _companies().values()
        if any(needle in _normalize(field) for field in (c.name, c.name_en, c.stock_code))
    ]


def popular_companies() -> list[CompanySummary]:
    companies = _companies()
    return [_to_summary(companies[code]) for code in _load("popular.json") if code in companies]


def get_company(stock_code: str) -> CompanyDetail:
    company = _companies().get(stock_code)
    if company is None:
        raise company_not_found()
    return company


def get_financials(stock_code: str) -> Financials:
    get_company(stock_code)
    data = _load("financials.json").get(stock_code)
    if data is None:
        # 확인된 재무 데이터가 아직 없는 회사: 숫자를 지어내지 않고 빈 값으로 내려준다.
        return Financials(
            stock_code=stock_code,
            unit="조 원",
            basis="연결 기준",
            source=None,
            annual=[],
            latest_quarter=None,
            segments=None,
            indicators=Indicators(),
        )
    return Financials(stock_code=stock_code, **data)


def get_news(stock_code: str) -> list[NewsItem]:
    get_company(stock_code)
    return [NewsItem(**n) for n in _load("news.json").get(stock_code, [])]


@lru_cache
def get_terms() -> list[Term]:
    return [Term(**t) for t in _load("terms.json")]
