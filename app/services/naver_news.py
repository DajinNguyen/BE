"""네이버 검색 API(뉴스) 연동.

- 공식 API라서 키(Client ID·Secret)가 있어야 한다.
- 2026년 7월 31일부터 새 키는 NAVER API HUB(네이버클라우드)에서만 받을 수 있다.
  예전 개발자센터 키는 2027년 6월 30일까지 쓸 수 있어서 `NAVER_SEARCH_HUB=false` 로 고를 수 있다.
  두 방식은 주소와 인증 헤더 이름만 다르고 요청·응답 형식은 같다.
- 응답은 제목·본문 앞부분·원문 링크·시각뿐이다 (기사 전문은 받지도 보내지도 않는다).
  언론사 이름이 없어서 원문 주소로 언론사를 찾고, 모르는 곳이면 주소를 그대로 보여준다.
- 같은 검색은 10분 캐시한다. 키가 없거나 받지 못하면 None 을 돌려주고,
  호출하는 쪽은 시드·예시 뉴스로 동작한다.
"""

import hashlib
import html
import logging
import re
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.schemas.news import NewsArticle
from app.services.market_clock import KST

logger = logging.getLogger(__name__)

HUB_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
HUB_HEADERS = ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")
DEVELOPERS_URL = "https://openapi.naver.com/v1/search/news.json"
DEVELOPERS_HEADERS = ("X-Naver-Client-Id", "X-Naver-Client-Secret")
TTL = 10 * 60  # 10분
TIMEOUT = 3.0  # 화면이 기다리지 않도록 짧게
RETRY_AFTER = 60  # 받기에 실패하면 이 시간 동안은 다시 시도하지 않는다
MAX_DISPLAY = 100  # 한 번에 받을 수 있는 최대 개수
MAX_START = 1000  # 네이버가 허용하는 가장 뒤 시작 위치

# 원문 주소 → 언론사. 하위 도메인도 같은 언론사로 본다 (가장 길게 맞는 것을 쓴다).
PRESS_BY_DOMAIN = {
    "yna.co.kr": "연합뉴스",
    "yonhapnewstv.co.kr": "연합뉴스TV",
    "einfomax.co.kr": "연합인포맥스",
    "newsis.com": "뉴시스",
    "news1.kr": "뉴스1",
    "chosun.com": "조선일보",
    "biz.chosun.com": "조선비즈",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "hankookilbo.com": "한국일보",
    "seoul.co.kr": "서울신문",
    "segye.com": "세계일보",
    "kmib.co.kr": "국민일보",
    "munhwa.com": "문화일보",
    "nocutnews.co.kr": "노컷뉴스",
    "ohmynews.com": "오마이뉴스",
    "pressian.com": "프레시안",
    "mk.co.kr": "매일경제",
    "hankyung.com": "한국경제",
    "sedaily.com": "서울경제",
    "mt.co.kr": "머니투데이",
    "edaily.co.kr": "이데일리",
    "asiae.co.kr": "아시아경제",
    "fnnews.com": "파이낸셜뉴스",
    "heraldcorp.com": "헤럴드경제",
    "ajunews.com": "아주경제",
    "etoday.co.kr": "이투데이",
    "newspim.com": "뉴스핌",
    "dailian.co.kr": "데일리안",
    "newdaily.co.kr": "뉴데일리",
    "businesspost.co.kr": "비즈니스포스트",
    "bizwatch.co.kr": "비즈워치",
    "thebell.co.kr": "더벨",
    "seoulfn.com": "서울파이낸스",
    "ebn.co.kr": "EBN",
    "kukinews.com": "쿠키뉴스",
    "etnews.com": "전자신문",
    "zdnet.co.kr": "지디넷코리아",
    "ddaily.co.kr": "디지털데일리",
    "dt.co.kr": "디지털타임스",
    "inews24.com": "아이뉴스24",
    "bloter.net": "블로터",
    "ytn.co.kr": "YTN",
    "kbs.co.kr": "KBS",
    "imbc.com": "MBC",
    "sbs.co.kr": "SBS",
    "sbsbiz.co.kr": "SBS Biz",
    "jtbc.co.kr": "JTBC",
    "mbn.co.kr": "MBN",
    "tvchosun.com": "TV조선",
    "ichannela.com": "채널A",
    "wowtv.co.kr": "한국경제TV",
}

