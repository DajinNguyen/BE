from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dajin.db"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    dart_api_key: str | None = None
    dart_cache_dir: str = ".cache/dart"
    naver_prices_enabled: bool = True  # 네이버 금융 시세 (비공식, 키 없음)

    # Google 로그인
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"
    # 로그인 쿠키(JWT) 서명 키. 비워 두면 서버를 켤 때마다 새로 만들어서 재시작하면 로그아웃돼요.
    jwt_secret: str | None = None
    session_days: int = 7
    cookie_secure: bool = False  # https로 배포하면 true
    # 프론트 주소 (CORS 허용 + 로그인 후 돌아갈 곳). 첫 번째가 기본값
    frontend_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
