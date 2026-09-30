def test_get_report_before_generation(client):
    res = client.get("/api/companies/005930/report")
    assert res.status_code == 404
    assert res.json()["code"] == "REPORT_NOT_FOUND"


def test_report_not_ready_for_unsupported_company(client):
    assert client.get("/api/companies/000660/report").json()["code"] == "REPORT_NOT_READY"
    assert client.post("/api/companies/000660/report").json()["code"] == "REPORT_NOT_READY"


def test_report_unknown_company(client):
    assert client.post("/api/companies/999999/report").json()["code"] == "COMPANY_NOT_FOUND"


def test_generate_then_get_report(client, fake_writer):
    res = client.post("/api/companies/005930/report")
    assert res.status_code == 200
    created = res.json()
    assert created["is_cached"] is False
    assert created["stock_code"] == "005930"
    assert created["generated_at"].endswith("+09:00")
    assert created["analyzed_sources"] == ["재무제표", "뉴스", "사업보고서"]
    assert len(created["disclaimers"]) == 2
    # 정답 번호가 보기 범위를 벗어난 퀴즈는 걸러진다
    assert [q["id"] for q in created["quiz"]] == ["q1"]

    context = fake_writer.calls[0]
    assert context["derived"]["operating_margin_percent"]["2025"] == 13.1
    assert all(not n["is_placeholder"] for n in context["news"])

    stored = client.get("/api/companies/005930/report").json()
    assert stored["is_cached"] is True
    assert stored["summary"] == created["summary"]


def test_post_overwrites_existing_report(client):
    client.post("/api/companies/005930/report")
    client.post("/api/companies/005930/report")
    assert client.get("/api/companies/005930/report").status_code == 200


def test_claude_writer_without_credentials_returns_error_format(client, monkeypatch):
    from app.api.deps import get_report_writer
    from app.core.config import Settings
    from app.main import app
    from app.services.report_service import ClaudeReportWriter

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    writer = ClaudeReportWriter(Settings(anthropic_api_key=None))
    app.dependency_overrides[get_report_writer] = lambda: writer

    res = client.post("/api/companies/005930/report")
    assert res.status_code == 502
    assert res.json()["code"] == "REPORT_GENERATION_FAILED"
