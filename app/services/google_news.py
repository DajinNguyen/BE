"""Google 뉴스 RSS 검색 (키·가입 없음).

- 공식 API가 아니고, 피드 이용 조건이 "개인적·비상업적 용도"다. 데모·학습용으로만 쓰고,
  서비스로 내보낼 때는 네이버 검색 API(`naver_news`)로 바꾼다.
- 한 번에 최대 100건 정도를 관련도순으로 주고 페이지 나누기가 없어서,
  최신순이 필요하면 받은 목록을 다시 정렬하고 페이지는 서버에서 나눈다.
- 요약(본문 앞부분)은 주지 않아서 `summary_short` 는 빈 글자다 (화면은 제목·언론사만 보여준다).
- 링크는 Google 뉴스 주소이고, 누르면 원문 기사로 넘어간다.
- 블로그·카페·커뮤니티·회사 홍보 글은 뉴스가 아니라서 검색에서 빼고, 걸러지지 않고 온 것도 버린다.
- 같은 검색은 10분 캐시한다. 받지 못하면 None 을 돌려주고, 호출하는 쪽은 시드·예시 뉴스로 동작한다.
"""

import hashlib
import logging
import threading
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.schemas.news import NewsArticle
from app.services.market_clock import KST
from app.services.naver_news import SearchResult, Sort, drop_duplicates

logger = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search"
HEADERS = {"User-Agent": "Mozilla/5.0"}
RECENT = "when:7d"  # 최근 7일 기사만
TTL = 10 * 60  # 10분
TIMEOUT = 3.0  # 화면이 기다리지 않도록 짧게
RETRY_AFTER = 60  # 받기에 실패하면 이 시간 동안은 다시 시도하지 않는다

# 언론사 기사가 아닌 곳 (블로그·카페·유료 구독 글·커뮤니티·영상·회사 홍보). 하위 도메인도 같이 뺀다.
# naver.com 은 블로그·프리미엄콘텐츠·페이 머니스토리가 섞여 오고, 네이버 뉴스 기사는 원 언론사로 온다.
NOT_NEWS_DOMAINS = (
    "naver.com",
    "navercorp.com",
    "daum.net",
    "tistory.com",
    "brunch.co.kr",
    "kakao.com",
    "youtube.com",
    "medium.com",
    "velog.io",
    "dcinside.com",
    "fmkorea.com",
    "clien.net",
    "ppomppu.co.kr",
    "theqoo.net",
    "instiz.net",
    "namu.wiki",
    "wikipedia.org",
    "instagram.com",
    "facebook.com",
    "x.com",
    "twitter.com",
)
# 검색할 때부터 빼면 그만큼 기사가 더 들어온다 (검색어가 길어지지 않게 많이 섞여 오는 곳만)
EXCLUDE_QUERY = " ".join(f"-site:{d}" for d in ("naver.com", "tistory.com", "daum.net"))


def is_news(source_url: str) -> bool:
    host = (urlparse(source_url).hostname or "").removeprefix("www.")
    return bool(host) and not any(host == d or host.endswith("." + d) for d in NOT_NEWS_DOMAINS)


def _parse_item(item: ET.Element, stock_code: str | None) -> NewsArticle:
    url = item.findtext("link") or ""
    press = item.findtext("source") or ""
    title = item.findtext("title") or ""
    suffix = f" - {press}"
    if press and title.endswith(suffix):  # 제목 끝에 붙은 " - 언론사" 를 뗀다
        title = title[: -len(suffix)]
    published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(KST)
    return NewsArticle(
        id="google-" + hashlib.sha1(url.encode()).hexdigest()[:12],
        title=title.strip(),
        press=press,
        published_at=published.isoformat(),
        url=url,
        summary_short="",
        company_stock_code=stock_code,
    )


class GoogleNewsClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, list[NewsArticle]]] = {}
        self._retry_at = 0.0

    def _fetch(self, query: str) -> bytes | None:
        res = httpx.get(
            RSS_URL,
            params={
                "q": f"{query} {EXCLUDE_QUERY} {RECENT}",
                "hl": "ko",
                "gl": "KR",
                "ceid": "KR:ko",
            },
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        res.raise_for_status()
        return res.content

    def _articles(self, query: str, stock_code: str | None) -> list[NewsArticle] | None:
        """관련도순 기사 전체. 받지 못하면 마지막으로 받은 값, 그것도 없으면 None."""
        key = f"{query}|{stock_code}"
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
        if time.time() < self._retry_at:
            return hit[1] if hit else None
        with self._lock:
            try:
                channel = ET.fromstring(self._fetch(query)).find("channel")
                items = channel.findall("item") if channel is not None else []
            except (httpx.HTTPError, ET.ParseError) as e:
                logger.warning("Google 뉴스를 받지 못했어요 (%s): %s", query, e)
                self._retry_at = time.time() + RETRY_AFTER
                return hit[1] if hit else None
            articles = []
            for item in items:
                source = item.find("source")
                if source is None or not is_news(source.get("url", "")):
                    continue
                try:
                    articles.append(_parse_item(item, stock_code))
                except (TypeError, ValueError) as e:  # 한 건이 이상해도 나머지는 보여준다
                    logger.warning("Google 뉴스 한 건을 읽지 못했어요: %s", e)
            articles = drop_duplicates(articles)
            self._cache[key] = (time.time(), articles)
            return articles

    def search(
        self,
        query: str,
        start: int,
        display: int,
        stock_code: str | None = None,
        sort: Sort = "date",
    ) -> SearchResult | None:
        """기사 목록 (기본 최신순). start 는 1부터 (네이버 검색과 같은 방식)."""
        articles = self._articles(query, stock_code)
        if articles is None:
            return None
        if sort == "date":
            articles = sorted(articles, key=lambda a: a.published_at, reverse=True)
        return SearchResult(total=len(articles), articles=articles[start - 1 : start - 1 + display])


@lru_cache
def get_google_news() -> GoogleNewsClient | None:
    return GoogleNewsClient() if get_settings().google_news_enabled else None
