def test_home_dashboard(client):
    res = client.get("/api/home")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {
        "base_time",
        "trading_value_top",
        "sector_companies",
        "rising",
        "most_viewed",
        "ai_analyzed",
    }
    assert body["base_time"].endswith(" 15:30")

    top = body["trading_value_top"]
    assert len(top) == 30
    assert top[0]["stock_code"] == "005930"
    assert top[0]["trading_value"] == {"value": 21450, "is_sample": True}
    assert top[0]["one_liner"]
    assert top[0]["ai_headline"] is None  # 리포트가 아직 없다
    values = [c["trading_value"]["value"] for c in top]
    assert values == sorted(values, reverse=True)

    assert body["rising"]["criteria"]
    assert len(body["rising"]["items"]) == 6
    assert len(body["most_viewed"]["items"]) == 6
    assert body["ai_analyzed"] == []


def test_home_sector_companies(client):
    sectors = client.get("/api/home").json()["sector_companies"]
    assert list(sectors) == ["semiconductor", "auto", "internet", "finance", "food", "etc"]
    assert [c["name"] for c in sectors["semiconductor"]][:2] == ["삼성전자", "SK하이닉스"]
    assert {c["name"] for c in sectors["food"]} == {"CJ제일제당", "오리온", "농심", "삼양식품"}
    for group, companies in sectors.items():
        assert all(c["sector_group"] == group for c in companies)
    # 모든 회사가 정확히 한 탭에 들어간다
    assert sum(len(c) for c in sectors.values()) == 41


def test_home_lists_generated_reports(client):
    client.post("/api/companies/005930/report")
    body = client.get("/api/home").json()
    analyzed = body["ai_analyzed"]
    assert len(analyzed) == 1
    assert analyzed[0]["stock_code"] == "005930"
    assert analyzed[0]["name"] == "삼성전자"
    assert analyzed[0]["headline"] == "3년 동안 이익이 크게 늘어난 회사예요"
    assert analyzed[0]["generated_at"].endswith("+09:00")

    top = {c["stock_code"]: c for c in body["trading_value_top"]}
    assert top["005930"]["ai_headline"] == "3년 동안 이익이 크게 늘어난 회사예요"
    assert top["000660"]["ai_headline"] is None
