"""주가 흐름. 네이버 시세를 받지 못하면 결정적인 예시 데이터를 만든다.

1시간·반나절 캔들(차트 크게 보기의 1달·3달)은 최근 며칠은 네이버, 그 앞은 야후에서 받는다.
야후를 받지 못하면 일 캔들로 대신 보낸다 (응답의 interval 이 "day").
"""

import random
from datetime import date, datetime, timedelta

from app.core.errors import invalid_param
from app.schemas.company import CompanyDetail
from app.schemas.price import Interval, Period, PriceHistory, PricePoint
from app.services import naver, yahoo
from app.services.data_store import get_company
from app.services.market_clock import KST, last_close_date

# 기간별로 거슬러 올라가는 날 수. 1d·5d는 연휴를 넘겨 받은 뒤 마지막 거래일만 남긴다.
PERIOD_DAYS: dict[str, int] = {
    "1d": 10,
    "5d": 14,
    "1m": 30,
    "3m": 91,
    "6m": 182,
    "1y": 365,
    "5y": 365 * 5,
    "all": 365 * 50,
}
TRADING_DAYS = {"1d": 1, "5d": 5}

# interval 을 주지 않으면 이 길이의 캔들로 보낸다 (예전 응답과 같게: 1y 는 주봉)
DEFAULT_INTERVAL: dict[str, Interval] = {
    "1d": "1min",
    "5d": "10min",
    "1m": "day",
    "3m": "day",
    "6m": "day",
    "1y": "week",
    "5y": "week",
    "all": "month",
}
MINUTE_BUCKET = {"1min": 1, "10min": 10}
# 1시간·반나절 캔들은 이 기간에만 (야후가 1시간 캔들을 약 2년치만 준다)
HOUR_INTERVALS = {"60min", "halfday"}
HOUR_PERIODS = {"1m", "3m", "6m", "1y"}
# 네이버 시간 캔들은 최근 약 7거래일치라, 이만큼 거슬러 물어본다
NAVER_HOURLY_DAYS = 14
# 정규장 마지막 1시간 캔들 (15:00~15:30 동시호가)
LAST_HOUR = "15:00"
TIMEFRAME: dict[str, naver.Timeframe] = {
    "1min": "minute",
    "10min": "minute",
    "day": "day",
    "week": "week",
    "month": "month",
    "year": "year",
}


def _check(period: Period, interval: Interval) -> None:
    """분 캔들은 1일·5일에만, 1일·5일은 분 캔들로만, 시간 캔들은 1달~1년에만
    (그 밖은 캔들이 너무 많거나 적다)."""
    minute = interval in MINUTE_BUCKET
    hour = interval in HOUR_INTERVALS
    if minute != (period in TRADING_DAYS) or (hour and period not in HOUR_PERIODS):
        raise invalid_param(f"period={period} 에는 interval={interval} 을 쓸 수 없어요.")


def _merge(group: list[naver.Candle], day: date, start: str) -> naver.Candle:
    return naver.Candle(
        date=day,
        time=start,
        open=group[0].open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        volume=sum(c.volume for c in group),
    )


def _bucket(candles: list[naver.Candle], minutes: int) -> list[naver.Candle]:
    """1분 캔들을 minutes 분짜리로 묶는다 (같은 날 안에서만)."""
    if minutes == 1:
        return candles
    grouped: dict[tuple[date, str], list[naver.Candle]] = {}
    for c in candles:
        hour, minute = c.time.split(":")  # type: ignore[union-attr]
        start = f"{hour}:{int(minute) // minutes * minutes:02d}"
        grouped.setdefault((c.date, start), []).append(c)
    return [_merge(group, day, start) for (day, start), group in grouped.items()]


def _half_days(candles: list[naver.Candle]) -> list[naver.Candle]:
    """1시간 캔들을 오전(09:00~12:00)·오후(12:00~장 마감) 반나절로 묶는다."""
    grouped: dict[tuple[date, str], list[naver.Candle]] = {}
    for c in candles:
        start = "09:00" if (c.time or "00:00") < "12:00" else "12:00"
        grouped.setdefault((c.date, start), []).append(c)
    return [_merge(group, day, start) for (day, start), group in grouped.items()]


def _hourly(
    company: CompanyDetail, period: Period, interval: Interval, end: date
) -> list[naver.Candle] | None:
    """야후 1시간 캔들에 최근 며칠은 네이버 시간 캔들(더 빨리 바뀐다)을 덮어쓴다.
    야후를 받지 못하면 None (네이버만으로는 며칠치뿐이라 일 캔들로 대신한다)."""
    start = end - timedelta(days=PERIOD_DAYS[period])
    older = yahoo.hourly(company.stock_code, company.market, start, end)
    if not older:
        return None
    recent = naver.candles(
        company.stock_code, "minute60", end - timedelta(days=NAVER_HOURLY_DAYS), end
    )
    daily = naver.candles(company.stock_code, "day", start, end) or []
    candles = _with_closing(older, daily)
    if recent:
        regular = [
            c for c in recent if (c.time or "") <= LAST_HOUR
        ]  # 장 마감 뒤(넥스트레이드)는 빼요
        if regular:
            candles = [c for c in candles if c.date < regular[0].date] + regular
    return _half_days(candles) if interval == "halfday" else candles


