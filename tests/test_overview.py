import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.api.deps import get_overview_writer
from app.core.db import new_session
from app.main import app
from app.models.overview import CompanyOverviewRecord
from app.services import dart, overview_service
from app.services.overview_service import (
    OverviewContent,
    OverviewQuotaExceeded,
    OverviewUnavailable,
)

CONTEXT = {
    "company": {"name": "SK하이닉스", "sector": "반도체"},
    "annual_financials": {
        "unit": "조 원",
        "basis": "연결 기준",
        "years": [
            {"fiscal_year": 2023, "revenue": 32.77, "operating_income": -7.73},
            {"fiscal_year": 2024, "revenue": 66.19, "operating_income": 23.47},
            {"fiscal_year": 2025, "revenue": 97.15, "operating_income": 47.21},
        ],
    },
    "business_report": {"title": "SK하이닉스 2025년 사업보고서", "text": "사업의 내용"},
}
SOURCE = "SK하이닉스 2025년 사업보고서 (2026년 3월 공시)"


def _item(year, is_plan=False):
    return {"year": year, "title": f"{year} 제목", "description": "설명이에요.", "is_plan": is_plan}


class FakeOverviewWriter:
    def __init__(self, fail=False, quota=False):
        self.calls = 0
        self.fail = fail
        self.quota = quota

    async def write(self, context):
        self.calls += 1
        if self.quota:
            raise OverviewQuotaExceeded()
        if self.fail:
            raise OverviewUnavailable("회사 소개를 만드는 중에 문제가 생겼어요.")
        return OverviewContent(
            business_description="SK하이닉스는 메모리 반도체를 만드는 회사예요.",
            business_segments=[
                {"code": "dram", "name": "DRAM", "description": "기억하는 반도체예요."},
                {"code": "DRAM", "name": "HBM", "description": "AI용 메모리예요."},
                {"code": "", "name": "", "description": "이름 없는 건 빠져요."},
            ],
            timeline=[
                _item("2025년"),
                _item("2023"),
                _item("2019"),  # 재무 자료에 없는 연도는 빠진다
                _item("2024"),
                _item("2026", is_plan=True),
                _item("2027", is_plan=True),  # 계획은 1개만
            ],
        )


@pytest.fixture
def overview(client, monkeypatch):
    writer = FakeOverviewWriter()
    app.dependency_overrides[get_overview_writer] = lambda: writer
    monkeypatch.setattr(
        overview_service, "load_source", lambda code: (CONTEXT, SOURCE, "20260317000635")
    )
    return writer


def test_business_section_text():
    document = (
        "<TITLE>I. 회사의 개요</TITLE><P>개요</P>"
        "<TITLE ATOC='Y'>II. 사업의 내용</TITLE><P>메모리를 만들어요&amp;팔아요</P>"
        "<TABLE><TR><TD>DRAM</TD><TD>60%</TD></TR></TABLE>"
        "<TITLE>III. 재무에 관한 사항</TITLE><P>재무</P>"
    )
    text = dart.business_section_text(document)
    assert text.startswith("II. 사업의 내용")
    assert "메모리를 만들어요&팔아요" in text and "DRAM" in text and "60%" in text
    assert "재무" not in text and "개요" not in text
    assert dart.business_section_text("<TITLE>다른 문서</TITLE>") == ""


def test_overview_generated_in_background_then_filled(client, overview):
    first = client.get("/api/companies/000660").json()
    assert first["business_segments"] == [] and first["timeline"] == []  # 아직 만드는 중
    assert overview.calls == 1  # 응답 뒤 백그라운드에서 만들었다

    body = client.get("/api/companies/000660").json()
    assert overview.calls == 1  # 이미 있으면 다시 만들지 않는다
    # 시드에 한 줄 설명이 있으면 시드가 먼저
    assert body["business_description"] == first["business_description"]
    assert [s["code"] for s in body["business_segments"]] == ["DRAM", "DRAM_"]
    assert [(t["year"], t["is_plan"]) for t in body["timeline"]] == [
        ("2023", False),
        ("2024", False),
        ("2025", False),
        ("2026", True),
    ]
    assert body["timeline_source"] == SOURCE


