from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dajin.db"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    gemini_api_key: str | None = None
    # 첫 번째 키가 사용 한도(429)에 걸리면 차례로 쓰는 예비 키
    gemini_api_key_2: str | None = None
    gemini_api_key_3: str | None = None
    gemini_api_key_4: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    dart_api_key: str | None = None
    dart_cache_dir: str = ".cache/dart"
    naver_prices_enabled: bool = True  # 네이버 금융 시세 (비공식, 키 없음)
    # 네이버 검색 API(뉴스). 비워 두면 Google 뉴스 RSS를 쓴다
    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    naver_search_hub: bool = True  # NAVER API HUB 키면 true, 예전 개발자센터 키면 false
    google_news_enabled: bool = True  # Google 뉴스 RSS (키 없음, 비상업적 용도만)

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

    @property
    def gemini_api_keys(self) -> list[str]:
        keys = [
            self.gemini_api_key,
            self.gemini_api_key_2,
            self.gemini_api_key_3,
            self.gemini_api_key_4,
        ]
        return [k for k in keys if k]


@lru_cache
def get_settings() -> Settings:
    return Settings()
