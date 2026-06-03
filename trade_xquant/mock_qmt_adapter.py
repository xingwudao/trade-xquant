from __future__ import annotations

from typing import Callable

from trade_xquant.broker import QmtGatewayEvent
from trade_xquant.models import AccountSnapshot, PlannedOrder, Position, normalize_symbol


class MockBrokerAdapter:
    def __init__(
        self,
        account_id: str,
        total_asset: float,
        cash: float,
        prices: dict[str, float],
        order_behavior: str = "filled",
        partial_fill_ratio: float = 0.5,
        event_handler: Callable[[QmtGatewayEvent], None] | None = None,
    ) -> None:
        self.account_id = account_id
        self.total_asset = total_asset
        self.cash = cash
        self.prices = {normalize_symbol(symbol): price for symbol, price in prices.items()}
        self.order_behavior = order_behavior
        self.partial_fill_ratio = max(0.0, min(1.0, partial_fill_ratio))
        self.event_handler = event_handler
        self.submitted_orders: list[PlannedOrder] = []
        self.events: list[QmtGatewayEvent] = []

    def connect(self) -> None:
        self._emit("connected", None, None, {"account_id": self.account_id, "broker": "mock_qmt"})

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account_id=self.account_id,
            total_asset=self.total_asset,
            cash=self.cash,
            market_value=max(0.0, self.total_asset - self.cash),
        )

    def get_positions(self) -> list[Position]:
        return []

    def get_orders(self) -> list[dict[str, object]]:
        return [dict(event.payload) for event in self.events if event.event_type == "stock_order"]

    def get_trades(self) -> list[dict[str, object]]:
        return [dict(event.payload) for event in self.events if event.event_type == "stock_trade"]

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        missing = [symbol for symbol in symbols if normalize_symbol(symbol) not in self.prices]
        if missing:
            raise RuntimeError(f"mock price missing for symbols: {sorted(set(missing))}")
        return {normalize_symbol(symbol): self.prices[normalize_symbol(symbol)] for symbol in symbols}

    def place_order(self, order: PlannedOrder) -> dict[str, object]:
        local_order_id = str(len(self.submitted_orders) + 1)
        broker_order_id = f"MOCK-{local_order_id.zfill(6)}"
        response = {
            "task_id": order.task_id,
            "order_id": local_order_id,
            "broker_order_id": broker_order_id,
            "stock_code": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "amount": order.amount,
            "status": "accepted",
            "broker": "mock_qmt",
        }
        self._emit("order_response", local_order_id, order.symbol, response)

        if self.order_behavior == "reject":
            self._emit(
                "order_error",
                local_order_id,
                order.symbol,
                {
                    **response,
                    "status": "rejected",
                    "error_id": 900001,
                    "error_msg": "mock order rejected",
                },
            )
            raise RuntimeError("mock order rejected")

        self.submitted_orders.append(order)
        traded_quantity = order.quantity
        order_status = "filled"
        if self.order_behavior == "partial_fill":
            traded_quantity = int(order.quantity * self.partial_fill_ratio)
            traded_quantity = max(0, min(order.quantity, traded_quantity))
            order_status = "partial_filled"

        self._emit(
            "stock_order",
            local_order_id,
            order.symbol,
            {**response, "status": order_status, "traded_volume": traded_quantity},
        )
        if traded_quantity > 0:
            trade_amount = traded_quantity * order.price
            self._emit(
                "stock_trade",
                local_order_id,
                order.symbol,
                {
                    **response,
                    "status": order_status,
                    "trade_id": f"MOCK-TRADE-{local_order_id.zfill(6)}",
                    "quantity": traded_quantity,
                    "traded_volume": traded_quantity,
                    "price": order.price,
                    "amount": trade_amount,
                    "trade_amount": trade_amount,
                },
            )
        return response

    def cancel_order(self, order_id: str) -> None:
        self._emit(
            "cancel_response",
            order_id,
            None,
            {"order_id": order_id, "status": "cancelled", "broker": "mock_qmt"},
        )

    def _emit(
        self,
        event_type: str,
        order_id: str | None,
        symbol: str | None,
        payload: dict[str, object],
    ) -> None:
        event = QmtGatewayEvent(
            event_type=event_type,
            order_id=order_id,
            symbol=normalize_symbol(symbol) if symbol else None,
            payload=dict(payload),
        )
        self.events.append(event)
        if self.event_handler:
            self.event_handler(event)
