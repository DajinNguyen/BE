import pytest

from app.schemas.common import SampleValue
from app.schemas.financials import AnnualResult, Financials, Indicators
from app.services import compare_service, data_store


def _financials(code, years, basis="연결 기준", debt=40.0):
    """years: {연도: (매출, 영업이익)}"""
    return Financials(
        stock_code=code,
        unit="조 원",
        basis=basis,
        source="DART 사업보고서",
        annual=[
            AnnualResult(fiscal_year=y, revenue=r, operating_income=o)
            for y, (r, o) in years.items()
        ],
        latest_quarter=None,
        segments=None,
        indicators=Indicators(debt_ratio=SampleValue(value=debt, is_sample=False)),
    )


@pytest.fixture
def financials(monkeypatch):
    """회사별 재무제표를 정해 준다. 없는 회사는 None (재무제표를 못 불러온 회사)."""
    table = {}
    monkeypatch.setattr(data_store, "find_financials", lambda code: table.get(code))
    return table


def _compare(client, codes):
    return client.get("/api/compare", params={"stock_codes": codes})


def test_same_year_and_basis(client, financials):
    financials["005930"] = _financials(
        "005930", {2024: (300.0, 30.0), 2025: (330.0, 66.0)}, debt=30.0
    )
    financials["000660"] = _financials(
        "000660", {2024: (60.0, 20.0), 2025: (90.0, 45.0)}, debt=45.0
    )
    body = _compare(client, "005930,000660").json()

    assert (body["unit"], body["fiscal_year"], body["basis"]) == ("조 원", 2025, "연결 기준")
    assert body["comparable"] is True
    assert body["notes"] == []
    first, second = body["companies"]
    assert [first["stock_code"], second["stock_code"]] == ["005930", "000660"]  # 요청한 순서
    assert first["metrics"] == {
        "revenue": 330.0,
        "operating_income": 66.0,
        "operating_margin": 20.0,
        "revenue_growth": 10.0,
        "debt_ratio": 30.0,
    }
    assert second["metrics"]["operating_margin"] == 50.0
    assert second["metrics"]["revenue_growth"] == 50.0
    assert [y["fiscal_year"] for y in first["annual"]] == [2024, 2025]
    assert (first["is_financial"], first["has_financials"], first["fiscal_year"]) == (
        False,
        True,
        2025,
    )
    assert body["differences"][0] == (
        "2025년 매출은 삼성전자 330.0조 원, SK하이닉스 90.0조 원이에요. 삼성전자 쪽이 3.7배 커요."
    )
    assert len(body["differences"]) == 5


def test_uses_the_latest_year_both_have(client, financials):
    """한쪽만 새 사업보고서가 나왔으면 앞 연도로 맞추고, 그 해 부채비율은 없다고 알려준다."""
    financials["005930"] = _financials("005930", {2023: (250.0, 5.0), 2024: (300.0, 30.0)})
    financials["000660"] = _financials(
        "000660", {2023: (30.0, -7.5), 2024: (60.0, 20.0), 2025: (90.0, 45.0)}
    )
    body = _compare(client, "005930,000660").json()
    assert body["fiscal_year"] == 2024
    first, second = body["companies"]
    assert second["metrics"]["revenue"] == 60.0
    assert second["metrics"]["revenue_growth"] == 100.0
    assert first["metrics"]["debt_ratio"] == 40.0
    assert second["metrics"]["debt_ratio"] is None  # 2025년 값만 있다
    assert any("SK하이닉스의 부채비율은 2025년 값만" in n for n in body["notes"])
    assert [y["fiscal_year"] for y in second["annual"]] == [2023, 2024]
    assert len(body["differences"]) == 4


def test_different_basis_is_not_comparable(client, financials):
    financials["005930"] = _financials("005930", {2025: (330.0, 66.0)})
    financials["000660"] = _financials("000660", {2025: (90.0, 45.0)}, basis="별도 기준")
    body = _compare(client, "005930,000660").json()
    assert (body["basis"], body["comparable"]) == (None, False)
    assert any("재무제표 기준이 달라요" in n for n in body["notes"])
    # 전년 값이 없으면 증가율은 지어내지 않는다
    assert body["companies"][0]["metrics"]["revenue_growth"] is None


def test_financial_company_is_flagged(client, financials):
    financials["005930"] = _financials("005930", {2025: (330.0, 66.0)})
    financials["105560"] = _financials("105560", {2025: (80.0, 8.0)}, debt=1200.0)
    body = _compare(client, "005930,105560").json()
    assert [c["is_financial"] for c in body["companies"]] == [False, True]
    assert body["comparable"] is False
    assert any("금융업이에요" in n for n in body["notes"])


def test_missing_financials_still_shows_the_other_company(client, financials):
    financials["005930"] = _financials("005930", {2024: (300.0, 30.0), 2025: (330.0, 66.0)})
    body = _compare(client, "005930,000660").json()
    assert (body["fiscal_year"], body["comparable"], body["differences"]) == (None, False, [])
    first, second = body["companies"]
    assert (first["fiscal_year"], first["metrics"]["revenue"]) == (2025, 330.0)
    assert second["has_financials"] is False
    assert second["metrics"] == dict.fromkeys(first["metrics"])
    assert body["notes"] == ["SK하이닉스의 재무제표를 아직 불러오지 못했어요."]


def test_small_company_amounts_read_in_eok(client, financials):
    financials["005930"] = _financials("005930", {2025: (330.0, 66.0)})
    financials["000660"] = _financials("000660", {2025: (0.0824, -0.0031)})
    body = _compare(client, "005930,000660").json()
    assert "SK하이닉스 824억 원" in body["differences"][0]
    assert "SK하이닉스 -31억 원" in body["differences"][1]


@pytest.mark.parametrize("codes", ["005930", "005930,005930", "005930,000660,035420", ""])
def test_needs_two_different_companies(client, codes):
    res = _compare(client, codes)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PARAM"


def test_unknown_company_is_404(client):
    res = _compare(client, "005930,999999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "COMPANY_NOT_FOUND"


# ---------------------------------------------------------------- 비슷한 회사


def test_similar_companies_share_an_industry_and_are_close_in_size(client):
    seeds = data_store.seed_companies()
    base = seeds["000660"].market_cap.value
    body = client.get("/api/companies/000660/similar").json()

    assert 3 <= len(body) <= 4
    codes = [c["stock_code"] for c in body]
    assert "000660" not in codes and len(set(codes)) == len(codes)
    mine = {i.industry_id for i in data_store.industries_of("000660")}
    for company in body:
        assert company["industry"]["industry_id"] in mine
        assert company["stock_code"] in next(
            r["companies"]
            for r in data_store.industry_records()
            if r["industry_id"] == company["industry"]["industry_id"]
        )
    # 시가총액 차이가 작은 순
    gaps = [abs(compare_service.math.log(c["market_cap"]["value"] / base)) for c in body]
    assert gaps == sorted(gaps)


def test_similar_companies_limit_and_errors(client):
    assert len(client.get("/api/companies/005930/similar", params={"limit": 3}).json()) == 3
    assert client.get("/api/companies/005930/similar", params={"limit": 9}).status_code == 422
    assert client.get("/api/companies/999999/similar").status_code == 404


def test_company_without_industry_has_no_similar_companies(client):
    in_industry = {code for r in data_store.industry_records() for code in r["companies"]}
    lonely = next(code for code in data_store.seed_companies() if code not in in_industry)
    assert client.get(f"/api/companies/{lonely}/similar").json() == []
