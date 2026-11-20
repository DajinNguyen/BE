import pytest

from app.core import rate_limit
from app.core.config import get_settings
from app.services import compare_service
from tests.conftest import login_as


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)


def test_login_start_is_limited_per_ip(client, limited):
    codes = [
        client.get("/api/auth/google/login", follow_redirects=False).status_code for _ in range(21)
    ]
    assert codes[-1] == 429
    assert 429 not in codes[:20]


def test_limit_response_is_friendly(client, limited):
    for _ in range(20):
        client.get("/api/auth/google/login", follow_redirects=False)
    res = client.get("/api/auth/google/login", follow_redirects=False)
    body = res.json()["error"]
    assert (res.status_code, body["code"]) == (429, "RATE_LIMITED")
    assert "다시 시도" in body["message"] and body["retry_after"] >= 1


def test_limit_is_per_user_not_shared(client, limited):
    login_as(client, 1)
    for _ in range(5):
        client.post("/api/companies/005930/report")
    assert client.post("/api/companies/005930/report").status_code == 429
    login_as(client, 2)  # 다른 계정은 영향 없음
    assert client.post("/api/companies/005930/report").status_code != 429


def test_disabled_limit_never_blocks(client):
    for _ in range(40):
        assert client.get("/api/auth/google/login", follow_redirects=False).status_code != 429


def test_forwarded_header_only_trusted_behind_proxy(client, limited, monkeypatch):
    def hit(ip):
        return client.get(
            "/api/auth/google/login", headers={"x-forwarded-for": ip}, follow_redirects=False
        ).status_code

    for _ in range(20):
        hit("1.1.1.1")
    assert hit("2.2.2.2") == 429  # 헤더를 믿지 않으면 같은 주소로 센다 (속일 수 없다)
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    assert hit("3.3.3.3") != 429  # 프록시 뒤에서는 헤더의 주소로 센다


def test_anonymous_ai_budget_caps_new_generations(client, limited, monkeypatch):
    monkeypatch.setattr(get_settings(), "anonymous_ai_daily_limit", 2)
    for _ in range(2):
        rate_limit.anonymous_ai_budget.consume()
    assert rate_limit.anonymous_ai_budget.available() is False
    started = []
    monkeypatch.setattr(compare_service, "get_compare_writer", lambda: object())
    monkeypatch.setattr(compare_service, "start_analysis", lambda *a: started.append(1) or True)
    from app.main import app

    app.dependency_overrides[compare_service.get_compare_writer] = lambda: object()
    try:
        client.get("/api/companies/compare", params={"left": "005930", "right": "000660"})
    finally:
        app.dependency_overrides.pop(compare_service.get_compare_writer, None)
    assert started == []  # 예산을 다 쓰면 새 AI 작성을 시작하지 않는다 (화면은 그대로 나온다)


def test_content_report_is_limited_per_user(client, limited):
    """신고 3건이면 글이 숨겨지므로, 계정 하나가 짧은 시간에 마구 신고하지 못하게 한다."""
    login_as(client, 1)
    body = {"target_type": "post", "target_id": "999999", "reason": "spam"}
    codes = [client.post("/api/reports", json=body).status_code for _ in range(11)]
    assert codes[-1] == 429
    assert 429 not in codes[:10]
