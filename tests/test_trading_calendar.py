from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from trade_xquant.storage import Storage
from trade_xquant.trading_calendar import CachedTradingCalendarSessionGate


class FakeXquant:
    def __init__(self) -> None:
        self.requests: list[tuple[str, date, date]] = []

    def fetch_trading_calendar(
        self,
        *,
        market: str,
        start_date: date,
        end_date: date,
    ) -> dict:
        self.requests.append((market, start_date, end_date))
        days = []
        current = start_date
        while current <= end_date:
            is_trading_day = current.weekday() < 5
            days.append(
                {
                    "date": current.isoformat(),
                    "is_trading_day": is_trading_day,
                    "sessions": [
                        {"name": "morning", "start": "09:30", "end": "11:30"},
                        {"name": "afternoon", "start": "13:00", "end": "14:57"},
                    ]
                    if is_trading_day
                    else [],
                }
            )
            current = date.fromordinal(current.toordinal() + 1)
        return {
            "market": market,
            "timezone": "Asia/Shanghai",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "calendar_version": "test-calendar",
            "generated_at": "2026-06-12T00:00:00+08:00",
            "days": days,
        }


class FailingXquant(FakeXquant):
    def fetch_trading_calendar(
        self,
        *,
        market: str,
        start_date: date,
        end_date: date,
    ) -> dict:
        self.requests.append((market, start_date, end_date))
        raise RuntimeError("calendar unavailable")


class IncompleteXquant(FakeXquant):
    def fetch_trading_calendar(
        self,
        *,
        market: str,
        start_date: date,
        end_date: date,
    ) -> dict:
        self.requests.append((market, start_date, end_date))
        return {
            "market": market,
            "timezone": "Asia/Shanghai",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "calendar_version": "incomplete-calendar",
            "generated_at": "2026-06-12T00:00:00+08:00",
            "days": [
                {
                    "date": start_date.isoformat(),
                    "is_trading_day": True,
                    "sessions": [
                        {"name": "morning", "start": "09:30", "end": "11:30"},
                    ],
                }
            ],
        }


def test_cached_calendar_gate_uses_local_calendar(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    xquant = FakeXquant()
    gate = CachedTradingCalendarSessionGate(
        storage=storage,
        xquant=xquant,  # type: ignore[arg-type]
        timezone="Asia/Shanghai",
    )

    gate.refresh_if_needed(datetime(2026, 6, 12, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert gate.is_trading_session(
        datetime(2026, 6, 12, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert not gate.is_trading_session(
        datetime(2026, 6, 12, 11, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert not gate.is_trading_session(
        datetime(2026, 6, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )


def test_cached_calendar_gate_fails_closed_without_cache(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    gate = CachedTradingCalendarSessionGate(
        storage=storage,
        xquant=FakeXquant(),  # type: ignore[arg-type]
        timezone="Asia/Shanghai",
    )

    assert not gate.is_trading_session(
        datetime(2026, 6, 12, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )


def test_cached_calendar_gate_fails_closed_after_incomplete_refresh_with_stale_day(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_trading_calendar(
        {
            "market": "CN_A",
            "timezone": "Asia/Shanghai",
            "calendar_version": "stale-calendar",
            "generated_at": "2026-06-01T00:00:00+08:00",
            "days": [
                {
                    "date": "2026-06-12",
                    "is_trading_day": True,
                    "sessions": [
                        {"name": "morning", "start": "09:30", "end": "11:30"},
                    ],
                }
            ],
        }
    )
    xquant = IncompleteXquant()
    gate = CachedTradingCalendarSessionGate(
        storage=storage,
        xquant=xquant,  # type: ignore[arg-type]
        timezone="Asia/Shanghai",
        refresh_days=2,
    )

    gate.refresh_if_needed(datetime(2026, 6, 12, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert xquant.requests == [
        ("CN_A", date(2026, 6, 12), date(2026, 6, 13))
    ]
    assert not gate.is_trading_session(
        datetime(2026, 6, 12, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )


def test_cached_calendar_refresh_does_not_cross_year_boundary(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    xquant = FakeXquant()
    gate = CachedTradingCalendarSessionGate(
        storage=storage,
        xquant=xquant,  # type: ignore[arg-type]
        timezone="Asia/Shanghai",
    )

    gate.refresh_if_needed(datetime(2026, 12, 20, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert xquant.requests == [
        ("CN_A", date(2026, 12, 20), date(2026, 12, 31))
    ]


def test_cached_calendar_gate_fails_closed_after_refresh_failure_with_stale_day(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_trading_calendar(
        {
            "market": "CN_A",
            "timezone": "Asia/Shanghai",
            "calendar_version": "stale-calendar",
            "generated_at": "2026-06-01T00:00:00+08:00",
            "days": [
                {
                    "date": "2026-06-12",
                    "is_trading_day": True,
                    "sessions": [
                        {"name": "morning", "start": "09:30", "end": "11:30"},
                    ],
                }
            ],
        }
    )
    xquant = FailingXquant()
    gate = CachedTradingCalendarSessionGate(
        storage=storage,
        xquant=xquant,  # type: ignore[arg-type]
        timezone="Asia/Shanghai",
        refresh_days=2,
    )

    gate.refresh_if_needed(datetime(2026, 6, 12, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert xquant.requests == [
        ("CN_A", date(2026, 6, 12), date(2026, 6, 13))
    ]
    assert not gate.is_trading_session(
        datetime(2026, 6, 12, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
