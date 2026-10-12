def test_home_dashboard(client):
    res = client.get("/api/home")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"base_time", "trading_value_top", "rising", "most_viewed", "ai_analyzed"}
    assert body["base_time"].endswith(" 15:30")

    top = body["trading_value_top"]
    assert len(top) == 8
    assert top[0]["stock_code"] == "005930"
    assert top[0]["trading_value"] == {"value": 21450, "is_sample": True}
    assert top[0]["one_liner"]

    assert body["rising"]["criteria"]
    assert len(body["rising"]["items"]) == 6
    assert len(body["most_viewed"]["items"]) == 6
    assert body["ai_analyzed"] == []


def test_home_lists_generated_reports(client):
    client.post("/api/companies/005930/report")
    analyzed = client.get("/api/home").json()["ai_analyzed"]
    assert len(analyzed) == 1
    assert analyzed[0]["stock_code"] == "005930"
    assert analyzed[0]["name"] == "삼성전자"
    assert analyzed[0]["headline"] == "3년 동안 이익이 크게 늘어난 회사예요"
    assert analyzed[0]["generated_at"].endswith("+09:00")
