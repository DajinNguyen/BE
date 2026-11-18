from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import new_session
from app.models.performance import CompanyQuarterSegmentsRecord
from app.services import dart, quarter_segment_service
from app.services.quarter_segment_service import (
    NOT_FOUND_REASON,
    QuarterSegmentDraft,
    QuarterSegmentExtraction,
    SegmentsUnavailable,
)
from tests.test_performance import TRILLION, use_dart  # noqa: F401 - 픽스처

# 삼성전자 분기 보고서 "부문별 보고" 주석을 줄인 것 (단위: 백만원). 그 분기 회사 매출은 150조 원.
NOTE = """27. 부문별 보고 (연결)
 | 영업부문에 대한 공시
 | 당분기
 | (단위 : 백만원)
 | DX 부문 | DS 부문 | 내부거래 조정 등 | 부문 합계
 | 3개월 | 누적 | 3개월 | 누적 | 3개월 | 누적 | 3개월 | 누적
 | 매출액 | 52,054,489 | 152,731,608 | 107,104,100 | 316,335,814 | (9,158,589) | (20,000,000) | 150,000,000 | 449,067,422
 | 영업이익 | (825,216) | 1,323,661 | 56,536,477 | 199,395,770
 | 전분기
 | 매출액 | 48,375,095 | 143,662,466 | 33,116,786 | 86,122,879
"""
BUSINESS = "DS 부문은 AI 서버용 메모리 판매가 늘었어요."
SOURCE = quarter_segment_service.source_text(NOTE, BUSINESS)
TOTALS = {
    "revenue": 150 * TRILLION,
    "operating_income": 60 * TRILLION,
    "prior_revenue": 75 * TRILLION,
    "company_name": "삼성전자",
}


def _extraction(**changes):
    data = {
        "kind": "segments",
        "unit": "백만원",
        "items": [
            QuarterSegmentDraft(
                name="DX(가전·스마트폰)",
                revenue="52,054,489",
                prior_revenue="48,375,095",
                operating_income="(825,216)",
                comment="",
            ),
            QuarterSegmentDraft(
                name="DS(반도체)",
                revenue="107,104,100",
                prior_revenue="33,116,786",
                operating_income="56,536,477",
                comment="AI 서버용 메모리 판매가 늘었어요",
            ),
        ],
    }
    return QuarterSegmentExtraction(**(data | changes))


def test_segments_are_read_from_the_note_in_won():
    items = quarter_segment_service.to_items(_extraction(), SOURCE, **TOTALS)
    assert items == [
        {
            "name": "DS(반도체)",
            "revenue": 107_104_100_000_000,
            "operating_income": 56_536_477_000_000,
            "prior_revenue": 33_116_786_000_000,
            "comment": "AI 서버용 메모리 판매가 늘었어요",
        },
        {
            "name": "DX(가전·스마트폰)",
            "revenue": 52_054_489_000_000,
            "operating_income": -825_216_000_000,  # 괄호는 적자
            "prior_revenue": 48_375_095_000_000,
            "comment": "",
        },
    ]


def test_numbers_not_in_the_report_are_dropped():
    extraction = _extraction()
    extraction.items[1].revenue = "107,104,101"  # 원문에 없는 숫자
    extraction.items[0].prior_revenue = "1,000"  # 없는 숫자면 그 칸만 비운다
    with pytest.raises(SegmentsUnavailable):  # 남은 부문(52조)만으로는 분기 매출(150조)과 안 맞는다
        quarter_segment_service.to_items(extraction, SOURCE, **TOTALS)


def test_cumulative_values_are_rejected_by_the_sum_check():
    cumulative = _extraction(
        items=[
            QuarterSegmentDraft(name="DX", revenue="152,731,608"),
            QuarterSegmentDraft(name="DS", revenue="316,335,814"),
        ]
    )
    with pytest.raises(SegmentsUnavailable, match=NOT_FOUND_REASON):
        quarter_segment_service.to_items(cumulative, SOURCE, **TOTALS)  # 합계가 분기 매출의 3배


def test_single_segment_company_shows_the_whole_company():
    single = QuarterSegmentExtraction(
        kind="single", single_name="반도체", single_comment="메모리 가격이 올랐어요"
    )
    assert quarter_segment_service.to_items(single, SOURCE, **TOTALS) == [
        {
            "name": "반도체",
            "revenue": 150 * TRILLION,
            "operating_income": 60 * TRILLION,
            "prior_revenue": 75 * TRILLION,
            "comment": "메모리 가격이 올랐어요",
        }
    ]


