from datetime import date


def test_search_matches_korean_english_and_code(client):
    for query in [
        "삼성전자",
        "삼 성 전 자",
        "samsung electronics",
        "SAMSUNG ELECTRONICS",
        "005930",
    ]:
        res = client.get("/api/companies", params={"query": query})
        assert res.status_code == 200
        assert [c["stock_code"] for c in res.json()] == ["005930"], query


def test_search_puts_exact_match_first_and_respects_limit(client):
    res = client.get("/api/companies", params={"query": "삼성", "limit": 20})
    codes = [c["stock_code"] for c in res.json()]
    assert len(codes) > 1 and "005930" in codes

    exact = client.get("/api/companies", params={"query": "SK"}).json()
    assert exact[0]["name"] == "SK"

    limited = client.get("/api/companies", params={"query": "삼성", "limit": 2}).json()
    assert len(limited) == 2


def test_search_no_result_is_empty_list(client):
    res = client.get("/api/companies", params={"query": "없는회사"})
    assert res.status_code == 200
    assert res.json() == []


def test_company_summary_fields_match_frontend(client):
    body = client.get("/api/companies", params={"query": "005930"}).json()[0]
    assert body == {
        "stock_code": "005930",
        "corp_code": "00126380",
        "name": "삼성전자",
        "name_en": "Samsung Electronics",
        "sector": "반도체·전자제품",
        "sector_group": "semiconductor",
        "market": "KOSPI",
        "current_price": {"value": 128500, "is_sample": True},
        "change_rate": {"value": 1.82, "is_sample": True},
        "one_liner": "스마트폰과 반도체를 만드는 회사예요",
        "market_alert": None,
    }


def test_companies_by_codes_keeps_order_and_skips_unknown(client):
    res = client.get("/api/companies", params={"stock_codes": "000660,999999,005930"})
    assert res.status_code == 200
    assert [c["stock_code"] for c in res.json()] == ["000660", "005930"]


def test_company_detail(client):
    res = client.get("/api/companies/005930")
    assert res.status_code == 200
    body = res.json()
    assert "has_report" not in body
    assert body["market_cap"]["is_sample"] is True
    assert body["timeline"][-1]["is_plan"] is True
    assert len(body["business_segments"]) == 4
    # 시세 기준일은 직전 장 마감일 (주말이 아닌 날)
    assert date.fromisoformat(body["price_as_of"]).weekday() < 5


def test_company_detail_without_source_data_uses_empty_values(client):
    body = client.get("/api/companies/207940").json()
    assert body["name"] == "삼성바이오로직스"
    # 산업 페이지용 시가총액 예시 값만 있다
    assert body["market_cap"] == {"value": 75.0, "is_sample": True}
    assert body["industries"] == [{"industry_id": "bio", "name": "바이오"}]
    assert body["homepage_url"] == ""
    assert body["business_description"] == ""
    assert body["business_segments"] == [] and body["timeline"] == []
    assert body["timeline_source"] == ""


def test_company_not_found(client):
    res = client.get("/api/companies/999999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "COMPANY_NOT_FOUND"
    assert res.json()["error"]["message"]


def test_popular_endpoint_removed(client):
    # 프론트에서 삭제됨. /api/home 으로 대체
    assert client.get("/api/companies/popular").json()["error"]["code"] == "COMPANY_NOT_FOUND"


def test_financials(client):
    body = client.get("/api/companies/005930/financials").json()
    assert body["unit"] == "조 원"
    assert [a["fiscal_year"] for a in body["annual"]] == [2023, 2024, 2025]
    assert body["latest_quarter"]["revenue"] == 93.8
    assert len(body["segments"]["items"]) == 4
    assert body["indicators"]["roe"] == {"value": 9.2, "is_sample": True}
    assert body["indicators_as_of"] == "2025년 사업보고서"
    perf = body["recent_performance"]
    assert perf["period_label"] == "2025년 4분기 실적"
    assert perf["revenue"] == {
        "value": 93.8,
        "unit": "조 원",
        "yoy_change_rate": 23.7,
        "yoy_is_sample": True,
    }
    assert len(perf["segments"]) == 4 and perf["source"]["url"]


def test_company_detail_industries(client):
    body = client.get("/api/companies/005930").json()
    assert {"industry_id": "semiconductor", "name": "반도체"} in body["industries"]
    assert {"industry_id": "ai", "name": "AI"} in body["industries"]


def test_financials_without_seed_data_is_data_not_found(client):
    res = client.get("/api/companies/000660/financials")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DATA_NOT_FOUND"


def test_prices(client):
    res = client.get("/api/companies/005930/prices", params={"period": "1m"})
    assert res.status_code == 200
    body = res.json()
    assert body["is_sample"] is True and body["period"] == "1m"
    assert body["points"][-1]["close"] == 128500
    dates = [p["date"] for p in body["points"]]
    assert dates == sorted(dates)


def test_prices_one_year_is_weekly(client):
    points = client.get("/api/companies/005930/prices", params={"period": "1y"}).json()["points"]
    assert 52 <= len(points) <= 53
    assert points[-1]["close"] == 128500


def test_prices_invalid_period(client):
    res = client.get("/api/companies/005930/prices", params={"period": "5y"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_prices_unknown_company(client):
    res = client.get("/api/companies/999999/prices", params={"period": "1m"})
    assert res.json()["error"]["code"] == "COMPANY_NOT_FOUND"


def test_news(client):
    page = client.get("/api/companies/005930/news").json()
    assert (page["page"], page["size"], page["has_next"]) == (1, 10, False)
    items = page["items"]
    assert len(items) == 2  # 원문 링크가 없는 예시 자리는 보내지 않는다
    first = items[0]
    assert set(first) == {
        "id",
        "title",
        "press",
        "published_at",
        "url",
        "summary_short",
        "summary_long",
        "company_stock_code",
        "related_metric",
        "sentiment",
        "sentiment_reason",
    }
    assert first["url"] and first["press"] == "삼성전자 뉴스룸"
    assert first["company_stock_code"] == "005930"
    assert first["related_metric"] == {"label": "2025년 매출", "value": "333.6조 원"}
    assert first["sentiment"] in {"very_good", "good", "neutral", "bad", "very_bad"}
    assert first["sentiment_reason"]


def test_news_paging(client):
    page = client.get("/api/companies/005930/news", params={"size": 1}).json()
    assert (len(page["items"]), page["has_next"]) == (1, True)
    page2 = client.get("/api/companies/005930/news", params={"size": 1, "page": 2}).json()
    assert (len(page2["items"]), page2["has_next"]) == (1, False)


def test_news_without_seed_data_is_empty_list(client):
    res = client.get("/api/companies/000660/news")
    assert res.status_code == 200
    assert res.json() == {"items": [], "page": 1, "size": 10, "has_next": False}


def test_news_unknown_company(client):
    res = client.get("/api/companies/999999/news")
    assert (res.status_code, res.json()["error"]["code"]) == (404, "COMPANY_NOT_FOUND")


def test_terms(client):
    terms = client.get("/api/terms").json()
    assert len(terms) == 70
    assert terms[0]["id"] == "stock"
    assert set(terms[0]) == {"id", "term", "aliases", "category", "level", "easy", "example"}
