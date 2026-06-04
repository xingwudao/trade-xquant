from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from trade_xquant.condition_indicators import PriceBar
from trade_xquant.models import AccountSnapshot, PlannedOrder, Position


class BrokerAdapter(Protocol):
    def connect(self) -> None: ...
    def get_account_snapshot(self) -> AccountSnapshot: ...
    def get_positions(self) -> list[Position]: ...
    def get_prices(self, symbols: list[str]) -> dict[str, float]: ...
    def get_price_bars(
        self, symbol: str, interval: str, window: int
    ) -> list[PriceBar]: ...
    def place_order(self, order: PlannedOrder) -> Any: ...
    def cancel_order(self, order_id: str) -> Any: ...


@dataclass(frozen=True)
class QmtGatewayEvent:
    event_type: str
    order_id: str | None
    symbol: str | None
    payload: dict[str, Any]
