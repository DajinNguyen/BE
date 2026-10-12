from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dajin.db"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    dart_api_key: str | None = None
    dart_cache_dir: str = ".cache/dart"


@lru_cache
def get_settings() -> Settings:
    return Settings()
