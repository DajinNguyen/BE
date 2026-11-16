def test_hot_industries(client):
    res = client.get("/api/industries/hot", params={"period": "1w"})
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 10
    scores = [i["heat_score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    # 점수 = 주가 40% + 거래대금 30% + 뉴스 30% (AI 1주: 6.2, 48, 62)
    assert items[0]["industry_id"] == "ai" and items[0]["heat_score"] == 35.5
    assert [i["heat_level"] for i in items] == ["high"] * 3 + ["mid"] * 3 + ["low"] * 4
    assert items[0]["headline_metric"] == {"label": "최근 1주", "value": 6.2, "unit": "%"}
    assert items[0]["as_of"].endswith("T15:30:00+09:00")
    assert all(i["is_sample"] for i in items)


def test_hot_industries_month(client):
    items = client.get("/api/industries/hot", params={"period": "1m"}).json()
    assert items[0]["headline_metric"]["label"] == "최근 1개월"


def test_hot_industries_invalid_period(client):
    assert client.get("/api/industries/hot", params={"period": "1y"}).status_code == 422


def test_industry_detail(client):
    res = client.get("/api/industries/semiconductor")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "반도체" and body["description"]
    assert body["why"]["text"]
    assert body["why"]["news_ids"] == [n["id"] for n in body["news"]] and len(body["news"]) == 2
    assert len(body["trend"]) == 5
    assert body["trend"][0]["value"] == 0 and body["trend"][-1]["value"] == 10.1
    assert body["score_breakdown"] == {
        "price_change_rate": 4.8,
        "trading_value_change_rate": 35,
        "news_mention_change_rate": 40,
        "period": "1w",
    }
    companies = body["companies"]
    assert companies[0]["stock_code"] == "005930" and companies[0]["intro"]
    caps = [c["market_cap"] for c in companies]
    assert caps == sorted(caps, reverse=True)


def test_industry_not_found(client):
    res = client.get("/api/industries/unknown")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "INDUSTRY_NOT_FOUND"
    assert client.get("/api/industries/unknown/news").json()["error"]["code"] == (
        "INDUSTRY_NOT_FOUND"
    )


def test_industry_news(client):
    news = client.get("/api/industries/ai/news", params={"size": 3}).json()
    assert [n["id"] for n in news] == ["ai-news-1", "ai-news-2", "ai-news-3"]
    assert all(n["press"] == "예시 뉴스" for n in news)


def test_industry_without_heat_data_is_not_ranked_but_has_a_page(client):
    hot = client.get("/api/industries/hot", params={"period": "1w"}).json()
    assert "holding" not in {i["industry_id"] for i in hot}
    detail = client.get("/api/industries/holding").json()
    assert detail["name"] == "지주회사" and detail["icon_key"] == "holding"
    assert detail["score_breakdown"]["price_change_rate"] == 0
    assert len(detail["companies"]) > 50
