"""실제 뉴스 검색: 네이버 검색 API 키가 있으면 네이버, 없거나 받지 못하면 Google 뉴스 RSS.

둘 다 받지 못하면 None 이고, 호출하는 쪽은 시드·예시 뉴스로 동작한다.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from app.schemas.news import NewsArticle
from app.services import google_news, naver_news
from app.services.market_clock import KST
from app.services.naver_news import SearchResult, Sort

COMPANY_MIN_ARTICLES = 5  # 최근 7일 기사가 이보다 적으면 최근 30일 기사를 쓴다
COMPANY_WIDE_DAYS = 30
COMPANY_FETCH = 100  # 제목을 걸러 낸 뒤에 페이지를 나누려고 한 번에 받는 개수


def search(
    query: str,
    start: int,
    display: int,
    stock_code: str | None = None,
    sort: Sort = "date",
    between: tuple[date, date] | None = None,
) -> SearchResult | None:
    """기사 목록 (기본 최신순). start 는 1부터.

    between 을 주면 Google 뉴스에서 최근 7일 대신 그 기간 기사를 찾는다 (지난 분기 실적 기사 찾기용).
    네이버 검색은 기간을 고를 수 없어서, 호출하는 쪽이 받은 기사의 날짜를 확인해야 한다.
    """
    result = naver_news.search(query, start, display, stock_code, sort)
    if result is not None:
        return result
    client = google_news.get_google_news()
    return client.search(query, start, display, stock_code, sort, between) if client else None


def _compact(text: str) -> str:
    return "".join(text.split()).lower()


def company_news(
    name: str, start: int, display: int, stock_code: str | None = None
) -> SearchResult | None:
    """제목에 회사 이름이 들어간 기사만 (최신순). start 는 1부터.

    그냥 이름으로 찾으면 본문에 이름이 한 번 스친 기사(시황 모음, 전혀 다른 소식)가 대부분이라,
    작은 회사일수록 상관없는 기사로 채워진다. 그래서 제목에 이름이 있는 기사만 남기고,
    최근 7일에 몇 건 없으면 최근 30일로 넓힌다.
    """

    def about(articles: list[NewsArticle]) -> list[NewsArticle]:
        needle = _compact(name)
        return [a for a in articles if needle in _compact(a.title)]

    def page(articles: list[NewsArticle]) -> SearchResult:
        return SearchResult(total=len(articles), articles=articles[start - 1 : start - 1 + display])

    result = naver_news.search(name, 1, COMPANY_FETCH, stock_code)
    if result is not None:  # 네이버 검색은 제목만 찾을 수 없어서 받은 뒤에 거른다
        return page(about(result.articles))
    client = google_news.get_google_news()
    if client is None:
        return None
    query = f'intitle:"{name}"'
    today = datetime.now(KST).date()
    period = (today - timedelta(days=COMPANY_WIDE_DAYS), today + timedelta(days=1))
    # 작은 회사는 최근 7일 기사가 거의 없어서 30일치도 같이 받아 둔다 (차례로 받으면 두 배로 느리다)
    with ThreadPoolExecutor(max_workers=2) as pool:
        recent_job = pool.submit(client.search, query, 1, COMPANY_FETCH, stock_code)
        wide_job = pool.submit(client.search, query, 1, COMPANY_FETCH, stock_code, "date", period)
        recent, wide = recent_job.result(), wide_job.result()
    if recent is None and wide is None:
        return None
    articles = about(recent.articles) if recent else []
    if len(articles) < COMPANY_MIN_ARTICLES and wide is not None:
        articles = about(wide.articles)
    return page(articles)


def latest(query: str, size: int, stock_code: str | None = None) -> list[NewsArticle] | None:
    """최신 기사 size 개. 받지 못하면 None."""
    result = search(query, 1, size, stock_code)
    return None if result is None else result.articles[:size]


def most_relevant(query: str) -> NewsArticle | None:
    """검색어와 가장 잘 맞는 최근 기사 한 건. 없거나 받지 못하면 None."""
    result = search(query, 1, 1, sort="sim")
    return result.articles[0] if result and result.articles else None
