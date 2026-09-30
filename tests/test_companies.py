def test_search_matches_korean_english_and_code(client):
    for query in ["삼성", "삼 성 전자", "samsung", "SAMSUNG electronics", "005930"]:
        res = client.get("/api/companies", params={"query": query})
        assert res.status_code == 200
        assert [c["stock_code"] for c in res.json()] == ["005930"], query


def test_search_no_result_is_empty_list(client):
    res = client.get("/api/companies", params={"query": "없는회사"})
    assert res.status_code == 200
    assert res.json() == []


def test_popular(client):
    res = client.get("/api/companies/popular")
    assert res.status_code == 200
    body = res.json()
    assert body[0]["stock_code"] == "005930"
    assert body[0]["current_price"] == {"value": 128500, "is_sample": True}
    assert isinstance(body[0]["current_price"]["value"], int)
    assert "business_description" not in body[0]


def test_company_detail(client):
    res = client.get("/api/companies/005930")
    assert res.status_code == 200
    body = res.json()
    assert body["has_report"] is True
    assert body["market_cap"]["is_sample"] is True
    assert body["timeline"][-1]["is_plan"] is True


def test_company_not_found(client):
    res = client.get("/api/companies/999999")
    assert res.status_code == 404
    assert res.json()["code"] == "COMPANY_NOT_FOUND"
    assert res.json()["detail"]


def test_financials(client):
    body = client.get("/api/companies/005930/financials").json()
    assert body["unit"] == "조 원"
    assert [a["fiscal_year"] for a in body["annual"]] == [2023, 2024, 2025]
    assert body["latest_quarter"]["revenue"] == 93.8
    assert body["indicators"]["roe"] == {"value": 9.2, "is_sample": True}


def test_financials_without_seed_data_are_empty(client):
    body = client.get("/api/companies/000660/financials").json()
    assert body["annual"] == []
    assert body["latest_quarter"] is None


def test_prices(client):
    res = client.get("/api/companies/005930/prices", params={"period": "1m"})
    assert res.status_code == 200
    body = res.json()
    assert body["is_sample"] is True and body["period"] == "1m"
    assert body["points"][-1]["close"] == 128500
    dates = [p["date"] for p in body["points"]]
    assert dates == sorted(dates)


def test_prices_invalid_period(client):
    res = client.get("/api/companies/005930/prices", params={"period": "5y"})
    assert res.status_code == 422
    assert res.json()["code"] == "VALIDATION_ERROR"


def test_prices_unknown_company(client):
    res = client.get("/api/companies/999999/prices", params={"period": "1m"})
    assert res.json()["code"] == "COMPANY_NOT_FOUND"


def test_news_placeholder_has_no_url(client):
    news = client.get("/api/companies/005930/news").json()
    assert any(not n["is_placeholder"] and n["url"] for n in news)
    assert all(n["url"] is None for n in news if n["is_placeholder"])


def test_terms(client):
    terms = client.get("/api/terms").json()
    assert terms[0]["id"] == "revenue"
    assert {"id", "name", "aliases", "easy_meaning", "example"} <= terms[0].keys()