Sort = Literal["date", "sim"]  # 최신순, 관련도순

_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SearchResult:
    total: int  # 검색된 전체 기사 수
    articles: list[NewsArticle]


def _clean(text: str) -> str:
    """검색어 강조(<b>)와 &quot; 같은 문자를 걷어낸다."""
    return " ".join(html.unescape(_TAG.sub("", text)).split())


def press_name(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    matches = [d for d in PRESS_BY_DOMAIN if host == d or host.endswith("." + d)]
    return PRESS_BY_DOMAIN[max(matches, key=len)] if matches else host


def _parse_item(item: dict[str, Any], stock_code: str | None) -> NewsArticle:
    url = item.get("originallink") or item["link"]  # 원문이 없으면 네이버 뉴스 주소
    published = parsedate_to_datetime(item["pubDate"]).astimezone(KST)
    return NewsArticle(
        id="naver-" + hashlib.sha1(url.encode()).hexdigest()[:12],
        title=_clean(item["title"]),
        press=press_name(url),
        published_at=published.isoformat(),
        url=url,
        summary_short=_clean(item["description"]),
        company_stock_code=stock_code,
    )


def drop_duplicates(articles: list[NewsArticle]) -> list[NewsArticle]:
    """같은 기사가 여러 곳에 실린 경우가 많아서, 제목이 같으면 처음 것만 남긴다."""
    seen: set[str] = set()
    unique = []
    for a in articles:
        key = "".join(a.title.split())
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


class NaverNewsClient:
    def __init__(self, client_id: str, client_secret: str, hub: bool = True) -> None:
        self._url = HUB_URL if hub else DEVELOPERS_URL
        id_header, secret_header = HUB_HEADERS if hub else DEVELOPERS_HEADERS
        self._headers = {id_header: client_id, secret_header: client_secret}
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, int, int, Sort], tuple[float, dict[str, Any]]] = {}
        self._retry_at = 0.0

    def _fetch(self, query: str, start: int, display: int, sort: Sort) -> dict[str, Any] | None:
        """검색 결과 원본. 받지 못하면 마지막으로 받은 값, 그것도 없으면 None."""
        key = (query, start, display, sort)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
        if time.time() < self._retry_at:
            return hit[1] if hit else None
        with self._lock:
            try:
                res = httpx.get(
                    self._url,
                    params={"query": query, "start": start, "display": display, "sort": sort},
                    headers=self._headers,
                    timeout=TIMEOUT,
                )
                res.raise_for_status()
                data = res.json()
                data["items"]  # 형식 확인
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                logger.warning("네이버 뉴스를 받지 못했어요 (%s): %s", query, e)
                self._retry_at = time.time() + RETRY_AFTER
                return hit[1] if hit else None
            self._cache[key] = (time.time(), data)
            return data

    def search(
        self,
        query: str,
        start: int,
        display: int,
        stock_code: str | None = None,
        sort: Sort = "date",
    ) -> SearchResult | None:
        """기사 목록 (기본 최신순). start 는 1부터 (네이버 검색 위치)."""
        if start > MAX_START:
            return SearchResult(total=0, articles=[])
        data = self._fetch(query, start, min(display, MAX_DISPLAY), sort)
        if data is None:
            return None
        articles = []
        for item in data["items"]:
            try:
                articles.append(_parse_item(item, stock_code))
            except (KeyError, TypeError, ValueError) as e:  # 한 건이 이상해도 나머지는 보여준다
                logger.warning("네이버 뉴스 한 건을 읽지 못했어요: %s", e)
        return SearchResult(
            total=min(int(data.get("total", 0)), MAX_START), articles=drop_duplicates(articles)
        )


@lru_cache
def get_naver_news() -> NaverNewsClient | None:
    settings = get_settings()
    if not (settings.naver_client_id and settings.naver_client_secret):
        return None
    return NaverNewsClient(
        settings.naver_client_id, settings.naver_client_secret, settings.naver_search_hub
    )


def search(
    query: str, start: int, display: int, stock_code: str | None = None, sort: Sort = "date"
) -> SearchResult | None:
    client = get_naver_news()
    return client.search(query, start, display, stock_code, sort) if client else None
