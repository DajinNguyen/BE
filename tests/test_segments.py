import pytest

from app.core.db import new_session
from app.services import dart, data_store, segment_service
from app.services.dart import AnnualReport, DartCorp
from app.services.segment_service import (
    NOT_FOUND_REASON,
    SegmentDraft,
    SegmentExtraction,
    SegmentsUnavailable,
)

REPORT = """II. 사업의 내용
1. 사업의 개요
회사는 조선해양과 토건 사업을 해요.
2. 주요 제품 및 서비스
가. 주요 제품 등의 현황
 | (단위 : 억원,%)
 | 조선해양
 | 102,268(96.03)
 | 토건
 | 4,232(3.97)
 | 합계
 | 106,500(100.0)
"""


def extraction(items, unit="억원", found=True, period="2025"):
    return SegmentExtraction(
        found=found,
        period=period,
        unit=unit,
        items=[SegmentDraft(name=n, revenue=r) for n, r in items],
    )


def test_excerpt_starts_near_product_table():
    text = "가" * 30_000 + REPORT
    part = segment_service.excerpt(text)
    assert "주요 제품 등의 현황" in part and "102,268" in part
    assert len(part) < 10_000
    # 표 제목이 없으면 앞부분
    assert segment_service.excerpt("사업 소개뿐") == "사업 소개뿐"


def test_to_segments_converts_table_unit_to_trillion():
    segments = segment_service.to_segments(
        extraction([("토건", "4,232"), ("조선·해양", "102,268")]), REPORT, 10.65
    )
    assert segments.base_label == "2025년 연간 기준"
    assert [(s.name, s.revenue) for s in segments.items] == [
        ("조선·해양", 10.2268),
        ("토건", 0.4232),
    ]
    millions = segment_service.to_segments(
        extraction([("조선·해양", "102,268")], unit="(단위: 백만원)"), REPORT, 0.1
    )
    assert millions.items[0].revenue == 0.1023


def test_to_segments_tidies_names_and_tiny_items():
    report = REPORT + " | 용 역\n | 10\n"
    segments = segment_service.to_segments(
        extraction([("조선·해양", "102,268"), ("용 역", "4,232"), ("아주 작은 것", "10")]),
        report,
        10.65,
    )
    assert [s.name for s in segments.items] == ["조선·해양", "용역"]


def test_to_segments_drops_numbers_not_in_report():
    segments = segment_service.to_segments(
        extraction([("조선·해양", "102,268"), ("지어낸 부문", "9,999")]), REPORT, 10.65
    )
    assert [s.name for s in segments.items] == ["조선·해양"]


@pytest.mark.parametrize(
    "bad",
    [
        extraction([], found=False),
        extraction([("조선·해양", "102,268")], unit="달러"),
        extraction([("지어낸 부문", "9,999")]),
        # 단위를 잘못 읽으면 합계가 연간 매출과 크게 달라서 버린다
        extraction([("조선·해양", "102,268")], unit="백만원"),
    ],
)
def test_to_segments_rejects_untrusted(bad):
    with pytest.raises(SegmentsUnavailable):
        segment_service.to_segments(bad, REPORT, 10.65)


class FakeSegmentWriter:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def write(self, excerpt):
        self.calls += 1
        return self.result


@pytest.fixture
def hynix(monkeypatch):
    """재무 시드에 사업별 매출이 없는 회사 (삼성전자 재무에서 사업별 매출만 뺀 것)."""
    samsung = data_store.find_financials("005930")
    # 2025년 매출을 표 합계(10.65조 원)에 맞춘다
    annual = [
        a.model_copy(update={"revenue": 10.65}) if a.fiscal_year == 2025 else a
        for a in samsung.annual
    ]
    financials = samsung.model_copy(
        update={"stock_code": "000660", "segments": None, "annual": annual}
    )
    monkeypatch.setattr(
        data_store, "find_financials", lambda code: financials if code == "000660" else None
    )
    monkeypatch.setattr(
        dart, "corp_index", lambda: {"000660": DartCorp("00164779", "SK하이닉스", "SK hynix")}
    )
    monkeypatch.setattr(
        dart,
        "latest_annual_report",
        lambda corp_code: AnnualReport("20260312001037", "사업보고서 (2025.12)", "20260312"),
    )
    monkeypatch.setattr(dart, "business_section", lambda rcept_no: REPORT)
    return financials


def use_writer(client, writer):
    client.app.dependency_overrides[segment_service.get_segment_writer] = lambda: writer


def test_financials_fill_segments_from_report(client, hynix):
    writer = FakeSegmentWriter(extraction([("조선·해양", "102,268"), ("토건", "4,232")]))
    use_writer(client, writer)

    first = client.get("/api/companies/000660/financials").json()
    # 백그라운드에서 만들고, 다음 요청부터 채워진다
    assert writer.calls == 1
    assert first["segments"]["items"] == []

    body = client.get("/api/companies/000660/financials").json()
    assert body["segments"]["base_label"] == "2025년 연간 기준"
    assert [s["name"] for s in body["segments"]["items"]] == ["조선·해양", "토건"]
    assert writer.calls == 1  # 같은 사업보고서로는 다시 만들지 않는다


def test_financials_without_table_does_not_retry(client, hynix):
    writer = FakeSegmentWriter(extraction([], found=False))
    use_writer(client, writer)
    client.get("/api/companies/000660/financials")
    body = client.get("/api/companies/000660/financials").json()
    assert body["segments"]["items"] == []
    assert writer.calls == 1
    with new_session() as session:
        record = session.get(segment_service.CompanySegmentsRecord, "000660")
        assert record.failure_reason == NOT_FOUND_REASON


def test_seed_segments_are_kept(client):
    writer = FakeSegmentWriter(extraction([("조선·해양", "102,268")]))
    use_writer(client, writer)
    body = client.get("/api/companies/005930/financials").json()
    assert body["segments"]["items"][0]["code"] == "DS"
    assert writer.calls == 0
