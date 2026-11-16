"""네이버 금융 코스피·코스닥 지수 연동 (비공식 API).

- 현재 지수(polling.finance.naver.com): 지수·등락폭·등락률·거래 시각. 1분 캐시한다.
- 당일 흐름(api.stock.naver.com): 10분 간격 지수. 1분 캐시한다.

시세(`naver.py`)와 같이 공식 API가 아니라서 바뀌거나 막힐 수 있다. 실패하면 None/빈 dict를 돌려주고,
호출하는 쪽은 예시 값(is_sample: true)으로 동작한다.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.naver import HEADERS, RETRY_AFTER, TIMEOUT

logger = logging.getLogger(__name__)

INDEX_URL = "https://polling.finance.naver.com/api/realtime/domestic/index"
INTRADAY_URL = "https://api.stock.naver.com/chart/domestic/index"
INDEX_TTL = 60  # 1분
INDEX_CODES = ("KOSPI", "KOSDAQ")


@dataclass(frozen=True)
class IndexQuote:
    code: str  # KOSPI / KOSDAQ
    value: float
    change: float  # 전일 대비 등락폭
    change_rate: float  # 전일 대비 등락률 (%)
    is_open: bool  # 장중이면 true
    traded_at: datetime  # 마지막 거래 시각 (KST)


def _parse_index(item: dict[str, Any]) -> IndexQuote:
    return IndexQuote(
        code=item["itemCode"],
        value=float(item["closePriceRaw"]),
        change=float(item["compareToPreviousClosePriceRaw"]),
        change_rate=float(item["fluctuationsRatioRaw"]),
        is_open=item.get("marketStatus") == "OPEN",
        traded_at=datetime.fromisoformat(item["localTradedAt"]),
    )


class NaverIndexClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: tuple[float, dict[str, IndexQuote]] | None = None
        self._intraday: dict[tuple[str, date], tuple[float, list[tuple[str, float]]]] = {}
        self._retry_at = 0.0

    def _get(self, url: str, params: dict[str, str] | None = None) -> Any:
        res = httpx.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        res.raise_for_status()
        return res.json()

    def _failed(self, what: str, error: Exception) -> None:
        logger.warning("네이버 %s를 받지 못했어요: %s", what, error)
        self._retry_at = time.time() + RETRY_AFTER

    def quotes(self) -> dict[str, IndexQuote]:
        """지수 코드 → 현재 지수. 받지 못하면 마지막으로 받은 값, 그것도 없으면 빈 dict."""
        with self._lock:
            hit = self._quotes
            if (hit and time.time() - hit[0] < INDEX_TTL) or time.time() < self._retry_at:
                return hit[1] if hit else {}
            try:
                items = self._get(f"{INDEX_URL}/{','.join(INDEX_CODES)}")["datas"]
                quotes = {q.code: q for q in map(_parse_index, items)}
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                self._failed("지수", e)
                return hit[1] if hit else {}
            self._quotes = (time.time(), quotes)
            return quotes

    def intraday(self, code: str, day: date) -> list[tuple[str, float]] | None:
        """그날 10분 간격 지수 [(HH:mm, 지수)]. 받지 못하면 마지막으로 받은 값, 그것도 없으면 None."""
        key = (code, day)
        hit = self._intraday.get(key)
        if (hit and time.time() - hit[0] < INDEX_TTL) or time.time() < self._retry_at:
            return hit[1] if hit else None
        try:
            rows = self._get(
                f"{INTRADAY_URL}/{code}/minute10",
                {"startDateTime": f"{day:%Y%m%d}0900", "endDateTime": f"{day:%Y%m%d}1530"},
            )
            points = [
                (
                    f"{r['localDateTime'][8:10]}:{r['localDateTime'][10:12]}",
                    float(r["currentPrice"]),
                )
                for r in rows
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            self._failed("지수 흐름", e)
            return hit[1] if hit else None
        if not points:
            return hit[1] if hit else None
        self._intraday[key] = (time.time(), points)
        return points


@lru_cache
def get_naver_index() -> NaverIndexClient | None:
    return NaverIndexClient() if get_settings().naver_prices_enabled else None


def quotes() -> dict[str, IndexQuote]:
    client = get_naver_index()
    return client.quotes() if client else {}


def intraday(code: str, day: date) -> list[tuple[str, float]] | None:
    client = get_naver_index()
    return client.intraday(code, day) if client else None
