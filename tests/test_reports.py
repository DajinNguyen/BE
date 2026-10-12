from datetime import UTC, datetime, timedelta

from app.core.db import new_session
from app.models.report import ReportRecord

KEY_POINT_ORDER = ["performance", "revenue_source", "recent_change", "risk", "strength"]


def test_report_before_generation_is_none(client):
    res = client.get("/api/companies/005930/report")
    assert res.status_code == 200
    assert res.json() == {
        "stock_code": "005930",
        "status": "none",
        "generated_at": None,
        "headline": "",
        "key_points": [],
        "evidence": {"financials": None, "news": [], "vision": None},
        "quiz": [],
        "sources": [],
        "failure_reason": None,
    }


def test_report_unknown_company(client):
    assert client.get("/api/companies/999999/report").json()["code"] == "COMPANY_NOT_FOUND"
    assert client.post("/api/companies/999999/report").json()["code"] == "COMPANY_NOT_FOUND"


def test_post_returns_generating_then_get_returns_done(client, fake_writer):
    res = client.post("/api/companies/005930/report")
    assert res.status_code == 200
    assert res.json()["status"] == "generating"
    assert res.json()["key_points"] == []

    report = client.get("/api/companies/005930/report").json()
    assert report["status"] == "done"
    assert report["failure_reason"] is None
    assert report["generated_at"].endswith("+09:00")
    assert report["headline"] == "3년 동안 이익이 크게 늘어난 회사예요"

    # 핵심 포인트는 분류별 1개씩 고정 순서, 근거 종류는 중복 없이
    assert [p["category"] for p in report["key_points"]] == KEY_POINT_ORDER
    assert report["key_points"][0]["evidence"] == ["financials"]
    assert report["key_points"][0]["after"] is None

    # 정답 번호가 보기 범위를 벗어난 퀴즈는 걸러진다
    assert [q["question"] for q in report["quiz"]] == ["2025년 매출은?"]

    context = fake_writer.calls[0]
    assert context["company"]["stock_code"] == "005930"
    assert context["derived"]["operating_margin_percent"]["2025"] == 13.1
    assert context["available_evidence"] == ["financials", "news", "vision"]
    assert all(not n["is_placeholder"] for n in context["news"])


def test_evidence_numbers_come_from_data(client):
    client.post("/api/companies/005930/report")
    report = client.get("/api/companies/005930/report").json()
    evidence = report["evidence"]

    financials = evidence["financials"]
    assert financials["years"] == ["2023", "2024", "2025"]
    assert financials["revenue"] == [258.94, 300.9, 333.6]
    assert financials["operating_income"] == [6.57, 32.7, 43.6]
    assert financials["base_date"] == "2025년 연간 · 연결 기준"
    assert [(h["label"], h["value"], h["is_sample"]) for h in financials["highlights"]] == [
        ("매출", "333.6조 원", False),
        ("영업이익", "43.6조 원", False),
        ("영업이익률", "13.1%", False),
        ("부채비율", "27.1%", True),
    ]

    # 원문 링크가 없는 자리표시용 뉴스는 빠진다
    assert len(evidence["news"]) == 2
    assert all(n["url"] for n in evidence["news"])
    assert evidence["news"][0]["press"] == "삼성전자 뉴스룸"

    vision = evidence["vision"]
    assert vision["summary"] == "스마트폰부터 반도체까지 만드는 회사예요."
    assert [m["name"] for m in vision["revenue_mix"]][0] == "DS(반도체)"
    assert vision["revenue_mix"][0] == {
        "name": "DS(반도체)",
        "amount": 44.0,
        "unit": "조 원",
        "period": "2025년 4분기 기준",
    }

    assert report["sources"] == [
        "금융감독원 DART",
        "한국거래소",
        "삼성전자 공식 실적 발표 (2026년 1월)",
    ]


def test_company_without_financials_fails_immediately(client, fake_writer):
    res = client.post("/api/companies/000660/report").json()
    assert res["status"] == "failed"
    assert res["failure_reason"] == "이 기업의 재무제표를 아직 불러오지 못했어요."
    assert fake_writer.calls == []
    assert client.get("/api/companies/000660/report").json()["status"] == "failed"


def test_regenerate_failure_keeps_previous_content(client, fake_writer):
    client.post("/api/companies/005930/report")
    first = client.get("/api/companies/005930/report").json()

    async def broken(context):
        raise RuntimeError("boom")

    fake_writer.write = broken
    client.post("/api/companies/005930/report")
    report = client.get("/api/companies/005930/report").json()
    assert report["status"] == "failed"
    assert report["failure_reason"]
    assert report["headline"] == first["headline"]
    assert report["key_points"] == first["key_points"]


def test_post_while_generating_does_not_start_again(client, fake_writer):
    with new_session() as session:
        session.add(
            ReportRecord(stock_code="005930", status="generating", started_at=datetime.now(UTC))
        )
        session.commit()
    res = client.post("/api/companies/005930/report").json()
    assert res["status"] == "generating"
    assert fake_writer.calls == []


def test_stuck_generation_turns_into_failure(client):
    with new_session() as session:
        started = datetime.now(UTC) - timedelta(minutes=10)
        session.add(ReportRecord(stock_code="005930", status="generating", started_at=started))
        session.commit()
    report = client.get("/api/companies/005930/report").json()
    assert report["status"] == "failed"
    assert report["failure_reason"]


def test_claude_writer_without_credentials_saves_failure(client, monkeypatch):
    from app.api.deps import get_report_writer
    from app.core.config import Settings
    from app.main import app
    from app.services.report_service import ClaudeReportWriter

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    writer = ClaudeReportWriter(Settings(anthropic_api_key=None))
    app.dependency_overrides[get_report_writer] = lambda: writer

    assert client.post("/api/companies/005930/report").json()["status"] == "generating"
    report = client.get("/api/companies/005930/report").json()
    assert report["status"] == "failed"
    assert (
        report["failure_reason"]
        == "리포트를 만드는 중에 문제가 생겼어요. 잠시 후 다시 시도해 주세요."
    )


def test_fixture_writer_matches_frontend_sample(client):
    from app.api.deps import get_report_writer
    from app.main import app
    from app.services.report_service import FixtureReportWriter

    app.dependency_overrides[get_report_writer] = lambda: FixtureReportWriter()
    client.post("/api/companies/005930/report")
    report = client.get("/api/companies/005930/report").json()
    assert report["status"] == "done"
    assert [p["category"] for p in report["key_points"]] == KEY_POINT_ORDER
    assert len(report["quiz"]) == 3
