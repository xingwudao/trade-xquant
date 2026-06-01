from __future__ import annotations

import os
from typing import Any

from trade_xquant.config import RuntimeConfig
from trade_xquant.models import ExecutionResult, OrderPlan, SubmittedOrder, TaskMode


class RealOrderGate:
    def __init__(self, runtime: RuntimeConfig) -> None:
        self.runtime = runtime

    def assert_enabled(self) -> None:
        if not self.runtime.allow_real_order:
            raise PermissionError("real order disabled by config")
        if os.getenv("TRADE_XQUANT_ENABLE_REAL_ORDER") != "1":
            raise PermissionError("real order disabled by environment")


class ExecutionEngine:
    def __init__(self, broker, runtime: RuntimeConfig) -> None:
        self.broker = broker
        self.runtime = runtime
        self.gate = RealOrderGate(runtime)

    def execute(self, plan: OrderPlan, mode: TaskMode) -> ExecutionResult:
        if mode == "dry_run":
            if self.runtime.broker_adapter == "mock" and self.runtime.mock_submit_dry_run_orders:
                event_cursor = self._event_cursor()
                submitted, errors = self._submit_orders(plan)
                return ExecutionResult(
                    task_id=plan.task_id,
                    status="failed" if errors else "dry_run_success",
                    mode=mode,
                    planned_orders=plan.orders,
                    submitted_orders=submitted,
                    trades=self._broker_trades(event_cursor),
                    events=self._broker_events(event_cursor),
                    errors=errors,
                    meta={"mock_submit_dry_run_orders": True},
                )
            return ExecutionResult(
                task_id=plan.task_id,
                status="dry_run_success",
                mode=mode,
                planned_orders=plan.orders,
            )

        if not (self.runtime.broker_adapter == "mock" and self.runtime.simulate_real_orders):
            self.gate.assert_enabled()
        event_cursor = self._event_cursor()
        submitted, errors = self._submit_orders(plan)
        return ExecutionResult(
            task_id=plan.task_id,
            status="failed" if errors else "submitted",
            mode=mode,
            planned_orders=plan.orders,
            submitted_orders=submitted,
            trades=self._broker_trades(event_cursor),
            events=self._broker_events(event_cursor),
            errors=errors,
        )

    def _submit_orders(self, plan: OrderPlan) -> tuple[list[SubmittedOrder], list[str]]:
        submitted: list[SubmittedOrder] = []
        errors: list[str] = []
        for order in plan.orders:
            try:
                response = self.broker.place_order(order)
                local_order_id = None
                broker_order_id = None
                raw_response = {"response": response}
                if isinstance(response, dict):
                    local_order_id = response.get("order_id")
                    broker_order_id = response.get("broker_order_id")
                    raw_response = response
                submitted.append(
                    SubmittedOrder(
                        task_id=order.task_id,
                        symbol=order.symbol,
                        side=order.side,
                        quantity=order.quantity,
                        price=order.price,
                        amount=order.amount,
                        local_order_id=str(local_order_id or response) if response is not None else None,
                        broker_order_id=str(broker_order_id) if broker_order_id else None,
                        raw=raw_response,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - broker exceptions must be audited
                errors.append(f"{order.symbol} {order.side} failed: {exc}")
        return submitted, errors

    def _event_cursor(self) -> int:
        return len(getattr(self.broker, "events", []))

    def _broker_events(self, start_index: int = 0) -> list[dict[str, Any]]:
        events = getattr(self.broker, "events", [])
        result: list[dict[str, Any]] = []
        for event in events[start_index:]:
            result.append(
                {
                    "event_type": event.event_type,
                    "order_id": event.order_id,
                    "symbol": event.symbol,
                    "payload": event.payload,
                }
            )
        return result

    def _broker_trades(self, start_index: int = 0) -> list[dict[str, Any]]:
        trades: list[dict[str, Any]] = []
        for event in self._broker_events(start_index):
            if event["event_type"] != "stock_trade":
                continue
            payload = dict(event["payload"])
            if not payload.get("symbol"):
                payload["symbol"] = event["symbol"] or payload.get("stock_code")
            if not payload.get("order_id"):
                payload["order_id"] = event["order_id"]
            trades.append(payload)
        return trades
