from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from trade_xquant.config import Settings
from trade_xquant.models import AccountSnapshot, OrderPlan, RebalanceTask


class RiskError(ValueError):
    pass


class RiskControl:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate(
        self,
        task: RebalanceTask,
        account: AccountSnapshot,
        plan: OrderPlan,
        now: datetime | None = None,
        known_symbols: set[str] | None = None,
    ) -> None:
        tz = ZoneInfo(self.settings.risk.timezone)
        current = now or datetime.now(tz)
        if current.tzinfo is None:
            current = current.replace(tzinfo=tz)

        if task.account_id != self.settings.qmt.account_id or account.account_id != task.account_id:
            raise RiskError("account id mismatch")
        if task.expires_at is not None and task.expires_at <= current:
            raise RiskError("task expired")
        if sum(target.target_weight for target in task.targets) > 1 + 1e-9:
            raise RiskError("target weights cannot exceed 1")
        if known_symbols is not None:
            unknown = {target.symbol for target in task.targets} - known_symbols
            if unknown:
                raise RiskError(f"unknown symbol: {sorted(unknown)}")
        for order in plan.orders:
            if order.amount > self.settings.risk.max_single_order_amount:
                raise RiskError("single order amount exceeds threshold")
            if order.price <= 0:
                raise RiskError(f"invalid price for {order.symbol}")
        if plan.turnover_ratio > self.settings.risk.max_turnover_ratio:
            raise RiskError("task turnover exceeds threshold")
        if task.mode == "real":
            if self._is_simulated_broker():
                return
            self._validate_real_order_enabled()
            if not self.is_trading_session(current.astimezone(tz)):
                raise RiskError("real order outside trading session")

    def _is_simulated_broker(self) -> bool:
        return self.settings.runtime.broker_adapter == "mock" and self.settings.runtime.simulate_real_orders

    def validate_real_order_enabled(self) -> None:
        self._validate_real_order_enabled()

    def _validate_real_order_enabled(self) -> None:
        if not self.settings.runtime.allow_real_order:
            raise RiskError("real order disabled by config")
        if os.getenv("TRADE_XQUANT_ENABLE_REAL_ORDER") != "1":
            raise RiskError("real order disabled by environment")

    def is_trading_session(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        hm = now.hour * 100 + now.minute
        return 930 <= hm <= 1130 or 1300 <= hm <= 1457

    def _is_trading_session(self, now: datetime) -> bool:
        return self.is_trading_session(now)
