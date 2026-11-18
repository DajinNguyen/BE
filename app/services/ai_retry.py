"""백그라운드 AI 호출을 혼잡·한도 오류일 때 조금 기다렸다가 다시 시도한다.

Gemini 무료 등급은 "503 high demand"·"429" 가 자주 난다. 한 번 실패하면 같은 회사는 30분 동안
다시 만들지 않아서, 그동안 "사업별 성과"·"왜 이렇게 됐나요?" 가 비어 보였다.
"""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRY_ERRORS = (
    "503",
    "429",
    "UNAVAILABLE",
    "RESOURCE_EXHAUSTED",
    "overloaded",
    "rate_limit",
)
RETRY_DELAYS_SECONDS = (5, 15, 30)  # 백그라운드 작업이라 화면을 기다리게 하지 않는다


def retryable(error: Exception) -> bool:
    return any(word in str(error) for word in RETRY_ERRORS)


def call(fn: Callable[..., T], *args: object, sleep: Callable[[float], None] = time.sleep) -> T:
    """fn(*args). 혼잡·한도 오류면 `RETRY_DELAYS_SECONDS` 만큼 기다렸다가 다시 부른다."""
    for delay in RETRY_DELAYS_SECONDS:
        try:
            return fn(*args)
        except Exception as e:
            if not retryable(e):
                raise
            logger.warning("AI가 혼잡해서 %s초 뒤에 다시 시도해요: %s", delay, str(e)[:80])
            sleep(delay)
    return fn(*args)
