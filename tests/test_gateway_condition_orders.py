from __future__ import annotations

import json

from trade_xquant.condition_orders import ConditionAction, ConditionOrder
from trade_xquant.config import QmtConfig, RiskConfig, RuntimeConfig, Settings, XquantConfig
from trade_xquant.daemon import GatewayService
from trade_xquant.models import AccountSnapshot, PlannedOrder, Position


def settings_for(tmp_path, task_file=None) -> Settings:
    return Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            mock_submit_dry_run_orders=True,
            mock_total_asset=100_000,
            mock_cash=100_000,
            mock_prices={"513100.SH": 1.0},
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
            local_task_file=str(task_file) if task_file else None,
        ),
        risk=RiskConfig(),
    )


def test_gateway_poll_once_reads_local_task_file_and_arms_condition_orders(tmp_path) -> None:
    task_file = tmp_path / "tasks.json"
    task_file.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "portfolio_id": "prod",
                        "account_id": "acct",
                        "mode": "dry_run",
                        "created_at": "2026-06-03T09:35:00+08:00",
                        "expires_at": None,
                        "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                        "constraints": {
                            "condition_orders": [
                                {
                                    "condition_id": "cond-1",
                                    "symbol": "513100.SH",
                                    "purpose": "stop_loss",
                                    "method": "static_pct",
                                    "reference_price": 1.0,
                                    "params": {"stop_loss_pct": 0.05},
                                    "action": {"type": "sell_pct", "pct": 1.0},
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    service = GatewayService(settings_for(tmp_path, task_file))

    result = service.poll_once(force_dry_run=True)

    assert result == [{"task_id": "task-1", "status": "dry_run_success"}]
    assert [order.condition_id for order in service.storage.list_active_condition_orders()] == ["cond-1"]


def test_gateway_poll_once_validates_condition_orders_before_execution(tmp_path) -> None:
    task_file = tmp_path / "tasks.json"
    task_file.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "portfolio_id": "prod",
                        "account_id": "acct",
                        "mode": "dry_run",
                        "created_at": "2026-06-03T09:35:00+08:00",
                        "expires_at": None,
                        "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                        "constraints": {
                            "condition_orders": [
                                {
                                    "condition_id": "cond-invalid",
                                    "symbol": "513100.SH",
                                    "purpose": "stop_loss",
                                    "method": "trailing_pct",
                                    "reference_price": 1.0,
                                    "params": {},
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    service = GatewayService(settings_for(tmp_path, task_file))

    result = service.poll_once(force_dry_run=True)

    assert result[0]["task_id"] == "task-1"
    assert result[0]["status"] == "failed"
    assert "cond-invalid" in str(result[0]["error"])
    assert "trail_pct" in str(result[0]["error"])
    assert service.qmt.submitted_orders == []


def test_gateway_condition_poll_once_executes_triggered_condition_order(tmp_path) -> None:
    service = GatewayService(settings_for(tmp_path))
    service.storage.initialize()
    service.storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-1",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    broker = PositionBroker()
    service.qmt = broker  # type: ignore[assignment]

    result = service.condition_poll_once()

    assert result == [{"condition_id": "cond-1", "status": "dry_run_success"}]
    assert len(broker.submitted_orders) == 1
    submitted = broker.submitted_orders[0]
    assert submitted.task_id == "condition:cond-1"
    assert submitted.symbol == "513100.SH"
    assert submitted.side == "sell"
    assert submitted.quantity == 1000
    assert submitted.remark == "cond:cond-1"
    assert service.storage.get_condition_order("cond-1").status == "submitted"


class PositionBroker:
    def __init__(self) -> None:
        self.submitted_orders: list[PlannedOrder] = []
        self.events = []

    def connect(self) -> None:
        return None

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(account_id="acct", total_asset=100_000, cash=90_000, market_value=10_000)

    def get_positions(self) -> list[Position]:
        return [
            Position(
                symbol="513100.SH",
                quantity=1000,
                sellable_quantity=1000,
                market_value=1100,
                cost_price=1.0,
            )
        ]

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        return {symbol: 1.1 for symbol in symbols}

    def place_order(self, order: PlannedOrder) -> dict[str, object]:
        self.submitted_orders.append(order)
        return {
            "task_id": order.task_id,
            "order_id": "1",
            "broker_order_id": "MOCK-000001",
            "stock_code": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "amount": order.amount,
            "status": "accepted",
        }

    def cancel_order(self, order_id: str) -> None:
        return None
