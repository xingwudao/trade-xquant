from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trade_xquant.condition_orders import (
    ConditionAction,
    ConditionEngine,
    ConditionOrder,
    extract_condition_orders,
)
from trade_xquant.models import AccountSnapshot, Position, RebalanceTask
from trade_xquant.storage import Storage


def task_with_conditions() -> RebalanceTask:
    return RebalanceTask.model_validate(
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
                        "condition_id": "cond-static",
                        "symbol": "513100.SH",
                        "purpose": "take_profit",
                        "method": "static_pct",
                        "reference_price": 1.0,
                        "params": {"take_profit_pct": 0.1},
                        "action": {"type": "sell_pct", "pct": 0.5},
                    }
                ]
            },
        }
    )


def test_extract_condition_orders_from_task_constraints() -> None:
    task = task_with_conditions()

    orders = extract_condition_orders(task)

    assert len(orders) == 1
    assert orders[0].condition_id == "cond-static"
    assert orders[0].task_id == "task-1"
    assert orders[0].symbol == "513100.SH"
    assert orders[0].status == "armed"


def test_storage_persists_active_condition_orders(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    orders = extract_condition_orders(task_with_conditions())

    storage.upsert_condition_orders(orders)

    active = storage.list_active_condition_orders()
    assert [order.condition_id for order in active] == ["cond-static"]
    assert active[0].params == {"take_profit_pct": 0.1}


def test_condition_engine_triggers_static_take_profit_sell_plan(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    order = extract_condition_orders(task_with_conditions())[0]
    storage.upsert_condition_orders([order])
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=AccountSnapshot(account_id="acct", total_asset=100_000, cash=90_000),
        positions=[
            Position(
                symbol="513100.SH",
                quantity=1000,
                sellable_quantity=1000,
                market_value=1100,
                cost_price=1.0,
            )
        ],
        prices={"513100.SH": 1.1},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert len(plans) == 1
    plan = plans[0]
    assert plan.task.mode == "dry_run"
    assert plan.order.condition_id == "cond-static"
    assert plan.plan.orders[0].symbol == "513100.SH"
    assert plan.plan.orders[0].side == "sell"
    assert plan.plan.orders[0].quantity == 500
    assert plan.plan.orders[0].remark == "cond:cond-static"
    assert storage.get_condition_order("cond-static").status == "triggered"


def test_condition_engine_updates_trailing_high_water_before_trigger(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-trailing",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="trailing_pct",
                reference_price=1.0,
                high_water_price=1.0,
                params={"trail_pct": 0.08},
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=AccountSnapshot(account_id="acct", total_asset=100_000, cash=90_000),
        positions=[
            Position(
                symbol="513100.SH",
                quantity=1000,
                sellable_quantity=1000,
                market_value=1200,
                cost_price=1.0,
            )
        ],
        prices={"513100.SH": 1.2},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert plans == []
    stored = storage.get_condition_order("cond-trailing")
    assert stored.high_water_price == 1.2
    assert stored.trigger_price == 1.104
    assert stored.status == "armed"


def test_deferred_methods_do_not_reuse_trailing_pct_trigger_logic() -> None:
    engine = ConditionEngine(storage=None)
    order = ConditionOrder(
        condition_id="cond-atr",
        task_id="task-1",
        portfolio_id="prod",
        account_id="acct",
        mode="dry_run",
        symbol="513100.SH",
        purpose="stop_loss",
        method="atr_trailing",
        reference_price=1.0,
        high_water_price=1.2,
        params={
            "atr_window": 3,
            "atr_multiple": 2.0,
            "bar_interval": "1d",
            "trail_pct": 0.08,
        },
        action=ConditionAction(type="sell_pct", pct=1.0),
    )

    expected_error = (
        "condition cond-atr method atr_trailing "
        "trigger calculation is not implemented"
    )
    with pytest.raises(ValueError, match=expected_error):
        engine._trigger_price(order, high_water_price=1.2)
