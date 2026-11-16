import httpx
import pytest

from app.services import naver_news


def _item(n, title=None, origin=None):
    return {
        "title": title or f"<b>삼성전자</b>, 기사 {n} &quot;HBM&quot;",
        "originallink": origin if origin is not None else f"https://www.yna.co.kr/view/{n}",
        "link": f"https://n.news.naver.com/mnews/article/001/{n}",
        "description": f"<b>삼성전자</b>가 {n}번째 소식을 &amp; 전했다.",
        "pubDate": "Mon, 02 Nov 2026 10:30:00 +0900",
    }


class FakeNaverSearch:
    """httpx.get 대신 쓰는 가짜 네이버 검색 API."""

    def __init__(self, total=25, fail=False, items=None):
        self.total = total
        self.fail = fail
        self.items = items
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        request = httpx.Request("GET", url)
        if self.fail:
            return httpx.Response(401, json={"errorCode": "024"}, request=request)
        start, display = params["start"], params["display"]
        items = self.items or [_item(n) for n in range(start, min(start + display, self.total + 1))]
        body = {"total": self.total, "start": start, "display": len(items), "items": items}
        return httpx.Response(200, json=body, request=request)


@pytest.fixture
def use_search(monkeypatch):
    """가짜 검색 API에 붙은 NaverNewsClient로 바꿔 끼운다."""

    def install(api: FakeNaverSearch) -> naver_news.NaverNewsClient:
        monkeypatch.setattr(naver_news.httpx, "get", api)
        client = naver_news.NaverNewsClient("id", "secret")
        monkeypatch.setattr(naver_news, "get_naver_news", lambda: client)
        return client

    return install


def test_parse_cleans_text_and_finds_press(use_search):
    use_search(FakeNaverSearch(total=1))
    article = naver_news.search("삼성전자", 1, 10, "005930").articles[0]
    assert article.title == '삼성전자, 기사 1 "HBM"'
    assert article.summary_short == "삼성전자가 1번째 소식을 & 전했다."
    assert article.press == "연합뉴스"
    assert article.url == "https://www.yna.co.kr/view/1"
    assert article.published_at == "2026-11-02T10:30:00+09:00"
    assert article.company_stock_code == "005930"
    assert article.id.startswith("naver-")


def test_press_name():
    assert naver_news.press_name("https://biz.chosun.com/a") == "조선비즈"  # 가장 길게 맞는 것
    assert naver_news.press_name("https://www.chosun.com/a") == "조선일보"
    assert naver_news.press_name("https://news.unknown.kr/a") == "news.unknown.kr"


def test_hub_and_developers_headers(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen[url] = headers
        body = {"total": 0, "items": []}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(naver_news.httpx, "get", fake_get)
    naver_news.NaverNewsClient("id", "secret").search("삼성전자", 1, 10)
    naver_news.NaverNewsClient("id", "secret", hub=False).search("삼성전자", 1, 10)
    assert seen[naver_news.HUB_URL] == {
        "X-NCP-APIGW-API-KEY-ID": "id",
        "X-NCP-APIGW-API-KEY": "secret",
    }
    assert seen[naver_news.DEVELOPERS_URL]["X-Naver-Client-Id"] == "id"


def test_without_original_link_uses_naver_link(use_search):
    use_search(FakeNaverSearch(items=[_item(1, origin="")]))
    article = naver_news.search("삼성전자", 1, 10).articles[0]
    assert article.url.startswith("https://n.news.naver.com/")


def test_duplicate_titles_are_dropped(use_search):
    use_search(FakeNaverSearch(items=[_item(1, title="같은 기사"), _item(2, title="같은  기사")]))
    assert len(naver_news.search("삼성전자", 1, 10).articles) == 1


def test_cache_and_backoff(use_search):
    api = FakeNaverSearch()
    client = use_search(api)
    client.search("삼성전자", 1, 10)
    client.search("삼성전자", 1, 10)  # 10분 안에는 다시 묻지 않는다
    assert len(api.calls) == 1

    api.fail = True
    assert client.search("카카오", 1, 10) is None
    assert client.search("카카오", 1, 10) is None
    assert len(api.calls) == 2  # 실패 직후에는 매번 다시 부르지 않는다


def test_company_news_from_naver(client, use_search):
    use_search(FakeNaverSearch(total=25))
    page = client.get("/api/companies/000660/news", params={"size": 10, "page": 3}).json()
    assert [a["title"] for a in page["items"]][0] == '삼성전자, 기사 21 "HBM"'
    assert len(page["items"]) == 5 and page["has_next"] is False
    assert page["items"][0]["company_stock_code"] == "000660"  # 시드 뉴스가 없는 회사도 나온다


def test_company_news_query_and_paging(client, use_search):
    api = FakeNaverSearch(total=25)
    use_search(api)
    page = client.get("/api/companies/005930/news", params={"size": 10, "page": 2}).json()
    assert api.calls[-1] == {"query": "삼성전자", "start": 11, "display": 10, "sort": "date"}
    assert page["has_next"] is True and page["page"] == 2


def test_company_news_falls_back_to_seed(client, use_search):
    use_search(FakeNaverSearch(fail=True))
    page = client.get("/api/companies/005930/news").json()
    assert page["items"][0]["press"] == "삼성전자 뉴스룸"


def test_market_and_industry_news_from_naver(client, use_search):
    api = FakeNaverSearch(total=3)
    use_search(api)
    market = client.get("/api/market/news", params={"date": "2026-11-02"}).json()
    assert len(market) == 3 and market[0]["company_stock_code"] is None
    assert api.calls[-1]["query"] == "증시"

    industry = client.get("/api/industries/internet/news", params={"size": 2}).json()
    assert len(industry) == 2 and api.calls[-1]["query"] == "인터넷 플랫폼 산업"


def test_report_uses_naver_news(client, use_search, fake_writer):
    use_search(FakeNaverSearch(total=3))
    client.post("/api/companies/005930/report")
    report = client.get("/api/companies/005930/report").json()
    news = report["evidence"]["news"]
    assert len(news) == 3 and news[0]["press"] == "연합뉴스"
    assert report["data_as_of"]["news"] == "2026-11-02"
    assert fake_writer.calls[0]["news"][0]["title"].startswith("삼성전자, 기사 1")
