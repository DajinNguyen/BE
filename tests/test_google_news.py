import httpx
import pytest

from app.services import google_news, naver_news


def _item(n, hour, source='<source url="https://www.hankyung.com">한국경제</source>'):
    return f"""<item>
<title>삼성전자 기사 {n} - 한국경제</title>
<link>https://news.google.com/rss/articles/A{n}?oc=5</link>
<pubDate>Mon, 02 Nov 2026 {hour:02d}:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;삼성전자 기사 {n}&lt;/a&gt;</description>
{source}
</item>"""


def _rss(items):
    return f'<?xml version="1.0"?><rss><channel>{"".join(items)}</channel></rss>'.encode()


class FakeGoogle:
    def __init__(self, body=None, fail=False):
        # 일부러 최신순이 아니게 (관련도 순으로 온다)
        self.body = body or _rss([_item(1, 1), _item(2, 5), _item(3, 3)])
        self.fail = fail
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        request = httpx.Request("GET", url)
        if self.fail:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, content=self.body, request=request)


@pytest.fixture
def use_google(monkeypatch):
    def install(api: FakeGoogle) -> google_news.GoogleNewsClient:
        monkeypatch.setattr(google_news.httpx, "get", api)
        client = google_news.GoogleNewsClient()
        monkeypatch.setattr(google_news, "get_google_news", lambda: client)
        return client

    return install


def test_parse_sorts_latest_first_and_strips_press(use_google):
    client = use_google(FakeGoogle())
    result = client.search("삼성전자", 1, 10, "005930")
    assert result.total == 3
    assert [a.title for a in result.articles] == [
        "삼성전자 기사 2",
        "삼성전자 기사 3",
        "삼성전자 기사 1",
    ]
    first = result.articles[0]
    assert first.press == "한국경제"
    assert first.published_at == "2026-11-02T14:00:00+09:00"
    assert first.url == "https://news.google.com/rss/articles/A2?oc=5"
    assert first.summary_short == "" and first.company_stock_code == "005930"


def test_paging_and_cache(use_google):
    api = FakeGoogle()
    client = use_google(api)
    assert [a.title for a in client.search("삼성전자", 2, 1).articles] == ["삼성전자 기사 3"]
    client.search("삼성전자", 3, 1)
    assert len(api.calls) == 1  # 페이지가 달라도 같은 검색은 한 번만 받는다
    assert api.calls[0]["q"] == (
        "삼성전자 -site:naver.com -site:tistory.com -site:daum.net when:7d"
    )


def test_failure_returns_none_and_backs_off(use_google):
    api = FakeGoogle(fail=True)
    client = use_google(api)
    assert client.search("삼성전자", 1, 10) is None
    assert client.search("삼성전자", 1, 10) is None
    assert len(api.calls) == 1


def test_company_news_uses_google_without_naver_key(client, use_google):
    use_google(FakeGoogle())
    page = client.get("/api/companies/005930/news", params={"size": 2}).json()
    assert [a["title"] for a in page["items"]] == ["삼성전자 기사 2", "삼성전자 기사 3"]
    assert page["has_next"] is True


def test_naver_failure_falls_back_to_google(client, use_google, monkeypatch):
    use_google(FakeGoogle())
    naver = naver_news.NaverNewsClient("id", "secret")
    monkeypatch.setattr(naver, "_fetch", lambda *args: None)  # 네이버를 받지 못함
    monkeypatch.setattr(naver_news, "get_naver_news", lambda: naver)
    news = client.get("/api/market/news", params={"date": "2026-11-02"}).json()
    assert news[0]["press"] == "한국경제"


def test_market_reasons_use_live_news(client, use_google):
    use_google(FakeGoogle())
    summary = client.get("/api/market/summary").json()
    news = [n for r in summary["reasons"] for n in r["news"]]
    assert len(news) == 6 and all(n["press"] == "한국경제" for n in news)


def test_relevance_sort_keeps_google_order(use_google):
    client = use_google(FakeGoogle())
    titles = [a.title for a in client.search("삼성전자", 1, 10, sort="sim").articles]
    assert titles == ["삼성전자 기사 1", "삼성전자 기사 2", "삼성전자 기사 3"]


def test_drops_blogs_and_non_news(use_google):
    blog = '<source url="https://blog.naver.com">Naver Blog</source>'
    premium = '<source url="https://contents.premium.naver.com">네이버 프리미엄콘텐츠</source>'
    tistory = '<source url="https://abc.tistory.com">티스토리</source>'
    deal = '<source url="https://zod.kr">조드</source>'  # 핫딜 게시판
    small_press = '<source url="https://www.pinpointnews.co.kr">핀포인트뉴스</source>'
    body = _rss(
        [
            _item(1, 1),
            _item(2, 2, blog),
            _item(3, 3, premium),
            _item(4, 4, tistory),
            _item(5, 5, ""),
            _item(6, 6, deal),
            _item(7, 7, small_press),
        ]
    )
    client = use_google(FakeGoogle(body))
    press = [a.press for a in client.search("삼성전자", 1, 10).articles]
    assert press == ["핀포인트뉴스", "한국경제"]


def test_naver_news_searched_by_korean_name(client, use_google):
    api = FakeGoogle()
    use_google(api)
    client.get("/api/companies/035420/news")
    assert api.calls[0]["q"].startswith("네이버 ")