def _with_closing(hourly: list[naver.Candle], daily: list[naver.Candle]) -> list[naver.Candle]:
    """야후 1시간 캔들에는 장 마감 동시호가(15:00~15:30) 칸이 없어서, 네이버 일 캔들의
    종가·거래량으로 그 칸을 만들어 그날 마지막 값이 실제 종가와 맞게 한다."""
    by_day: dict[date, list[naver.Candle]] = {}
    for c in hourly:
        by_day.setdefault(c.date, []).append(c)
    result: list[naver.Candle] = []
    days = {c.date: c for c in daily}
    for day, group in by_day.items():
        result.extend(group)
        close = days.get(day)
        if close is None or any(c.time == LAST_HOUR for c in group):
            continue
        before = group[-1].close
        result.append(
            naver.Candle(
                date=day,
                time=LAST_HOUR,
                open=before,
                high=max(before, close.close),
                low=min(before, close.close),
                close=close.close,
                volume=max(0, close.volume - sum(c.volume for c in group)),
            )
        )
    return result


def _points(candles: list[naver.Candle]) -> list[PricePoint]:
    return [
        PricePoint(
            date=c.date,
            time=c.time,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
        )
        for c in candles
    ]


def _live(stock_code: str, period: Period, interval: Interval, end: date) -> list[PricePoint]:
    start = end - timedelta(days=PERIOD_DAYS[period])
    candles = naver.candles(stock_code, TIMEFRAME[interval], start, end)
    if not candles:
        return []
    if period in TRADING_DAYS:
        days = sorted({c.date for c in candles})[-TRADING_DAYS[period] :]
        candles = _bucket([c for c in candles if c.date in days], MINUTE_BUCKET[interval])
    return _points(candles)


def get_price_history(
    stock_code: str,
    period: Period,
    interval: Interval | None = None,
    today: date | None = None,
) -> PriceHistory:
    company = get_company(stock_code)
    interval = interval or DEFAULT_INTERVAL[period]
    _check(period, interval)

    end = today or datetime.now(KST).date()
    if interval in HOUR_INTERVALS:
        hourly = _hourly(company, period, interval, end)
        if hourly:
            return PriceHistory(
                stock_code=stock_code,
                period=period,
                interval=interval,
                is_sample=False,
                points=_points(hourly),
            )
        interval = "day"  # 야후를 받지 못하면 일 캔들로

    live = _live(stock_code, period, interval, end)
    if live:
        return PriceHistory(
            stock_code=stock_code, period=period, interval=interval, is_sample=False, points=live
        )
    return PriceHistory(
        stock_code=stock_code,
        period=period,
        interval=interval,
        is_sample=True,
        points=_sample(company.current_price.value, stock_code, period, interval, today),
    )


# ---------------------------------------------------------------------- 예시 데이터

SAMPLE_VOLATILITY = {"day": 0.015, "week": 0.032, "month": 0.07, "year": 0.2}
SAMPLE_ALL_YEARS = 10  # "전체" 예시는 10년치


def _sample_dates(end: date, period: Period, interval: Interval) -> list[date]:
    days = PERIOD_DAYS[period] if period != "all" else 365 * SAMPLE_ALL_YEARS
    start = end - timedelta(days=days)
    if interval == "day":
        result, current = [], start
        while current <= end:
            if current.weekday() < 5:
                result.append(current)
            current += timedelta(days=1)
        return result
    if interval == "week":
        return [end - timedelta(weeks=w) for w in range(days // 7, -1, -1)]
    if interval == "month":
        months = days // 30
        return [_months_before(end, m) for m in range(months, -1, -1)]
    return [date(year, 1, 1) for year in range(start.year, end.year + 1)]


def _months_before(end: date, months: int) -> date:
    total = end.year * 12 + end.month - 1 - months
    return date(total // 12, total % 12 + 1, 1)


def _sample(
    current: int, stock_code: str, period: Period, interval: Interval, today: date | None
) -> list[PricePoint]:
    """분 캔들 예시는 만들지 않는다 (빈 목록 → 화면은 "주가 자료가 아직 없어요")."""
    if interval in MINUTE_BUCKET:
        return []
    end = today or last_close_date()  # 마지막 값은 직전 장 마감일 종가 (실시간 아님)
    days = _sample_dates(end, period, interval)
    volatility = SAMPLE_VOLATILITY[interval]

    # 같은 종목·기간·날짜면 항상 같은 모양이 나오도록 시드를 고정하고,
    # 마지막 종가가 현재가(예시 값)와 맞도록 뒤에서부터 거슬러 올라가며 만든다.
    rng = random.Random(f"{stock_code}:{period}:{interval}:{end.isoformat()}")
    price = float(current)
    closes = [price]
    for _ in range(len(days) - 1):
        price = max(price / (1 + rng.gauss(0, volatility)), 1)
        closes.append(price)
    closes.reverse()

    # 시가/고가/저가는 예시용으로 만든다: 전날 종가를 시가로 삼고, 당일 변동폭 안에서 고가·저가를 벌린다.
    opens = [closes[0] * (1 + rng.gauss(0, volatility / 4))] + closes[:-1]
    points = []
    for d, o, c in zip(days, opens, closes, strict=True):
        spread = abs(rng.gauss(0, volatility / 2)) * c
        high = max(o, c) + spread
        low = max(min(o, c) - spread, 1)
        points.append(
            PricePoint(date=d, open=round(o), high=round(high), low=round(low), close=round(c))
        )
    return points
