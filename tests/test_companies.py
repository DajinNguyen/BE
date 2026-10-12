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


def test_company_detail_without_source_data_uses_empty_values(client):
    body = client.get("/api/companies/207940").json()
    assert body["name"] == "삼성바이오로직스"
    assert body["market_cap"] is None
    assert body["homepage_url"] == ""
    assert body["business_description"] == ""
    assert body["business_segments"] == [] and body["timeline"] == []
    assert body["timeline_source"] == ""


def test_company_not_found(client):
    res = client.get("/api/companies/999999")
    assert res.status_code == 404
    assert res.json()["code"] == "COMPANY_NOT_FOUND"
    assert res.json()["detail"]


def test_popular_endpoint_removed(client):
    # 프론트에서 삭제됨. /api/home 으로 대체
    assert client.get("/api/companies/popular").json()["code"] == "COMPANY_NOT_FOUND"


def test_financials(client):
    body = client.get("/api/companies/005930/financials").json()
    assert body["unit"] == "조 원"
    assert [a["fiscal_year"] for a in body["annual"]] == [2023, 2024, 2025]
    assert body["latest_quarter"]["revenue"] == 93.8
    assert len(body["segments"]["items"]) == 4
    assert body["indicators"]["roe"] == {"value": 9.2, "is_sample": True}


def test_financials_without_seed_data_is_data_not_found(client):
    res = client.get("/api/companies/000660/financials")
    assert res.status_code == 404
    assert res.json()["code"] == "DATA_NOT_FOUND"


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
    assert res.json()["code"] == "VALIDATION_ERROR"


def test_prices_unknown_company(client):
    res = client.get("/api/companies/999999/prices", params={"period": "1m"})
    assert res.json()["code"] == "COMPANY_NOT_FOUND"


def test_news(client):
    news = client.get("/api/companies/005930/news").json()
    assert any(not n["is_placeholder"] and n["url"] for n in news)
    assert all(n["url"] is None for n in news if n["is_placeholder"])


def test_news_without_seed_data_is_empty_list(client):
    res = client.get("/api/companies/000660/news")
    assert res.status_code == 200
    assert res.json() == []


def test_terms(client):
    terms = client.get("/api/terms").json()
    assert len(terms) == 66
    assert terms[0]["id"] == "stock"
    assert set(terms[0]) == {"id", "term", "aliases", "category", "level", "easy", "example"}
