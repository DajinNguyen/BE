import pytest

from app.core.db import new_session
from app.main import app
from app.models.compare import CompareAnalysisRecord
from app.schemas.common import SampleValue
from app.schemas.financials import AnnualResult, Financials, Indicators
from app.services import compare_service, data_store
from app.services.compare_service import AnalysisDraft, ComparePoint


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


@pytest.fixture
def two(financials):
    financials["000660"] = _financials(
        "000660", {2024: (60.0, 20.0), 2025: (90.0, 45.0)}, debt=45.0
    )
    financials["005930"] = _financials(
        "005930", {2024: (300.0, 30.0), 2025: (330.0, 66.0)}, debt=30.0
    )


def _compare(client, left="000660", right="005930"):
    return client.get("/api/companies/compare", params={"left": left, "right": right})


def test_same_year_and_basis(client, two):
    body = _compare(client).json()
    assert (body["base_period"], body["basis"]) == ("2025년 연간", "연결 기준")
    assert body["analysis"] is None
    assert body["notes"] == []
    # left = 비교할 기업, right = 지금 보는 기업
    assert body["left"] == {
        "stock_code": "000660",
        "name": "SK하이닉스",
        "sector": "반도체",
        "market_cap": data_store.seed_companies()["000660"].market_cap.value,
        "is_financial": False,
        "revenue": 90.0,
        "operating_income": 45.0,
        "operating_margin": 50.0,
        "revenue_growth": 50.0,
        "debt_ratio": 45.0,
        "unit": "조 원",
        "base_period": "2025년 연간",
        "basis": "연결 기준",
    }
    right = body["right"]
    assert (right["stock_code"], right["operating_margin"], right["revenue_growth"]) == (
        "005930",
        20.0,
        10.0,
    )


def test_uses_the_latest_year_both_have(client, financials):
    """한쪽만 새 사업보고서가 나왔으면 앞 연도로 맞추고, 그 해 부채비율은 비워 둔다."""
    financials["000660"] = _financials(
        "000660", {2023: (30.0, -7.5), 2024: (60.0, 20.0), 2025: (90.0, 45.0)}
    )
    financials["005930"] = _financials("005930", {2023: (250.0, 5.0), 2024: (300.0, 30.0)})
    body = _compare(client).json()
    assert body["base_period"] == "2024년 연간"
    assert (body["left"]["revenue"], body["left"]["revenue_growth"]) == (60.0, 100.0)
    assert body["left"]["base_period"] == body["right"]["base_period"] == "2024년 연간"
    assert body["right"]["debt_ratio"] == 40.0
    assert body["left"]["debt_ratio"] is None  # 2025년 값만 있다
    assert any("SK하이닉스의 부채비율은 2025년 값만" in n for n in body["notes"])


def test_different_basis_is_shown_per_company(client, financials):
    financials["000660"] = _financials("000660", {2025: (90.0, 45.0)}, basis="별도 기준")
    financials["005930"] = _financials("005930", {2025: (330.0, 66.0)})
    body = _compare(client).json()
    assert (body["left"]["basis"], body["right"]["basis"]) == ("별도 기준", "연결 기준")
    assert body["basis"] == "연결 기준"  # 지금 보는 기업 기준
    assert any("재무제표 기준이 달라요" in n for n in body["notes"])
    # 전년 값이 없으면 증가율은 지어내지 않는다
    assert body["right"]["revenue_growth"] is None


def test_financial_company_is_flagged(client, financials):
    financials["105560"] = _financials("105560", {2025: (80.0, 8.0)}, debt=1200.0)
    financials["005930"] = _financials("005930", {2025: (330.0, 66.0)})
    body = _compare(client, "105560", "005930").json()
    assert (body["left"]["is_financial"], body["right"]["is_financial"]) == (True, False)
    assert any("금융업이에요" in n for n in body["notes"])


