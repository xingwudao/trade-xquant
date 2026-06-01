from __future__ import annotations

import pytest

from trade_xquant.broker import QmtGatewayEvent
from trade_xquant.config import RuntimeConfig
from trade_xquant.execution_engine import ExecutionEngine, RealOrderGate
from trade_xquant.models import OrderPlan, PlannedOrder


def test_real_order_gate_requires_config_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    with pytest.raises(PermissionError):
        RealOrderGate(RuntimeConfig(allow_real_order=False)).assert_enabled()

    monkeypatch.delenv("TRADE_XQUANT_ENABLE_REAL_ORDER", raising=False)
    with pytest.raises(PermissionError):
        RealOrderGate(RuntimeConfig(allow_real_order=True)).assert_enabled()

    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    RealOrderGate(RuntimeConfig(allow_real_order=True)).assert_enabled()


class FakeBroker:
    def place_order(self, order):
        return {"order_id": "1", "broker_order_id": "MOCK-000001"}


class EventBroker:
    def __init__(self) -> None:
        self.events = []

    def place_order(self, order):
        self.events.append(
            QmtGatewayEvent(
                event_type="stock_trade",
                order_id="1",
                symbol=order.symbol,
                payload={
                    "symbol": None,
                    "stock_code": order.symbol,
                    "quantity": order.quantity,
                    "price": order.price,
                    "amount": order.amount,
                },
            )
        )
        return {"order_id": "1", "broker_order_id": "MOCK-000001"}


def test_mock_broker_can_simulate_real_order_without_real_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADE_XQUANT_ENABLE_REAL_ORDER", raising=False)
    runtime = RuntimeConfig(
        allow_real_order=False,
        broker_adapter="mock",
        simulate_real_orders=True,
    )
    plan = OrderPlan(
        task_id="task-1",
        account_id="acct",
        total_asset=100_000,
        turnover_amount=1_000,
        turnover_ratio=0.01,
        orders=[
            PlannedOrder(
                task_id="task-1",
                symbol="513100.SH",
                side="buy",
                quantity=1000,
                price=1,
                amount=1000,
            )
        ],
    )

    result = ExecutionEngine(FakeBroker(), runtime).execute(plan, "real")

    assert result.status == "submitted"
    assert result.submitted_orders[0].local_order_id == "1"
    assert result.submitted_orders[0].broker_order_id == "MOCK-000001"


def test_mock_broker_can_submit_dry_run_orders_for_integration_tests() -> None:
    runtime = RuntimeConfig(
        broker_adapter="mock",
        mock_submit_dry_run_orders=True,
    )
    plan = OrderPlan(
        task_id="task-1",
        account_id="acct",
        total_asset=100_000,
        turnover_amount=1_000,
        turnover_ratio=0.01,
        orders=[
            PlannedOrder(
                task_id="task-1",
                symbol="513100.SH",
                side="buy",
                quantity=1000,
                price=1,
                amount=1000,
            )
        ],
    )

    result = ExecutionEngine(FakeBroker(), runtime).execute(plan, "dry_run")

    assert result.status == "dry_run_success"
    assert result.mode == "dry_run"
    assert result.submitted_orders[0].local_order_id == "1"
    assert result.meta["mock_submit_dry_run_orders"] is True


def test_execution_result_trade_symbol_is_filled_from_event_symbol() -> None:
    runtime = RuntimeConfig(
        broker_adapter="mock",
        mock_submit_dry_run_orders=True,
    )
    plan = OrderPlan(
        task_id="task-1",
        account_id="acct",
        total_asset=100_000,
        turnover_amount=1_000,
        turnover_ratio=0.01,
        orders=[
            PlannedOrder(
                task_id="task-1",
                symbol="513100.SH",
                side="buy",
                quantity=1000,
                price=1,
                amount=1000,
            )
        ],
    )

    result = ExecutionEngine(EventBroker(), runtime).execute(plan, "dry_run")

    assert result.trades[0]["symbol"] == "513100.SH"
