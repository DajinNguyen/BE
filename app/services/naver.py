"""네이버 금융 시세 연동 (비공식 API).

- 현재가(polling.finance.naver.com): 현재가·등락률·거래대금·시가총액·거래 시각. 여러 종목을 한 번에 받고 1분 캐시한다.
- 분·시간·일·주·월·년 캔들(api.stock.naver.com): 주가 흐름 그래프용. 종목·기간별로 1분 캐시한다.

공식 API가 아니라서 예고 없이 바뀌거나 막힐 수 있다. 실패하면 None/빈 dict를 돌려주고,
호출하는 쪽은 시드 예시 값(is_sample: true)으로 동작한다.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Literal

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

QUOTE_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock"
CHART_URL = "https://api.stock.naver.com/chart/domestic/item"
HEADERS = {"User-Agent": "Mozilla/5.0"}
QUOTE_TTL = 20  # 초 (화면의 현재가와 그래프를 같은 값으로 맞추려고 짧게 둔다)
CHART_TTL = 20  # 초 (오늘 캔들이 장중에 계속 바뀐다)
TIMEOUT = 3.0  # 화면이 기다리지 않도록 짧게
RETRY_AFTER = 60  # 받기에 실패하면 이 시간 동안은 다시 시도하지 않는다
BATCH_SIZE = 50  # 한 번에 묻는 종목 수
QUOTE_WORKERS = 8  # 묶음을 동시에 받는 수


Timeframe = Literal["minute", "minute60", "day", "week", "month", "year"]


@dataclass(frozen=True)
class Candle:
    date: date
    time: str | None  # 분 캔들만 "HH:MM"
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class Quote:
    stock_code: str
    price: int  # 원
    change_rate: float  # 전일 대비 (%)
    trading_value: int  # 오늘 누적 거래대금 (원)
    market_cap: int  # 시가총액 (원)
    traded_at: datetime  # 마지막 체결 시각 (KST)
    previous_close: int | None = None  # 전일 종가 (원) = 현재가 − 전일 대비 변동. 모르면 None


def _int_or_zero(raw: Any) -> int:
    """장 시작 전(PREOPEN)·거래 정지 종목은 거래대금 등이 빈 문자열로 온다."""
    return int(raw) if raw not in (None, "") else 0


def _previous_close(price: int, item: dict[str, Any]) -> int | None:
    """네이버는 전일 종가를 직접 주지 않고 '전일 대비 변동(원)'만 줘서 거꾸로 계산한다."""
    raw = item.get("compareToPreviousClosePriceRaw")
    try:
        return price - int(raw)
    except (TypeError, ValueError):
        return None


def _parse_quote(item: dict[str, Any]) -> Quote:
    price = int(item["closePriceRaw"])  # 현재가가 없으면 쓸 수 없어서 그 종목만 뺀다
    return Quote(
        stock_code=item["itemCode"],
        price=price,
        change_rate=float(item["fluctuationsRatioRaw"] or 0),
        trading_value=_int_or_zero(item.get("accumulatedTradingValueRaw")),
        market_cap=_int_or_zero(item.get("marketValueFullRaw")),
        traded_at=datetime.fromisoformat(item["localTradedAt"]),
        previous_close=_previous_close(price, item),
    )


def _parse_quotes(items: list[dict[str, Any]]) -> list[Quote]:
    """한 종목이 이상해도 같은 묶음(50종목)의 다른 종목은 살린다."""
    quotes = []
    for item in items:
        try:
            quotes.append(_parse_quote(item))
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(
                "네이버 현재가를 읽지 못한 종목을 건너뛰어요 %s: %s", item.get("itemCode"), e
            )
    return quotes


class NaverClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: dict[str, tuple[float, Quote]] = {}
        self._charts: dict[tuple[str, str, date], tuple[float, list[Candle]]] = {}
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

    def _fetch_batch(self, batch: list[str]) -> list[Quote] | Exception:
        try:
            items = self._get(f"{QUOTE_URL}/{','.join(batch)}")["datas"]
            return _parse_quotes(items)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            return e

    def _fetch_quotes(self, stock_codes: list[str]) -> None:
        batches = [stock_codes[i : i + BATCH_SIZE] for i in range(0, len(stock_codes), BATCH_SIZE)]
        if len(batches) == 1:
            results = [self._fetch_batch(batches[0])]
        else:  # 메인 화면은 전체 회사(2,000곳) 시세가 필요해서 묶음을 동시에 받는다
            with ThreadPoolExecutor(max_workers=QUOTE_WORKERS) as pool:
                results = list(pool.map(self._fetch_batch, batches))
        fetched_at = time.time()
        for result in results:
            if isinstance(result, Exception):
                self._failed("현재가", result)
                continue
            for q in result:
                self._quotes[q.stock_code] = (fetched_at, q)

    # ------------------------------------------------------------------ 캔들 (분·일·주·월·년)

    def candles(
        self, stock_code: str, timeframe: Timeframe, start: date, end: date
    ) -> list[Candle] | None:
        """오래된 순 캔들 목록. 받지 못하면 마지막으로 받은 값, 그것도 없으면 None."""
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
            points = [_parse_candle(r) for r in rows]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            self._failed("주가 흐름", e)
            return hit[1] if hit else None
        points = [p for p in points if _plausible(p)]
        if not points:
            return hit[1] if hit else None
        self._charts[key] = (time.time(), points)
        return points


def _parse_candle(row: dict[str, Any]) -> Candle:
    """분 캔들은 localDateTime·currentPrice, 나머지는 localDate·closePrice 로 온다."""
    if "localDateTime" in row:
        at = datetime.strptime(row["localDateTime"], "%Y%m%d%H%M%S")
        day, clock, close = at.date(), f"{at:%H:%M}", row["currentPrice"]
    else:
        day = datetime.strptime(row["localDate"], "%Y%m%d").date()
        clock, close = None, row["closePrice"]
    return Candle(
        date=day,
        time=clock,
        open=round(row["openPrice"]),
        high=round(row["highPrice"]),
        low=round(row["lowPrice"]),
        close=round(close),
        volume=int(row.get("accumulatedTradingVolume") or 0),
    )


# 한 캔들 안에서 고가가 저가의 이만큼 배를 넘으면 잘못된 값으로 본다
# (예: 삼성전자 1990년 연봉이 시가 44,000원·종가 319원으로 온다)
IMPLAUSIBLE_RANGE = 20


def _plausible(c: Candle) -> bool:
    return c.low > 0 and c.high <= c.low * IMPLAUSIBLE_RANGE


@lru_cache
def get_naver() -> NaverClient | None:
    return NaverClient() if get_settings().naver_prices_enabled else None


def quotes(stock_codes: list[str]) -> dict[str, Quote]:
    client = get_naver()
    return client.quotes(stock_codes) if client else {}


def candles(stock_code: str, timeframe: Timeframe, start: date, end: date) -> list[Candle] | None:
    client = get_naver()
    return client.candles(stock_code, timeframe, start, end) if client else None
