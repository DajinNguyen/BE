"""야후 파이낸스 1시간 캔들 (비공식 API).

네이버는 분·시간 캔들을 최근 약 7거래일치만 줘서, 차트 크게 보기의 1달·3달(1시간·반나절 캔들)은
그보다 앞 구간을 야후에서 채운다. 야후는 1시간 캔들을 최근 약 2년치까지 준다.

공식 API가 아니고 약관상 개인 용도라 예고 없이 막힐 수 있다. 실패하면 None 을 돌려주고,
호출하는 쪽은 일 캔들로 대신 보여 준다.
"""

import logging
import time
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

import httpx

from app.core.config import get_settings
from app.services.market_clock import KST
from app.services.naver import Candle

logger = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 4.0
HOURLY_TTL = 600  # 10분 (최근 며칠은 네이버 시간 캔들로 덮어써서 오래 둬도 된다)
RETRY_AFTER = 300  # 받기에 실패하면 이 시간 동안은 다시 시도하지 않는다

# 코스피 종목은 005930.KS, 코스닥 종목은 247540.KQ
SUFFIX = {"KOSPI": "KS", "KOSDAQ": "KQ"}


class YahooClient:
    def __init__(self) -> None:
        self._hourly: dict[tuple[str, date], tuple[float, list[Candle]]] = {}
        self._retry_at = 0.0

    def hourly(
        self, stock_code: str, market: Literal["KOSPI", "KOSDAQ"], start: date, end: date
    ) -> list[Candle] | None:
        """오래된 순 1시간 캔들 (시각은 한국 시간 "HH:MM"). 받지 못하면 마지막으로 받은 값 또는 None."""
        key = (stock_code, start)
        hit = self._hourly.get(key)
        if hit and time.time() - hit[0] < HOURLY_TTL:
            return hit[1]
        if time.time() < self._retry_at:
            return hit[1] if hit else None
        begin = datetime.combine(start, datetime.min.time(), KST)
        until = datetime.combine(end + timedelta(days=1), datetime.min.time(), KST)
        try:
            res = httpx.get(
                f"{CHART_URL}/{stock_code}.{SUFFIX[market]}",
                params={
                    "interval": "60m",
                    "period1": str(int(begin.timestamp())),
                    "period2": str(int(until.timestamp())),
                },
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            res.raise_for_status()
            candles = _parse_chart(res.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as e:
            logger.warning("야후 1시간 캔들을 받지 못했어요: %s", e)
            self._retry_at = time.time() + RETRY_AFTER
            return hit[1] if hit else None
        if not candles:
            return hit[1] if hit else None
        self._hourly[key] = (time.time(), candles)
        return candles


def _parse_chart(body: dict[str, Any]) -> list[Candle]:
    result = body["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    candles = []
    for i, ts in enumerate(result.get("timestamp") or []):
        o, h, low, c = (quote[k][i] for k in ("open", "high", "low", "close"))
        if None in (o, h, low, c) or low <= 0:
            continue  # 거래가 없던 칸
        at = datetime.fromtimestamp(ts, KST)
        candles.append(
            Candle(
                date=at.date(),
                time=f"{at:%H:%M}",
                open=round(o),
                high=round(h),
                low=round(low),
                close=round(c),
                volume=int(quote["volume"][i] or 0),
            )
        )
    return candles


@lru_cache
def get_yahoo() -> YahooClient | None:
    return YahooClient() if get_settings().yahoo_prices_enabled else None


def hourly(
    stock_code: str, market: Literal["KOSPI", "KOSDAQ"], start: date, end: date
) -> list[Candle] | None:
    client = get_yahoo()
    return client.hourly(stock_code, market, start, end) if client else None
