"""네이버 금융 시세 연동 (비공식 API).

- 현재가(polling.finance.naver.com): 현재가·등락률·거래대금·시가총액·거래 시각. 여러 종목을 한 번에 받고 1분 캐시한다.
- 일별·주별 종가(api.stock.naver.com): 주가 흐름 그래프용. 종목·기간별로 1시간 캐시한다.

공식 API가 아니라서 예고 없이 바뀌거나 막힐 수 있다. 실패하면 None/빈 dict를 돌려주고,
호출하는 쪽은 시드 예시 값(is_sample: true)으로 동작한다.
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

logger = logging.getLogger(__name__)

QUOTE_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock"
CHART_URL = "https://api.stock.naver.com/chart/domestic/item"
HEADERS = {"User-Agent": "Mozilla/5.0"}
QUOTE_TTL = 60  # 1분
CHART_TTL = 60 * 60  # 1시간
TIMEOUT = 3.0  # 화면이 기다리지 않도록 짧게
RETRY_AFTER = 60  # 받기에 실패하면 이 시간 동안은 다시 시도하지 않는다
BATCH_SIZE = 50  # 한 번에 묻는 종목 수


@dataclass(frozen=True)
class Quote:
    stock_code: str
    price: int  # 원
    change_rate: float  # 전일 대비 (%)
    trading_value: int  # 오늘 누적 거래대금 (원)
    market_cap: int  # 시가총액 (원)
    traded_at: datetime  # 마지막 체결 시각 (KST)


def _parse_quote(item: dict[str, Any]) -> Quote:
    return Quote(
        stock_code=item["itemCode"],
        price=int(item["closePriceRaw"]),
        change_rate=float(item["fluctuationsRatioRaw"]),
        trading_value=int(item["accumulatedTradingValueRaw"]),
        market_cap=int(item["marketValueFullRaw"]),
        traded_at=datetime.fromisoformat(item["localTradedAt"]),
    )


class NaverClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: dict[str, tuple[float, Quote]] = {}
        self._charts: dict[tuple[str, str, date], tuple[float, list[tuple[date, int]]]] = {}
        self._retry_at = 0.0

    def _get(self, url: str, params: dict[str, str] | None = None) -> Any:
        res = httpx.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        res.raise_for_status()
        return res.json()

    def _failed(self, what: str, error: Exception) -> None:
        logger.warning("네이버 %s를 받지 못했어요: %s", what, error)
        self._retry_at = time.time() + RETRY_AFTER

    # ------------------------------------------------------------------ 현재가

    def quotes(self, stock_codes: list[str]) -> dict[str, Quote]:
        """종목코드 → 현재가. 받지 못한 종목은 빠진다 (실패하면 마지막으로 받은 값)."""
        now = time.time()
        stale = [c for c in stock_codes if now - self._quotes.get(c, (0.0, None))[0] > QUOTE_TTL]
        if stale and now >= self._retry_at:
            with self._lock:
                self._fetch_quotes(stale)
        return {c: self._quotes[c][1] for c in stock_codes if c in self._quotes}

    def _fetch_quotes(self, stock_codes: list[str]) -> None:
        for i in range(0, len(stock_codes), BATCH_SIZE):
            batch = stock_codes[i : i + BATCH_SIZE]
            try:
                items = self._get(f"{QUOTE_URL}/{','.join(batch)}")["datas"]
                quotes = [_parse_quote(item) for item in items]
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                self._failed("현재가", e)
                return
            fetched_at = time.time()
            for q in quotes:
                self._quotes[q.stock_code] = (fetched_at, q)

    # ------------------------------------------------------------------ 일별·주별 종가

    def closes(
        self, stock_code: str, start: date, end: date, weekly: bool
    ) -> list[tuple[date, int]] | None:
        """(날짜, 종가) 목록. 받지 못하면 마지막으로 받은 값, 그것도 없으면 None."""
        timeframe = "week" if weekly else "day"
        key = (stock_code, timeframe, start)
        hit = self._charts.get(key)
        if hit and time.time() - hit[0] < CHART_TTL:
            return hit[1]
        if time.time() < self._retry_at:
            return hit[1] if hit else None
        try:
            rows = self._get(
                f"{CHART_URL}/{stock_code}/{timeframe}",
                {"startDateTime": f"{start:%Y%m%d}0000", "endDateTime": f"{end:%Y%m%d}2359"},
            )
            points = [
                (datetime.strptime(r["localDate"], "%Y%m%d").date(), round(r["closePrice"]))
                for r in rows
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            self._failed("주가 흐름", e)
            return hit[1] if hit else None
        if not points:
            return hit[1] if hit else None
        self._charts[key] = (time.time(), points)
        return points


@lru_cache
def get_naver() -> NaverClient | None:
    return NaverClient() if get_settings().naver_prices_enabled else None


def quotes(stock_codes: list[str]) -> dict[str, Quote]:
    client = get_naver()
    return client.quotes(stock_codes) if client else {}


def closes(stock_code: str, start: date, end: date, weekly: bool) -> list[tuple[date, int]] | None:
    client = get_naver()
    return client.closes(stock_code, start, end, weekly) if client else None