def test_new_annual_report_regenerates_but_keeps_old_meanwhile(client, overview, monkeypatch):
    client.get("/api/companies/000660")
    assert overview.calls == 1

    newer = dart.AnnualReport(
        rcept_no="20270316000111", report_nm="사업보고서", rcept_dt="20270316"
    )
    monkeypatch.setattr(dart, "latest_annual_report", lambda corp_code: newer)
    overview.fail = True  # 새로 만들다 실패해도
    body = client.get("/api/companies/000660").json()
    assert overview.calls == 2  # 새 보고서가 나와서 다시 만들었다
    assert body["business_segments"] and body["timeline_source"] == SOURCE  # 예전 개요는 보인다
    assert client.get("/api/companies/000660").json()["business_segments"]


def test_same_annual_report_is_not_regenerated(client, overview, monkeypatch):
    client.get("/api/companies/000660")
    same = dart.AnnualReport(rcept_no="20260317000635", report_nm="사업보고서", rcept_dt="20260317")
    monkeypatch.setattr(dart, "latest_annual_report", lambda corp_code: same)
    client.get("/api/companies/000660")
    assert overview.calls == 1


def test_company_with_seed_overview_is_not_generated(client, overview):
    body = client.get("/api/companies/005930").json()
    assert body["timeline_source"].startswith("삼성전자")
    assert overview.calls == 0


def test_company_without_corp_code_is_not_generated(client, overview):
    client.get("/api/companies/207940")  # 시드에 DART 고유번호가 없다 (테스트는 DART를 안 부른다)
    assert overview.calls == 0


def test_no_ai_key_does_not_generate(client, monkeypatch):
    app.dependency_overrides[get_overview_writer] = lambda: None
    called = []
    monkeypatch.setattr(overview_service, "load_source", lambda code: called.append(code))
    assert client.get("/api/companies/000660").status_code == 200
    assert called == []


def test_failure_waits_before_retry(client, monkeypatch):
    writer = FakeOverviewWriter(fail=True)
    app.dependency_overrides[get_overview_writer] = lambda: writer
    monkeypatch.setattr(
        overview_service, "load_source", lambda code: (CONTEXT, SOURCE, "20260317000635")
    )
    assert client.get("/api/companies/000660").status_code == 200
    client.get("/api/companies/000660")
    assert writer.calls == 1  # 방금 실패했으면 바로 다시 부르지 않는다
    with new_session() as session:
        record = session.get(CompanyOverviewRecord, "000660")
        assert record.status == "failed" and record.failure_reason
        record.updated_at = datetime.now(UTC) - timedelta(hours=2)
        session.add(record)
        session.commit()
    client.get("/api/companies/000660")
    assert writer.calls == 2  # 한 시간이 지나면 다시 시도한다


def test_missing_report_is_saved_as_failure(client, monkeypatch):
    writer = FakeOverviewWriter()
    app.dependency_overrides[get_overview_writer] = lambda: writer

    def no_source(code):
        raise OverviewUnavailable(overview_service.NO_SOURCE_REASON)

    monkeypatch.setattr(overview_service, "load_source", no_source)
    assert client.get("/api/companies/000660").status_code == 200
    assert writer.calls == 0
    with new_session() as session:
        record = session.get(CompanyOverviewRecord, "000660")
        assert record.failure_reason == overview_service.NO_SOURCE_REASON


def test_source_label():
    report = dart.AnnualReport("20260317000635", "사업보고서 (2025.12)", "20260317")
    assert overview_service._source_label("SK하이닉스", report) == SOURCE


def test_run_generation_reports_quota(client, monkeypatch):
    monkeypatch.setattr(
        overview_service, "load_source", lambda code: (CONTEXT, SOURCE, "20260317000635")
    )
    result = asyncio.run(
        overview_service.run_generation("000660", FakeOverviewWriter(quota=True), new_session)
    )
    assert result == "quota"
    with new_session() as session:
        record = session.get(CompanyOverviewRecord, "000660")
        assert record.status == "failed"
        assert record.failure_reason == overview_service.QUOTA_REASON
    ok = asyncio.run(overview_service.run_generation("000660", FakeOverviewWriter(), new_session))
    assert ok == "done"
