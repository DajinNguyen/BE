"""시드 데이터 저장소.

OpenDART / pykrx 연동 전까지는 `app/data/*.json` 에 넣어둔 값을 읽어서 쓴다.
확인 전 숫자는 `is_sample: true` 로 표시돼 있다.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.errors import company_not_found, data_not_found
from app.schemas.company import CompanyDetail, CompanySummary
from app.schemas.financials import Financials
from app.schemas.news import NewsItem
from app.schemas.term import Term

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache
def _companies() -> dict[str, CompanyDetail]:
    return {c["stock_code"]: CompanyDetail(**c) for c in load_json("companies.json")}


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def to_summary(company: CompanyDetail) -> CompanySummary:
    return CompanySummary(**company.model_dump(include=set(CompanySummary.model_fields)))


def search_companies(query: str, limit: int) -> list[CompanySummary]:
    needle = _normalize(query)
    if not needle:
        return []
    matches = []
    for c in _companies().values():
        fields = [_normalize(f) for f in (c.name, c.name_en, c.stock_code)]
        if any(needle in f for f in fields):
            matches.append((needle not in fields, c))  # 정확히 일치하는 회사를 먼저
    matches.sort(key=lambda m: m[0])
    return [to_summary(c) for _, c in matches[:limit]]


def companies_by_codes(stock_codes: list[str]) -> list[CompanySummary]:
    """요청한 순서대로 돌려주고, 없는 종목코드는 건너뛴다."""
    companies = _companies()
    return [to_summary(companies[code]) for code in stock_codes if code in companies]


def get_company(stock_code: str) -> CompanyDetail:
    company = _companies().get(stock_code)
    if company is None:
        raise company_not_found()
    return company


def find_financials(stock_code: str) -> Financials | None:
    """재무 자료가 아직 없는 회사는 None (숫자를 지어내지 않는다)."""
    get_company(stock_code)
    data = load_json("financials.json").get(stock_code)
    return None if data is None else Financials(stock_code=stock_code, **data)


def get_financials(stock_code: str) -> Financials:
    financials = find_financials(stock_code)
    if financials is None:
        raise data_not_found()
    return financials


def get_news(stock_code: str) -> list[NewsItem]:
    get_company(stock_code)
    return [NewsItem(**n) for n in load_json("news.json").get(stock_code, [])]


@lru_cache
def get_terms() -> list[Term]:
    return [Term(**t) for t in load_json("terms.json")]
