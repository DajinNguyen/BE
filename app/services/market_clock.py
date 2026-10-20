"""장 마감 기준일. 공휴일 달력은 아직 없어서 주말만 뺀다."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
MARKET_CLOSE = time(15, 30)


def last_close_date(now: datetime | None = None) -> date:
    """가장 최근에 장이 마감된 날. 평일 15:30 이후면 오늘, 아니면 그 전 평일."""
    now = (now or datetime.now(KST)).astimezone(KST)
    day = now.date()
    if day.weekday() >= 5 or now.time() < MARKET_CLOSE:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day
