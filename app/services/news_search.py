"""실제 뉴스 검색: 네이버 검색 API 키가 있으면 네이버, 없거나 받지 못하면 Google 뉴스 RSS.

둘 다 받지 못하면 None 이고, 호출하는 쪽은 시드·예시 뉴스로 동작한다.
"""

from app.schemas.news import NewsArticle
from app.services import google_news, naver_news
from app.services.naver_news import SearchResult, Sort


def search(
    query: str, start: int, display: int, stock_code: str | None = None, sort: Sort = "date"
) -> SearchResult | None:
    """기사 목록 (기본 최신순). start 는 1부터."""
    result = naver_news.search(query, start, display, stock_code, sort)
    if result is not None:
        return result
    client = google_news.get_google_news()
    return client.search(query, start, display, stock_code, sort) if client else None


def latest(query: str, size: int, stock_code: str | None = None) -> list[NewsArticle] | None:
    """최신 기사 size 개. 받지 못하면 None."""
    result = search(query, 1, size, stock_code)
    return None if result is None else result.articles[:size]


def most_relevant(query: str) -> NewsArticle | None:
    """검색어와 가장 잘 맞는 최근 기사 한 건. 없거나 받지 못하면 None."""
    result = search(query, 1, 1, sort="sim")
    return result.articles[0] if result and result.articles else None
