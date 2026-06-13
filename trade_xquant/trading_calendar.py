from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from trade_xquant.storage import Storage
from trade_xquant.xquant_adapter import XquantAdapter, XquantAdapterError

logger = logging.getLogger(__name__)

TRADING_CALENDAR_MARKET = "CN_A"
TRADING_CALENDAR_REFRESH_DAYS = 370


class TradingSessionGate(Protocol):
    def is_trading_session(self, now: datetime) -> bool:
        ...


@dataclass(frozen=True)
class TradingCalendarSession:
    name: str
    start: time
    end: time


@dataclass(frozen=True)
class TradingCalendarDay:
    date: date
    is_trading_day: bool
    sessions: tuple[TradingCalendarSession, ...]


class StaticTradingSessionGate:
    def is_trading_session(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        hm = now.hour * 100 + now.minute
        return 930 <= hm <= 1130 or 1300 <= hm <= 1457


class CachedTradingCalendarSessionGate:
    def __init__(
        self,
        *,
        storage: Storage,
        xquant: XquantAdapter,
        timezone: str,
        market: str = TRADING_CALENDAR_MARKET,
        refresh_days: int = TRADING_CALENDAR_REFRESH_DAYS,
    ) -> None:
        self.storage = storage
        self.xquant = xquant
        self.timezone = timezone
        self.market = market
        self.refresh_days = refresh_days

    def refresh_if_needed(self, now: datetime) -> None:
        current = self._local_now(now)
        today = current.date()
        end_date = min(
            today + timedelta(days=self.refresh_days - 1),
            date(today.year, 12, 31),
        )
        if self.storage.has_trading_calendar_range(self.market, today, end_date):
            return
        try:
            payload = self.xquant.fetch_trading_calendar(
                market=self.market,
                start_date=today,
                end_date=end_date,
            )
        except XquantAdapterError as exc:
            logger.warning(
                "failed to refresh trading calendar: market=%s status_code=%s error=%s",
                self.market,
                exc.status_code,
                exc,
            )
            return
        except Exception as exc:  # noqa: BLE001 - local gate must fail closed
            logger.warning(
                "failed to refresh trading calendar: market=%s error=%s",
                self.market,
                exc,
            )
            return
        self.storage.upsert_trading_calendar(payload)

    def is_trading_session(self, now: datetime) -> bool:
        current = self._local_now(now)
        calendar_day = self.storage.get_trading_calendar_day(self.market, current.date())
        if calendar_day is None or not calendar_day.is_trading_day:
            return False
        current_time = current.time().replace(second=0, microsecond=0)
        return any(
            session.start <= current_time <= session.end
            for session in calendar_day.sessions
        )

    def _local_now(self, now: datetime) -> datetime:
        tz = ZoneInfo(self.timezone)
        if now.tzinfo is None:
            return now.replace(tzinfo=tz)
        return now.astimezone(tz)


def parse_trading_calendar_day(payload: dict) -> TradingCalendarDay:
    return TradingCalendarDay(
        date=date.fromisoformat(payload["date"]),
        is_trading_day=bool(payload["is_trading_day"]),
        sessions=tuple(
            TradingCalendarSession(
                name=str(item["name"]),
                start=time.fromisoformat(item["start"]),
                end=time.fromisoformat(item["end"]),
            )
            for item in payload.get("sessions", [])
        ),
    )
