"""DART 재무제표(fnlttSinglAcnt) → Financials 변환과 API 연결."""

import io
import zipfile
from datetime import date

import httpx
import pytest

from app.schemas.common import SampleValue
from app.services import dart, dart_financials

TODAY = date(2026, 10, 20)
SAMSUNG, HYNIX, KIA, SMALL = "00126380", "00164779", "00106641", "00999999"

CORP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
    <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>
        <corp_eng_name>SAMSUNG ELECTRONICS CO,.LTD</corp_eng_name><stock_code>005930</stock_code></list>
    <list><corp_code>00164779</corp_code><corp_name>에스케이하이닉스</corp_name>
        <corp_eng_name>SK hynix Inc.</corp_eng_name><stock_code>000660</stock_code></list>
    <list><corp_code>00106641</corp_code><corp_name>기아</corp_name>
        <corp_eng_name>KIA CORPORATION</corp_eng_name><stock_code>000270</stock_code></list>
</result>
"""

NO_DATA = {"status": "013", "message": "조회된 데이타가 없습니다."}


def _won(value: int | None) -> str:
    return "" if value is None else f"{value:,}"


def _rows(fs_div: str, accounts: dict[str, tuple]) -> list[dict]:
    """accounts: 계정명 → (당기, 전기, 전전기) 원 단위 금액."""
    rows = []
    for name, amounts in accounts.items():
        th, frm, bfe = (*amounts, None, None)[:3]
        rows.append(
            {
                "fs_div": fs_div,
                "account_nm": name,
                "thstrm_amount": _won(th),
                "frmtrm_amount": _won(frm),
                "bfefrmtrm_amount": _won(bfe),
            }
        )
    return rows


def _ok(rows: list[dict]) -> dict:
    return {"status": "000", "message": "정상", "list": rows}


T = 1_000_000_000_000  # 1조 원

SAMSUNG_2025 = _ok(
    _rows(
        "CFS",
        {
            "매출액": (333_600 * T // 1000, 300_900 * T // 1000, 258_940 * T // 1000),
            "영업이익": (43_600 * T // 1000, 32_700 * T // 1000, 6_570 * T // 1000),
            "당기순이익": (40 * T, 34 * T, 15 * T),
            "부채총계": (100 * T, 90 * T, 92 * T),
            "자본총계": (400 * T, 380 * T, 360 * T),
        },
    )
    # 별도재무제표 행은 연결이 있으면 쓰지 않는다
    + _rows("OFS", {"매출액": (200 * T, 1, 1), "영업이익": (10 * T, 1, 1)})
)

SAMSUNG_2026_HALF = _ok(
    _rows("CFS", {"매출액": (80 * T, 74 * T), "영업이익": (9_500 * T // 1000, 10 * T)})
)


class FakeDartApi:
    """httpx.get 대신 쓰는 가짜 OpenDART. 재무제표는 (고유번호, 연도, 보고서 코드)로 찾는다."""

    def __init__(self, statements: dict[tuple[str, str, str], dict], fail: bool = False):
        self.statements = statements
        self.fail = fail
        self.calls: list[tuple] = []

    def __call__(self, url, params=None, timeout=None):
        request = httpx.Request("GET", url)
        if self.fail:
            raise httpx.ConnectError("down", request=request)
        if url.endswith("corpCode.xml"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("CORPCODE.xml", CORP_XML)
            return httpx.Response(200, content=buf.getvalue(), request=request)
        if url.endswith("fnlttSinglAcnt.json"):
            key = (params["corp_code"], params["bsns_year"], params["reprt_code"])
            self.calls.append(key)
            return httpx.Response(200, json=self.statements.get(key, NO_DATA), request=request)
        return httpx.Response(200, json={"status": "013"}, request=request)


@pytest.fixture
def use_dart(tmp_path, monkeypatch):
    def install(api: FakeDartApi) -> dart.DartClient:
        monkeypatch.setattr(dart.httpx, "get", api)
        client = dart.DartClient("test-key", tmp_path)
        monkeypatch.setattr(dart, "get_dart", lambda: client)
        monkeypatch.setattr(dart_financials, "_today", lambda: TODAY)
        return client

    return install


MARKET_CAP = SampleValue(value=1200, is_sample=False)  # 조 원


def fetch(corp_code=SAMSUNG, market_cap=MARKET_CAP):
    return dart_financials.fetch_financials("005930", corp_code, market_cap)


# ---------------------------------------------------------------------- DartClient


def test_single_accounts_caches_result(use_dart, tmp_path):
    api = FakeDartApi({(SAMSUNG, "2025", "11011"): SAMSUNG_2025})
    client = use_dart(api)
    rows = client.single_accounts(SAMSUNG, 2025, "11011")
    assert rows[0]["account_nm"] == "매출액"
    client.single_accounts(SAMSUNG, 2025, "11011")
    assert dart.DartClient("test-key", tmp_path).single_accounts(SAMSUNG, 2025, "11011") == rows
    assert api.calls == [(SAMSUNG, "2025", "11011")]


def test_single_accounts_no_data_is_empty_and_error_is_none(use_dart):
    client = use_dart(FakeDartApi({}))
    assert client.single_accounts(SAMSUNG, 2026, "11014") == []
    client = use_dart(FakeDartApi({}, fail=True))
    assert client.single_accounts(SAMSUNG, 2025, "11011") is None


# ---------------------------------------------------------------------- 변환


def test_annual_from_latest_business_report_in_trillion_won(use_dart):
    use_dart(FakeDartApi({(SAMSUNG, "2025", "11011"): SAMSUNG_2025}))
    f = fetch()
    assert f.unit == "조 원"
    assert f.basis == "연결 기준"
    assert [(a.fiscal_year, a.revenue, a.operating_income) for a in f.annual] == [
        (2023, 258.94, 6.57),
        (2024, 300.9, 32.7),
        (2025, 333.6, 43.6),
    ]
    assert f.indicators_as_of == "2025년 사업보고서"
    assert f.source == "DART 2025년 사업보고서"
    assert f.segments is None


def test_falls_back_to_previous_business_report(use_dart):
    api = FakeDartApi({(SAMSUNG, "2024", "11011"): SAMSUNG_2025})
    use_dart(api)
    f = fetch()
    assert [a.fiscal_year for a in f.annual] == [2022, 2023, 2024]
    assert (SAMSUNG, "2025", "11011") in api.calls


def test_separate_statements_when_no_consolidated(use_dart):
    rows = _ok(_rows("OFS", {"매출액": (5 * T, 4 * T), "영업이익": (T, T // 2)}))
    use_dart(FakeDartApi({(SAMSUNG, "2025", "11011"): rows}))
    f = fetch()
    assert f.basis == "별도 기준"
    assert [(a.fiscal_year, a.revenue) for a in f.annual] == [(2024, 4.0), (2025, 5.0)]


def test_operating_revenue_used_when_no_sales_line(use_dart):
    # 금융회사는 매출액 대신 영업수익
    rows = _ok(_rows("CFS", {"영업수익": (30 * T, 28 * T), "영업이익": (6 * T, 5 * T)}))
    use_dart(FakeDartApi({(SAMSUNG, "2025", "11011"): rows}))
    assert [a.revenue for a in fetch().annual] == [28.0, 30.0]


def test_small_company_still_in_trillion_won(use_dart):
    # 프론트는 금액에 항상 "조 원"을 붙여 보여줘요
    rows = _ok(
        _rows("CFS", {"매출액": (812_345_000_000, 700_000_000_000), "영업이익": (-32_100_000_000,)})
    )
    use_dart(FakeDartApi({(SMALL, "2025", "11011"): rows}))
    f = fetch(SMALL)
    assert f.unit == "조 원"
    # 영업이익이 없는 해는 빼요
    assert [(a.fiscal_year, a.revenue, a.operating_income) for a in f.annual] == [
        (2025, 0.81, -0.03)
    ]


def test_latest_quarter_from_newest_quarterly_report(use_dart):
    use_dart(
        FakeDartApi(
            {
                (SAMSUNG, "2025", "11011"): SAMSUNG_2025,
                (SAMSUNG, "2026", "11012"): SAMSUNG_2026_HALF,
                (SAMSUNG, "2026", "11013"): _ok(
                    _rows("CFS", {"매출액": (1, 1), "영업이익": (1, 1)})
                ),
            }
        )
    )
    f = fetch()
    assert f.latest_quarter.label == "2026년 2분기"
    assert (f.latest_quarter.revenue, f.latest_quarter.operating_income) == (80.0, 9.5)
    assert f.source == "DART 2025년 사업보고서 · 2026년 반기보고서"


def test_indicators_from_statements_and_market_cap(use_dart):
    use_dart(FakeDartApi({(SAMSUNG, "2025", "11011"): SAMSUNG_2025}))
    ind = fetch(market_cap=SampleValue(value=1200, is_sample=False)).indicators
    assert ind.roe == SampleValue(value=10.0, is_sample=False)  # 40 / 400
    assert ind.debt_ratio == SampleValue(value=25.0, is_sample=False)  # 100 / 400
    assert ind.per == SampleValue(value=30.0, is_sample=False)  # 1200 / 40
    assert ind.pbr == SampleValue(value=3.0, is_sample=False)  # 1200 / 400
    # 시가총액이 예시 값이면 PER·PBR도 예시 값
    ind = fetch(market_cap=SampleValue(value=1200, is_sample=True)).indicators
    assert ind.per.is_sample and ind.pbr.is_sample and not ind.roe.is_sample


def test_none_when_no_business_report_or_dart_down(use_dart):
    use_dart(FakeDartApi({}))
    assert fetch() is None
    use_dart(FakeDartApi({}, fail=True))
    assert fetch() is None


# ---------------------------------------------------------------------- API 연결


def test_financials_api_uses_dart_for_company_without_seed(client, use_dart):
    use_dart(FakeDartApi({(HYNIX, "2025", "11011"): SAMSUNG_2025}))
    res = client.get("/api/companies/000660/financials")
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "DART 2025년 사업보고서"
    assert [a["revenue"] for a in body["annual"]] == [258.94, 300.9, 333.6]
    assert body["indicators"]["roe"] == {"value": 10.0, "is_sample": False}
    # 프론트는 segments가 항상 있다고 봐요. 사업별 매출이 없으면 빈 목록
    assert body["segments"]["items"] == []
    assert body["segments"]["base_label"]


def test_report_without_segments_has_no_vision_evidence(client, use_dart, fake_writer):
    # 기아는 시드에 사업 소개·계획이 없다
    use_dart(FakeDartApi({(KIA, "2025", "11011"): SAMSUNG_2025}))
    client.post("/api/companies/000270/report")
    # 사업별 매출도 없으면 vision 근거를 내놓지 않는다
    assert fake_writer.calls[0]["available_evidence"] == ["financials"]
    assert client.get("/api/companies/000270/report").json()["evidence"]["vision"] is None


def test_dart_keeps_seed_segments(client, use_dart):
    use_dart(FakeDartApi({(SAMSUNG, "2025", "11011"): SAMSUNG_2025}))
    body = client.get("/api/companies/005930/financials").json()
    assert body["source"] == "DART 2025년 사업보고서"
    assert [s["code"] for s in body["segments"]["items"]] == ["DS", "MX", "SDC", "HARMAN"]


def test_seed_used_when_dart_has_no_statements(client, use_dart):
    use_dart(FakeDartApi({}))
    body = client.get("/api/companies/005930/financials").json()
    assert body["source"] == "삼성전자 공식 실적 발표 (2026년 1월)"


def test_report_for_company_without_seed_uses_dart(client, use_dart, fake_writer):
    use_dart(FakeDartApi({(HYNIX, "2025", "11011"): SAMSUNG_2025}))
    client.post("/api/companies/000660/report")
    report = client.get("/api/companies/000660/report").json()
    assert report["status"] == "done"
    assert fake_writer.calls[0]["financials"]["source"] == "DART 2025년 사업보고서"
    assert report["evidence"]["financials"]["years"] == ["2023", "2024", "2025"]
    assert "DART 2025년 사업보고서" in report["sources"]
