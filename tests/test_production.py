import pytest

from app.core.config import Settings, check_production

GOOD = dict(
    environment="production",
    jwt_secret="x" * 32,
    cookie_secure=True,
    google_client_id="id",
    google_client_secret="secret",
    google_redirect_uri="https://api.dajin.example/api/auth/google/callback",
    database_url="postgresql+psycopg://u:p@db/dajin",
    frontend_origins=["https://dajin.example"],
    anthropic_api_key="key",
    naver_prices_enabled=False,
    google_news_enabled=False,
    _env_file=None,
)


def check(**overrides):
    return check_production(Settings(**{**GOOD, **overrides}))


def test_good_production_config_has_no_errors():
    errors, warnings = check()
    assert errors == [] and warnings == []


@pytest.mark.parametrize(
    "override",
    [
        {"jwt_secret": None},
        {"jwt_secret": "short"},
        {"cookie_secure": False},
        {"google_client_id": None},
        {"google_redirect_uri": "http://localhost:8000/api/auth/google/callback"},
        {"database_url": "sqlite:///./dajin.db"},
        {"frontend_origins": ["http://localhost:5173"]},
        {"frontend_origins": ["https://dajin.example", "http://dajin.example"]},
    ],
)
def test_each_missing_setting_is_an_error(override):
    errors, _ = check(**override)
    assert len(errors) == 1


def test_unofficial_data_sources_are_warned():
    _, warnings = check(naver_prices_enabled=True, anthropic_api_key=None)
    assert len(warnings) >= 2


def test_production_refuses_to_start_when_misconfigured(monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    monkeypatch.setattr(get_settings(), "environment", "production")
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        with TestClient(app):
            pass


def test_health_ok(client):
    res = client.get("/api/health")
    assert (res.status_code, res.json()) == (200, {"status": "ok"})


def test_disabled_rate_limit_is_warned():
    _, warnings = check(rate_limit_enabled=False)
    assert any("RATE_LIMIT_ENABLED" in w for w in warnings)