@pytest.mark.parametrize(
    "comment",
    ["앞으로 매출이 늘 전망이에요", "매출이 30% 늘었어요", "지금 매수할 때예요", "가" * 41],
)
def test_comment_with_forecast_numbers_or_advice_is_dropped(comment):
    extraction = _extraction()
    extraction.items[1].comment = comment
    items = quarter_segment_service.to_items(extraction, SOURCE, **TOTALS)
    assert items[0]["comment"] == ""


def test_nothing_found():
    with pytest.raises(SegmentsUnavailable):
        quarter_segment_service.to_items(QuarterSegmentExtraction(kind="none"), SOURCE, **TOTALS)


class FakeWriter:
    def __init__(self, extraction):
        self.extraction = extraction
        self.sources = []

    def write(self, source):
        self.sources.append(source)
        return self.extraction


def test_performance_shows_segments_after_generation(client, use_dart, monkeypatch):  # noqa: F811
    use_dart()
    monkeypatch.setattr(dart, "quarter_report", lambda rcept_no: (NOTE, BUSINESS))
    writer = FakeWriter(_extraction())
    client.app.dependency_overrides[quarter_segment_service.get_quarter_segment_writer] = (
        lambda: writer
    )

    first = client.get("/api/companies/005930/performance").json()
    assert first["segments"] == []  # 백그라운드에서 만들고 다음 요청부터 채워진다
    assert "[영업부문 주석]" in writer.sources[0]

    body = client.get("/api/companies/005930/performance").json()
    assert body["unit"] == "조 원"
    assert body["segments"] == [
        {
            "name": "DS(반도체)",
            "revenue": 107.1,
            "operating_income": 56.54,
            "yoy_rate": 223.4,
            "comment": "AI 서버용 메모리 판매가 늘었어요",
        },
        {
            "name": "DX(가전·스마트폰)",
            "revenue": 52.05,
            "operating_income": -0.83,
            "yoy_rate": 7.6,
            "comment": "",
        },
    ]
    assert len(writer.sources) == 1  # 같은 분기 보고서로는 다시 만들지 않는다


def test_not_found_is_not_retried_for_the_same_report(client, use_dart, monkeypatch):  # noqa: F811
    use_dart()
    monkeypatch.setattr(dart, "quarter_report", lambda rcept_no: ("", BUSINESS))  # 주석이 없다
    writer = FakeWriter(QuarterSegmentExtraction(kind="none"))
    client.app.dependency_overrides[quarter_segment_service.get_quarter_segment_writer] = (
        lambda: writer
    )
    client.get("/api/companies/005930/performance")
    client.get("/api/companies/005930/performance")
    # 주석이 없어도 사업의 내용으로 한 번 판단해 보고, 못 찾으면 같은 보고서로는 다시 하지 않는다
    assert len(writer.sources) == 1
    assert "(영업부문 주석이 없어요)" in writer.sources[0]
    with new_session() as session:
        record = session.get(CompanyQuarterSegmentsRecord, "005930")
        assert (record.status, record.failure_reason) == ("failed", NOT_FOUND_REASON)
        later = datetime.now(UTC) + timedelta(days=1)
        start = quarter_segment_service.start_generation
        assert not start(session, "005930", record.rcept_no, 2, later)
        assert start(session, "005930", "새보고서", 3, later)  # 새 분기 보고서가 나오면 다시


def test_fourth_quarter_is_not_generated(client):
    with new_session() as session:
        assert not quarter_segment_service.start_generation(session, "005930", "r", 4)
        assert not quarter_segment_service.start_generation(session, "005930", None, 2)


