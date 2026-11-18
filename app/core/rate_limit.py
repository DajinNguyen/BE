"""요청 횟수 제한 (AI 비용·도배 방어).

서버 한 대(프로세스 하나) 안의 메모리로 센다. 서버를 여러 대로 늘리면 Redis 같은 공용 저장소로 바꿔야 한다.
"""

import threading
import time
from collections import defaultdict, deque
from datetime import date

from fastapi import Depends, Request

from app.api.deps import optional_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.models.user import User


class _Windows:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window: float) -> float | None:
        """요청 하나를 센다. 한도를 넘었으면 다시 시도할 수 있기까지 남은 초, 괜찮으면 None."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                return window - (now - hits[0])
            hits.append(now)
            if len(self._hits) > 50_000:  # 오래 안 온 키가 쌓이지 않게 가끔 정리
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= window]:
                    del self._hits[k]
            return None

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


windows = _Windows()


def client_ip(request: Request) -> str:
    """요청한 사람의 주소. 프록시(Nginx) 뒤에서만 X-Forwarded-For 를 믿는다 (아니면 속일 수 있다)."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(retry_after: float) -> AppError:
    seconds = max(1, round(retry_after))
    return AppError(
        429, "RATE_LIMITED", f"요청이 너무 많아요. {seconds}초 뒤에 다시 시도해 주세요.",
        {"retry_after": seconds},
    )  # fmt: skip


def rate_limit(name: str, limit: int, window: float = 60.0):
    """라우터에 `dependencies=[Depends(rate_limit("report", 5))]` 로 건다.

    로그인한 사람은 계정별로, 아니면 주소(IP)별로 센다.
    """

    def check(request: Request, user: User | None = Depends(optional_user)) -> None:
        if not get_settings().rate_limit_enabled:
            return
        who = f"u{user.id}" if user else f"ip:{client_ip(request)}"
        wait = windows.hit(f"{name}:{who}", limit, window)
        if wait is not None:
            raise rate_limited(wait)

    return check


class _DailyBudget:
    """하루에 쓸 수 있는 AI 작성 횟수 (로그인 없이 누구나 시작할 수 있는 작성용). 날짜가 바뀌면 0부터."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day = date.min
        self._used = 0

    def _roll(self) -> None:
        today = date.today()
        if today != self._day:
            self._day, self._used = today, 0

    def available(self) -> bool:
        if not get_settings().rate_limit_enabled:
            return True
        with self._lock:
            self._roll()
            return self._used < get_settings().anonymous_ai_daily_limit

    def consume(self) -> None:
        with self._lock:
            self._roll()
            self._used += 1

    def reset(self) -> None:
        with self._lock:
            self._day, self._used = date.min, 0


anonymous_ai_budget = _DailyBudget()
