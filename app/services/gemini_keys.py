"""Gemini API 키 여러 개를 차례로 쓴다.

`GEMINI_API_KEY` 를 먼저 쓰고, 사용 한도(429)에 걸리면 `GEMINI_API_KEY_2` … 순서로 넘어간다.
한도에 걸린 키는 잠시(QUOTA_COOLDOWN) 건너뛰었다가 다시 써 본다 (하루 한도는 매일 다시 찬다).
모든 키가 한도에 걸렸으면 마지막 429 오류를 그대로 올려서, 호출하는 쪽의 기존 처리를 따른다.
"""

import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

QUOTA_COOLDOWN = 10 * 60  # 한도에 걸린 키를 건너뛰는 시간 (초)

T = TypeVar("T")


def is_quota_error(error: Exception) -> bool:
    return isinstance(error, genai_errors.ClientError) and (
        getattr(error, "code", None) == 429
        or getattr(error, "status", None) == "RESOURCE_EXHAUSTED"
    )


class GeminiKeyPool:
    def __init__(self, keys: list[str], timeout_ms: int) -> None:
        self._keys = keys
        self._timeout_ms = timeout_ms
        self._clients: dict[int, genai.Client] = {}
        self._exhausted_until: dict[int, float] = {}
        self._last_quota_error: genai_errors.ClientError | None = None
        self._lock = threading.Lock()

    def _client(self, index: int) -> genai.Client:
        with self._lock:
            if index not in self._clients:
                self._clients[index] = genai.Client(
                    api_key=self._keys[index],
                    http_options=genai_types.HttpOptions(timeout=self._timeout_ms),
                )
            return self._clients[index]

    async def call(self, request: Callable[[genai.Client], Awaitable[T]]) -> T:
        """쓸 수 있는 키로 차례로 요청한다. 429면 다음 키, 다른 오류는 그대로 올린다."""
        now = time.time()
        for index in range(len(self._keys)):
            if self._exhausted_until.get(index, 0) > now:
                continue
            try:
                return await request(self._client(index))
            except genai_errors.ClientError as e:
                if not is_quota_error(e):
                    raise
                logger.warning(
                    "Gemini 키 %d번이 사용 한도에 걸렸어요. 다음 키로 넘어가요.", index + 1
                )
                self._exhausted_until[index] = time.time() + QUOTA_COOLDOWN
                self._last_quota_error = e
        if self._last_quota_error is None:  # 키가 없을 때만 (설정 확인용)
            raise RuntimeError("Gemini API 키가 없어요.")
        raise self._last_quota_error