def test_cumulative_only_table_subtracts_the_previous_report():
    """3개월 열이 없는 회사(현대차 등): 3분기 누적 − 반기 누적 = 3분기 3개월."""
    q3 = " | 차량 | 금융 | 매출액 | 300,000 | 60,000 | 전분기 | 매출액 | 240,000 | 45,000"
    h1 = " | 차량 | 금융 | 매출액 | 200,000 | 40,000 | 전반기 | 매출액 | 160,000 | 30,000"

    def drafts(values):
        return QuarterSegmentExtraction(
            kind="segments",
            unit="억원",
            items=[
                QuarterSegmentDraft(
                    name=name,
                    table_name=name,
                    revenue_cumulative=now,
                    prior_revenue_cumulative=before,
                )
                for name, now, before in values
            ],
        )

    current = drafts([("차량", "300,000", "240,000"), ("금융", "60,000", "45,000")])
    previous = drafts([("차량", "200,000", "160,000"), ("금융", "40,000", "30,000")])
    assert quarter_segment_service.needs_previous(current, 3)
    assert not quarter_segment_service.needs_previous(current, 1)  # 1분기는 누적 = 3개월
    hundred_million = 100_000_000
    items = quarter_segment_service.to_items(
        current,
        q3,
        revenue=120_000 * hundred_million,
        operating_income=0,
        prior_revenue=None,
        company_name="현대차",
        quarter=3,
        previous=previous,
        previous_source=h1,
    )
    assert [(i["name"], i["revenue"], i["prior_revenue"]) for i in items] == [
        ("차량", 100_000 * hundred_million, 80_000 * hundred_million),
        ("금융", 20_000 * hundred_million, 15_000 * hundred_million),
    ]
    # 앞 분기 보고서가 없으면 누적만으로는 3개월을 알 수 없어서 버린다
    with pytest.raises(SegmentsUnavailable):
        quarter_segment_service.to_items(
            current, q3, revenue=120_000 * hundred_million, operating_income=0,
            prior_revenue=None, company_name="현대차", quarter=3,
        )  # fmt: skip


def test_cumulative_values_in_three_month_fields_are_read_again_as_cumulative():
    """SK텔레콤처럼 AI가 3개월 칸에 누적을 옮기면, 합계가 분기 매출의 몇 배라서 알아채고 다시 읽는다."""
    q3 = " | 무선 | 유선 | 매출액 | 9,000 | 3,000 | 전년동기 | 매출액 | 8,700 | 2,700"
    h1 = " | 무선 | 유선 | 매출액 | 6,000 | 2,000 | 전년동기 | 매출액 | 5,800 | 1,800"

    def drafts(values):
        return QuarterSegmentExtraction(
            kind="segments",
            unit="억원",
            items=[
                QuarterSegmentDraft(name=name, table_name=name, revenue=now, prior_revenue=before)
                for name, now, before in values
            ],
        )

    current = drafts([("무선", "9,000", "8,700"), ("유선", "3,000", "2,700")])
    previous = drafts([("무선", "6,000", "5,800"), ("유선", "2,000", "1,800")])
    hundred_million = 100_000_000
    totals = {
        "revenue": 4_000 * hundred_million,  # 3분기 3개월 매출
        "operating_income": 0,
        "prior_revenue": None,
        "company_name": "SK텔레콤",
        "quarter": 3,
    }
    assert quarter_segment_service.suspect_cumulative(current, q3, totals["revenue"], 3)
    assert not quarter_segment_service.suspect_cumulative(current, q3, totals["revenue"], 1)

    items = quarter_segment_service.to_items(
        current, q3, previous=previous, previous_source=h1, **totals
    )
    assert [(i["name"], i["revenue"], i["prior_revenue"]) for i in items] == [
        ("무선", 3_000 * hundred_million, 2_900 * hundred_million),
        ("유선", 1_000 * hundred_million, 900 * hundred_million),
    ]
    # 앞 분기 보고서가 없으면 고칠 수 없으니 그대로 버린다
    with pytest.raises(SegmentsUnavailable):
        quarter_segment_service.to_items(current, q3, **totals)


def test_suspected_cumulative_reads_the_previous_report(
    client, use_dart, monkeypatch
):  # noqa: F811
    """3개월 칸 합계가 분기 매출의 3배면 앞 분기 보고서도 읽는다 (AI 호출 2번)."""
    cumulative = _extraction(
        items=[
            QuarterSegmentDraft(name="DX", revenue="152,731,608"),
            QuarterSegmentDraft(name="DS", revenue="316,335,814"),
        ]
    )
    reads = []

    def fake_source(rcept_no):
        reads.append(rcept_no)
        return SOURCE

    monkeypatch.setattr(quarter_segment_service, "_read_source", fake_source)
    writer = FakeWriter(cumulative)
    status = quarter_segment_service.run_generation(
        "005930",
        writer,
        new_session,
        company_name="삼성전자",
        rcept_no="q3",
        revenue=150 * TRILLION,
        operating_income=60 * TRILLION,
        prior_revenue=None,
        quarter=3,
        previous_rcept_no="h1",
    )
    assert reads == ["q3", "h1"]
    # 같은 표를 앞 분기라고 돌려줬으니 빼면 0 → 그래도 믿을 수 없어서 failed
    assert status == "failed"
