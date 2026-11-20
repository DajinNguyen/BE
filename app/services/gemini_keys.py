"""Gemini 예비 키: 앞 키가 사용 한도(429)에 걸리면 다음 키로 넘어간다.

`GEMINI_API_KEY` 를 먼저 쓰고, 한도에 걸리면 `GEMINI_API_KEY_2` … `_4` 순서로 쓴다.
한도에 걸린 키는 잠시(QUOTA_COOLDOWN) 건너뛰었다가 다시 써 본다 (하루 한도는 매일 다시 찬다).
모든 키가 한도에 걸렸으면 마지막 429 오류를 그대로 올려서, 부르는 쪽의 기존 처리를 따른다.

`client(settings, timeout_ms)` 는 `genai.Client` 처럼
`.models.generate_content(...)` / `.aio.models.generate_content(...)` 를 쓸 수 있는 객체를 돌려준다.
한도 기록은 프로세스 안에서 모든 기능(리포트·개요·챗봇 등)이 함께 쓴다.
"""

import logging
import threading
import time
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.config import Settings

logger = logging.getLogger(__name__)

QUOTA_COOLDOWN = 10 * 60  # 한도에 걸린 키를 건너뛰는 시간 (초)

_lock = threading.Lock()
_exhausted_until: dict[str, float] = {}  # 키 → 다시 써 볼 시각


def is_quota_error(error: BaseException) -> bool:
    return isinstance(error, genai_errors.ClientError) and (
        getattr(error, "code", None) == 429
        or getattr(error, "status", None) == "RESOURCE_EXHAUSTED"
    )


def reset() -> None:
    """테스트용: 한도 기록을 지운다."""
    with _lock:
        _exhausted_until.clear()


class _Pool:
    def __init__(self, keys: list[str], timeout_ms: int | None) -> None:
        if not keys:
            raise RuntimeError("Gemini API 키가 없어요.")
        self._keys = keys
        options = genai_types.HttpOptions(timeout=timeout_ms) if timeout_ms else None
        self._clients = [genai.Client(api_key=k, http_options=options) for k in keys]

    def _order(self) -> list[int]:
        """지금 써 볼 키 순서. 모두 한도에 걸렸으면 가장 먼저 풀리는 키 하나만 써 본다."""
        now = time.time()
        with _lock:
            ready = [i for i, k in enumerate(self._keys) if _exhausted_until.get(k, 0) <= now]
            if ready:
                return ready
            return [min(range(len(self._keys)), key=lambda i: _exhausted_until[self._keys[i]])]

    def _mark(self, index: int) -> None:
        with _lock:
            _exhausted_until[self._keys[index]] = time.time() + QUOTA_COOLDOWN
        if index + 1 < len(self._keys):
            logger.warning("Gemini 키 %d번이 사용 한도에 걸렸어요. 다음 키로 넘어가요.", index + 1)
        else:
            logger.warning("Gemini 키 %d번이 사용 한도에 걸렸어요.", index + 1)

    def generate(self, **kwargs: Any) -> Any:
        error: BaseException | None = None
        for i in self._order():
            try:
                return self._clients[i].models.generate_content(**kwargs)
            except genai_errors.ClientError as e:
                if not is_quota_error(e):
                    raise
                self._mark(i)
                error = e
        assert error is not None
        raise error

    async def agenerate(self, **kwargs: Any) -> Any:
        error: BaseException | None = None
        for i in self._order():
            try:
                return await self._clients[i].aio.models.generate_content(**kwargs)
            except genai_errors.ClientError as e:
                if not is_quota_error(e):
                    raise
                self._mark(i)
                error = e
        assert error is not None
        raise error


class _Models:
    def __init__(self, pool: _Pool, use_async: bool) -> None:
        self._pool = pool
        self._async = use_async

    def generate_content(self, **kwargs: Any) -> Any:
        return self._pool.agenerate(**kwargs) if self._async else self._pool.generate(**kwargs)


class _Aio:
    def __init__(self, pool: _Pool) -> None:
        self.models = _Models(pool, use_async=True)


class FallbackClient:
    """genai.Client 대신 쓰는 객체 (generate_content 만)."""

    def __init__(self, keys: list[str], timeout_ms: int | None = None) -> None:
        pool = _Pool(keys, timeout_ms)
        self.models = _Models(pool, use_async=False)
        self.aio = _Aio(pool)


def client(settings: Settings, timeout_ms: int | None = None) -> FallbackClient:
    return FallbackClient(settings.gemini_api_keys, timeout_ms)
