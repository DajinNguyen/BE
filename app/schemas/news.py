from pydantic import BaseModel


class FinancialLink(BaseModel):
    label: str
    value: str


class NewsItem(BaseModel):
    """시드 뉴스 (`app/data/news.json`) 한 건. 응답으로는 `NewsArticle` 로 바꿔서 보낸다."""

    id: str
    title: str
    url: str | None
    source: str | None
    published_at: str | None
    summary: str
    financial_link: FinancialLink | None = None
    is_placeholder: bool


class NewsArticle(BaseModel):
    """응답용 뉴스 한 건. 프론트 `NewsItem` (뉴스 탭·리포트 근거·시장 뉴스 공통)과 같다.

    기사 전문은 보내지 않는다 (제목·요약·원문 링크만).
    """

    id: str
    title: str
    press: str  # 언론사
    published_at: str  # ISO 8601 (시드 값은 "2026년 1월"처럼 날짜를 모르는 경우가 있다)
    url: str
    summary_short: str
    summary_long: str | None = None
    company_stock_code: str | None  # 시장 전체 기사면 null
    related_metric: FinancialLink | None = None


class NewsPage(BaseModel):
    items: list[NewsArticle]
    page: int
    size: int
    has_next: bool
