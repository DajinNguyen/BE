"""Gemini 예비 키: 앞 키가 한도(429)에 걸리면 다음 키로 넘어가요."""

import asyncio

import pytest
from google.genai import errors as genai_errors

from app.core.config import Settings
from app.services import gemini_keys


def _quota() -> genai_errors.ClientError:
    return genai_errors.ClientError(
        429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}}
    )


def _bad_request() -> genai_errors.ClientError:
    return genai_errors.ClientError(
        400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad"}}
    )


class FakeModels:
    def __init__(self, key: str, calls: list[str], fail: dict[str, BaseException]):
        self._key, self._calls, self._fail = key, calls, fail

    def generate_content(self, **kwargs):
        self._calls.append(self._key)
        if self._key in self._fail:
            raise self._fail[self._key]
        return f"ok:{self._key}"


class FakeAioModels(FakeModels):
    async def generate_content(self, **kwargs):  # type: ignore[override]
        return FakeModels.generate_content(self, **kwargs)


class FakeClient:
    calls: list[str] = []
    fail: dict[str, BaseException] = {}

    def __init__(self, api_key: str, http_options=None):
        self.models = FakeModels(api_key, FakeClient.calls, FakeClient.fail)
        self.aio = type("Aio", (), {})()
        self.aio.models = FakeAioModels(api_key, FakeClient.calls, FakeClient.fail)


@pytest.fixture(autouse=True)
def fake_genai(monkeypatch):
    gemini_keys.reset()
    FakeClient.calls = []
    FakeClient.fail = {}
    monkeypatch.setattr(gemini_keys.genai, "Client", FakeClient)
    yield
    gemini_keys.reset()


def _settings(*keys: str) -> Settings:
    names = ["gemini_api_key", "gemini_api_key_2", "gemini_api_key_3", "gemini_api_key_4"]
    return Settings(**dict(zip(names, keys, strict=False)))


def test_uses_first_key_when_it_works():
    client = gemini_keys.client(_settings("a", "b"))
    assert client.models.generate_content(model="m") == "ok:a"
    assert FakeClient.calls == ["a"]


def test_moves_to_backup_key_on_quota_and_skips_it_next_time():
    FakeClient.fail = {"a": _quota()}
    client = gemini_keys.client(_settings("a", "b"))
    assert client.models.generate_content(model="m") == "ok:b"
    # 한도에 걸린 키는 잠시 건너뛰어요 (다른 기능의 클라이언트도 마찬가지)
    other = gemini_keys.client(_settings("a", "b"))
    assert asyncio.run(other.aio.models.generate_content(model="m")) == "ok:b"
    assert FakeClient.calls == ["a", "b", "b"]


def test_raises_quota_error_when_every_key_is_used_up():
    FakeClient.fail = {"a": _quota(), "b": _quota()}
    client = gemini_keys.client(_settings("a", "b"))
    with pytest.raises(genai_errors.ClientError) as e:
        client.models.generate_content(model="m")
    assert gemini_keys.is_quota_error(e.value)


def test_other_errors_do_not_switch_keys():
    FakeClient.fail = {"a": _bad_request()}
    client = gemini_keys.client(_settings("a", "b"))
    with pytest.raises(genai_errors.ClientError):
        client.models.generate_content(model="m")
    assert FakeClient.calls == ["a"]


def test_empty_backup_slots_are_ignored():
    assert _settings("a", "", None, "d").gemini_api_keys == ["a", "d"]