def test_missing_financials_still_shows_the_other_company(client, financials):
    financials["005930"] = _financials("005930", {2024: (300.0, 30.0), 2025: (330.0, 66.0)})
    body = _compare(client).json()
    assert body["base_period"] == ""
    left, right = body["left"], body["right"]
    assert (right["base_period"], right["revenue"]) == ("2025년 연간", 330.0)
    assert (left["base_period"], left["basis"]) == (None, None)
    for key in ("revenue", "operating_income", "operating_margin", "revenue_growth", "debt_ratio"):
        assert left[key] is None
    assert body["notes"] == ["SK하이닉스의 재무제표를 아직 불러오지 못했어요."]


def test_no_overlapping_year(client, financials):
    financials["000660"] = _financials("000660", {2022: (40.0, 7.0)})
    financials["005930"] = _financials("005930", {2025: (330.0, 66.0)})
    body = _compare(client).json()
    assert body["base_period"] == ""
    assert (body["left"]["base_period"], body["right"]["base_period"]) == (
        "2022년 연간",
        "2025년 연간",
    )
    assert any("겹치는 연도가 없어서" in n for n in body["notes"])


@pytest.mark.parametrize(
    "params", [{"left": "005930", "right": "005930"}, {"left": " ", "right": "005930"}]
)
def test_needs_two_different_companies(client, params):
    res = client.get("/api/companies/compare", params=params)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PARAM"


def test_bad_requests(client):
    assert client.get("/api/companies/compare", params={"left": "005930"}).status_code == 422
    res = _compare(client, "999999", "005930")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "COMPANY_NOT_FOUND"
    # "compare" 를 종목코드로 읽지 않는다
    assert client.get("/api/companies/compare").status_code == 422


# ---------------------------------------------------------------- 한눈에 비교 (AI)


class FakeCompareWriter:
    def __init__(self):
        self.calls = []
        self.draft = AnalysisDraft(
            headline="삼성전자는 사업이 넓고, SK하이닉스는 메모리 반도체에 집중해요",
            points=[
                ComparePoint(title="규모", description="삼성전자 매출이 330조 원으로 더 커요."),
                ComparePoint(
                    title="수익성", description="100원을 팔면 SK하이닉스가 50원을 남겨요."
                ),
            ],
            reason="삼성전자는 스마트폰과 가전도 함께 팔아요.",
        )

    def write(self, context):
        self.calls.append(context)
        if isinstance(self.draft, Exception):
            raise self.draft
        return self.draft


@pytest.fixture
def writer(client):
    fake = FakeCompareWriter()
    app.dependency_overrides[compare_service.get_compare_writer] = lambda: fake
    return fake


def test_analysis_is_made_in_background_and_shown_next_time(client, two, writer):
    assert _compare(client).json()["analysis"] is None  # 처음에는 없고, 만들기 시작한다
    body = _compare(client).json()
    assert body["analysis"] == {
        "headline": "삼성전자는 사업이 넓고, SK하이닉스는 메모리 반도체에 집중해요",
        "points": [
            {"title": "규모", "description": "삼성전자 매출이 330조 원으로 더 커요."},
            {"title": "수익성", "description": "100원을 팔면 SK하이닉스가 50원을 남겨요."},
        ],
        "reason": "삼성전자는 스마트폰과 가전도 함께 팔아요.",
        "sources": [],  # 테스트에서는 DART를 부르지 않는다
    }
    # 왼쪽·오른쪽을 바꿔도 같은 글을 쓰고, 다시 만들지 않는다
    assert _compare(client, "005930", "000660").json()["analysis"] == body["analysis"]
    assert len(writer.calls) == 1

    context = writer.calls[0]
    assert (context["fiscal_year"], context["basis"]) == (2025, "연결 기준")
    assert [c["name"] for c in context["companies"]] == ["SK하이닉스", "삼성전자"]
    assert context["companies"][0]["operating_margin_percent"] == 50.0


