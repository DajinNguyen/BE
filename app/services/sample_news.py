"""시장·산업 예시 기사.

아직 외부 뉴스 API를 붙이지 않아서, 프론트 목 데이터와 같은 예시 기사를 내려보낸다.
실제 기사처럼 보이지 않게 언론사는 "예시 뉴스", 주소는 example.com 으로 둔다.
시각은 지금 기준으로 계산해서 화면의 "3시간 전" 표시를 확인할 수 있게 한다.
"""

from datetime import datetime, timedelta

from app.schemas.news import NewsArticle
from app.services.market_clock import KST

SAMPLE_PRESS = "예시 뉴스"


def sample_article(
    article_id: str, title: str, summary: str, hours_ago: float, url: str, now: datetime | None
) -> NewsArticle:
    published = (now or datetime.now(KST)).astimezone(KST) - timedelta(hours=hours_ago)
    return NewsArticle(
        id=article_id,
        title=title,
        press=SAMPLE_PRESS,
        published_at=published.replace(microsecond=0).isoformat(),
        url=url,
        summary_short=summary,
        company_stock_code=None,
    )


def latest_first(articles: list[NewsArticle]) -> list[NewsArticle]:
    return sorted(articles, key=lambda a: a.published_at, reverse=True)
