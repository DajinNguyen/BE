"""시드 데이터 저장소.

회사 목록·재무·뉴스는 `app/data/*.json` 에 넣어둔 값을 읽어서 쓴다.
확인 전 숫자는 `is_sample: true` 로 표시돼 있다.
DART 키가 있으면 회사마다 DART 고유번호·공식 회사명을 붙이고, 상세 화면에서는 기업개황으로 빈 값을 채운다.
네이버 시세를 받으면 현재가·등락률·시가총액을 실제 값(`is_sample: false`)으로 바꾼다.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.errors import company_not_found, data_not_found, puzzle_not_found
from app.schemas.common import SampleValue
from app.schemas.company import CompanyDetail, CompanySummary
from app.schemas.crossword import CrosswordPuzzle, CrosswordSummary
from app.schemas.financials import Financials
from app.schemas.news import NewsItem
from app.schemas.term import Term
from app.services import dart, naver
from app.services.market_clock import last_close_date

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache
def _seed_companies() -> dict[str, CompanyDetail]:
    return {c["stock_code"]: CompanyDetail(**c) for c in load_json("companies.json")}


def _with_live_data(
    company: CompanyDetail, corp: dart.DartCorp | None, quote: naver.Quote | None
) -> CompanyDetail:
    update: dict[str, Any] = {}
    if corp:
        update["corp_code"] = corp.corp_code
    if quote:
        update |= {
            "current_price": SampleValue(value=quote.price, is_sample=False),
            "change_rate": SampleValue(value=quote.change_rate, is_sample=False),
            "market_cap": SampleValue(value=round(quote.market_cap / 1e12, 1), is_sample=False),
            "price_as_of": quote.traded_at.date().isoformat(),
        }
    return company.model_copy(update=update) if update else company


def _companies() -> dict[str, CompanyDetail]:
    """시드 회사 + DART 고유번호 + 네이버 시세 (받지 못한 값은 시드 그대로)."""
    seed = _seed_companies()
    index = dart.corp_index()
    quotes = naver.quotes(list(seed))
    return {
        code: _with_live_data(company, index.get(code), quotes.get(code))
        for code, company in seed.items()
    }


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def to_summary(company: CompanyDetail) -> CompanySummary:
    return CompanySummary(**company.model_dump(include=set(CompanySummary.model_fields)))


def _search_fields(company: CompanyDetail, corp: dart.DartCorp | None) -> list[str]:
    fields = [company.name, company.name_en, company.stock_code]
    if corp:
        # 공식 회사명(현대자동차, 케이티 등)과 공식 영문명으로도 찾을 수 있게
        fields += [corp.corp_name, corp.corp_eng_name]
    return [_normalize(f) for f in fields if f]


def search_companies(query: str, limit: int) -> list[CompanySummary]:
    needle = _normalize(query)
    if not needle:
        return []
    index = dart.corp_index()
    matches = []
    for c in _companies().values():
        fields = _search_fields(c, index.get(c.stock_code))
        if any(needle in f for f in fields):
            matches.append((needle not in fields, c))  # 정확히 일치하는 회사를 먼저
    matches.sort(key=lambda m: m[0])
    return [to_summary(c) for _, c in matches[:limit]]


def all_companies() -> list[CompanySummary]:
    """시드 목록 순서 그대로 (대략 시가총액 순)."""
    return [to_summary(c) for c in _companies().values()]


def companies_by_codes(stock_codes: list[str]) -> list[CompanySummary]:
    """요청한 순서대로 돌려주고, 없는 종목코드는 건너뛴다."""
    companies = _companies()
    return [to_summary(companies[code]) for code in stock_codes if code in companies]


def get_company(stock_code: str) -> CompanyDetail:
    company = _companies().get(stock_code)
    if company is None:
        raise company_not_found()
    return company


def get_company_detail(stock_code: str) -> CompanyDetail:
    """상세 화면용. 시세 기준일을 붙이고, 시드에 없는 홈페이지는 DART 기업개황으로 채운다."""
    company = get_company(stock_code)
    if company.price_as_of is None:  # 시세를 못 받았으면 예시 값의 기준일은 직전 장 마감일
        company = company.model_copy(update={"price_as_of": last_close_date().isoformat()})
    if company.homepage_url or not company.corp_code:
        return company
    overview = dart.company_overview(company.corp_code)
    homepage = dart.homepage_url(overview) if overview else ""
    return company.model_copy(update={"homepage_url": homepage}) if homepage else company


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


@lru_cache
def _crosswords() -> dict[str, CrosswordPuzzle]:
    return {p["id"]: CrosswordPuzzle(**p) for p in load_json("crosswords.json")}


def list_crosswords() -> list[CrosswordSummary]:
    return [
        CrosswordSummary(
            id=p.id, title=p.title, difficulty=p.difficulty, size=p.size, word_count=len(p.words)
        )
        for p in _crosswords().values()
    ]


def get_crossword(puzzle_id: str) -> CrosswordPuzzle:
    puzzle = _crosswords().get(puzzle_id)
    if puzzle is None:
        raise puzzle_not_found()
    return puzzle