def test_analysis_is_rewritten_when_the_year_changes(client, two, writer, financials):
    _compare(client)
    financials["000660"] = _financials("000660", {2025: (90.0, 45.0), 2026: (120.0, 60.0)})
    financials["005930"] = _financials("005930", {2025: (330.0, 66.0), 2026: (350.0, 70.0)})
    assert _compare(client).json()["analysis"] is None  # 예전 연도로 쓴 글은 보여주지 않는다
    assert len(writer.calls) == 2
    assert writer.calls[1]["fiscal_year"] == 2026


def test_no_analysis_without_numbers_for_both(client, financials, writer):
    financials["005930"] = _financials("005930", {2025: (330.0, 66.0)})
    _compare(client)
    assert writer.calls == []


def test_failed_analysis_is_not_retried_right_away(client, two, writer):
    writer.draft = RuntimeError("AI 고장")
    _compare(client)
    assert _compare(client).json()["analysis"] is None
    assert len(writer.calls) == 1
    with new_session() as session:
        assert session.get(CompareAnalysisRecord, "000660:005930").status == "failed"


CONTEXT = {"fiscal_year": 2025, "companies": [{"revenue": 330.0, "operating_margin_percent": 50.0}]}


def _draft(
    headline="두 회사는 하는 일이 달라요", description="매출이 330조 원이에요.", reason=None
):
    return AnalysisDraft(
        headline=headline,
        points=[ComparePoint(title="규모", description=description)],
        reason=reason,
    )


@pytest.mark.parametrize(
    "draft",
    [
        _draft(headline="삼성전자가 더 좋은 회사예요"),
        _draft(headline="지금 사세요"),
        _draft(description="매출이 999조 원이에요."),  # 넘긴 자료에 없는 숫자
        _draft(description="앞으로 더 성장할 것 같아요."),
        _draft(headline="  "),
    ],
)
def test_analysis_breaking_the_rules_is_dropped(draft):
    assert compare_service.clean(draft, CONTEXT) is None


def test_clean_keeps_good_sentences_and_drops_bad_ones():
    draft = AnalysisDraft(
        headline="두 회사는 하는 일이 달라요",
        points=[
            ComparePoint(title="규모", description="매출이 330조 원이에요."),
            ComparePoint(title="성장 속도", description="매출이 77% 늘었어요."),
            ComparePoint(title="수익성", description="2025년에 100원을 팔아 50원을 남겼어요."),
        ],
        reason="이 회사를 추천해요.",
    )
    cleaned = compare_service.clean(draft, CONTEXT)
    assert [p.title for p in cleaned.points] == ["규모", "수익성"]
    assert cleaned.reason is None


# ---------------------------------------------------------------- 비슷한 회사


def test_peers_share_an_industry_and_are_close_in_size(client):
    seeds = data_store.seed_companies()
    base = seeds["000660"].market_cap.value
    body = client.get("/api/companies/000660/peers").json()

    assert 3 <= len(body) <= 4
    codes = [c["stock_code"] for c in body]
    assert "000660" not in codes and len(set(codes)) == len(codes)
    # CompanySummary 와 같은 형식
    assert set(body[0]) == set(
        client.get("/api/companies", params={"stock_codes": "000660"}).json()[0]
    )
    mine = {
        code
        for r in data_store.industry_records()
        if "000660" in r["companies"]
        for code in r["companies"]
    }
    assert set(codes) <= mine
    # 시가총액 차이가 작은 순
    gaps = [abs(compare_service.math.log(seeds[c].market_cap.value / base)) for c in codes]
    assert gaps == sorted(gaps)


def test_peers_limit_and_errors(client):
    assert len(client.get("/api/companies/005930/peers", params={"limit": 3}).json()) == 3
    assert client.get("/api/companies/005930/peers", params={"limit": 9}).status_code == 422
    assert client.get("/api/companies/999999/peers").status_code == 404


def test_company_without_industry_has_no_peers(client):
    in_industry = {code for r in data_store.industry_records() for code in r["companies"]}
    lonely = next(code for code in data_store.seed_companies() if code not in in_industry)
    assert client.get(f"/api/companies/{lonely}/peers").json() == []
