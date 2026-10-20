from datetime import date, datetime

from app.services.market_clock import KST, last_close_date


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_after_close_on_weekday_is_today():
    assert last_close_date(at(2026, 10, 16, 15, 30)) == date(2026, 10, 16)  # 금


def test_before_close_is_previous_weekday():
    assert last_close_date(at(2026, 10, 16, 9, 0)) == date(2026, 10, 15)  # 금 → 목
    assert last_close_date(at(2026, 10, 19, 9, 0)) == date(2026, 10, 16)  # 월 → 금


def test_weekend_is_friday():
    assert last_close_date(at(2026, 10, 17, 18, 0)) == date(2026, 10, 16)  # 토
    assert last_close_date(at(2026, 10, 18, 10, 0)) == date(2026, 10, 16)  # 일
