import asyncio

import pytest
from google.genai import errors as genai_errors

from app.core.config import Settings
from app.services import gemini_keys
from app.services.gemini_keys import GeminiKeyPool


def _quota_error():
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}}
    return genai_errors.ClientError(429, body)


class FakeClient:
    def __init__(self, key):
        self.key = key


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setattr(
        gemini_keys.genai, "Client", lambda api_key, http_options: FakeClient(api_key)
    )
    return GeminiKeyPool(["key-1", "key-2"], timeout_ms=1000)


def test_quota_moves_to_next_key_and_skips_exhausted(pool):
    used = []

    async def request(client):
        used.append(client.key)
        if client.key == "key-1":
            raise _quota_error()
        return "ok"

    assert asyncio.run(pool.call(request)) == "ok"
    assert asyncio.run(pool.call(request)) == "ok"
    assert used == ["key-1", "key-2", "key-2"]  # 한도에 걸린 키는 잠시 건너뛴다


def test_all_keys_exhausted_raises_quota_error(pool):
    async def request(client):
        raise _quota_error()

    with pytest.raises(genai_errors.ClientError) as e:
        asyncio.run(pool.call(request))
    assert gemini_keys.is_quota_error(e.value)


def test_other_errors_are_not_retried_on_next_key(pool):
    used = []

    async def request(client):
        used.append(client.key)
        raise genai_errors.ClientError(400, {"error": {"code": 400, "message": "bad"}})

    with pytest.raises(genai_errors.ClientError):
        asyncio.run(pool.call(request))
    assert used == ["key-1"]


def test_settings_collects_keys():
    settings = Settings(gemini_api_key="a", gemini_api_key_2="", gemini_api_key_3="c")
    assert settings.gemini_api_keys == ["a", "c"]
