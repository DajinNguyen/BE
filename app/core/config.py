from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # production 이면 시작할 때 필수 설정을 점검하고, 빠졌으면 서버를 켜지 않는다 (check_production 참고)
    environment: str = "development"
    database_url: str = "sqlite:///./dajin.db"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"  # 무료 한도: 하루 약 500회 (flash는 20회)
    dart_api_key: str | None = None
    dart_cache_dir: str = ".cache/dart"
    naver_prices_enabled: bool = True  # 네이버 금융 시세 (비공식, 키 없음)
    yahoo_prices_enabled: bool = True  # 야후 1시간 캔들: 차트 크게 보기 1달·3달 (비공식, 개인 용도)
    # 네이버 검색 API(뉴스). 비워 두면 Google 뉴스 RSS를 쓴다
    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    naver_search_hub: bool = True  # NAVER API HUB 키면 true, 예전 개발자센터 키면 false
    google_news_enabled: bool = True  # Google 뉴스 RSS (키 없음, 비상업적 용도만)
    upload_dir: str = "uploads"  # 커뮤니티 사진을 저장하는 폴더 (/uploads 로 보여준다)

    # Google 로그인
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"
    # 로그인 쿠키(JWT) 서명 키. 비워 두면 서버를 켤 때마다 새로 만들어서 재시작하면 로그아웃돼요.
    jwt_secret: str | None = None
    session_days: int = 7
    # 약관 동의(만 14세 이상 확인 포함)를 마친 사용자만 서비스 기능을 쓰게 한다
    require_consent: bool = True
    terms_version: str = "2026-11-18"  # 약관이 바뀌어 다시 동의를 받을 때 올린다
    # 요청 횟수 제한 (AI 비용·도배 방어)
    rate_limit_enabled: bool = True
    # Nginx 같은 프록시 뒤에서 X-Forwarded-For 로 실제 접속자 주소를 읽는다 (프록시 없이 켜면 주소를 속일 수 있다)
    trust_proxy_headers: bool = False
    # 로그인 없이 시작되는 AI 작성(회사 개요·기업 비교 분석)을 하루에 최대 몇 건까지 새로 만들지
    anonymous_ai_daily_limit: int = 300
    cookie_secure: bool = False  # https로 배포하면 true
    # 프론트 주소 (CORS 허용 + 로그인 후 돌아갈 곳). 첫 번째가 기본값
    frontend_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]


def check_production(settings: Settings) -> tuple[list[str], list[str]]:
    """운영(production)으로 켤 때 빠뜨리기 쉬운 설정을 점검한다. (오류, 경고) 목록을 돌려준다.

    오류가 있으면 서버가 켜지지 않는다. 경고는 로그에만 남긴다.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not settings.jwt_secret or len(settings.jwt_secret) < 32:
        errors.append(
            "JWT_SECRET 을 32자 이상으로 정해 주세요 (없으면 재시작할 때마다 모두 로그아웃돼요)"
        )
    if not settings.cookie_secure:
        errors.append("COOKIE_SECURE=true 로 해 주세요 (https 에서만 로그인 쿠키를 보내요)")
    if not (settings.google_client_id and settings.google_client_secret):
        errors.append("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 이 필요해요 (로그인이 안 돼요)")
    if not settings.google_redirect_uri.startswith("https://"):
        errors.append("GOOGLE_REDIRECT_URI 를 운영 도메인(https)으로 바꿔 주세요")
    if settings.database_url.startswith("sqlite"):
        errors.append("DATABASE_URL 을 PostgreSQL 로 바꿔 주세요 (SQLite 는 운영에 맞지 않아요)")
    for origin in settings.frontend_origins:
        if not origin.startswith("https://") or "localhost" in origin:
            errors.append(f"FRONTEND_ORIGINS 에 운영 주소(https)만 넣어 주세요: {origin}")
    if not (settings.anthropic_api_key or settings.gemini_api_key):
        warnings.append("AI 키가 없어서 리포트가 데모용 목데이터로 나와요")
    if settings.naver_prices_enabled:
        warnings.append(
            "네이버 금융 시세는 비공식 API라 상업 서비스에 쓰면 안 돼요. 정식 시세 소스로 바꿔 주세요"
        )
    if settings.yahoo_prices_enabled:
        warnings.append(
            "야후 파이낸스 1시간 캔들은 비공식 API·개인 용도라 상업 서비스에 쓰면 안 돼요. "
            "YAHOO_PRICES_ENABLED=false 면 1달·3달 차트가 일 캔들로 나와요"
        )
    if settings.google_news_enabled and not settings.naver_client_id:
        warnings.append(
            "Google 뉴스 RSS 는 비상업적 용도만 허용돼요. 뉴스 API 를 정식으로 계약해 주세요"
        )
    return errors, warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
