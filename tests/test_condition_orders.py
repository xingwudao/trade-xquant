from __future__ import annotations

import json
import math
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trade_xquant.condition_orders import (
    ConditionAction,
    ConditionEngine,
    ConditionOrder,
    extract_condition_orders,
)
from trade_xquant.condition_indicators import PriceBar
from trade_xquant.mock_qmt_adapter import MockBrokerAdapter
from trade_xquant.models import AccountSnapshot, Position, RebalanceTask
from trade_xquant.storage import Storage


class BarProvider:
    def __init__(self, bars: list[PriceBar]) -> None:
        self.bars = bars
        self.calls: list[tuple[str, str, int]] = []

    def get_price_bars(self, symbol: str, interval: str, window: int) -> list[PriceBar]:
        self.calls.append((symbol, interval, window))
        return self.bars[-window:]


class UnwiredBarProvider:
    def get_price_bars(self, symbol: str, interval: str, window: int) -> list[PriceBar]:
        raise NotImplementedError("bars are not wired")


class TimeoutBarProvider:
    def get_price_bars(self, symbol: str, interval: str, window: int) -> list[PriceBar]:
        raise TimeoutError("bars timed out")


def price_bars() -> list[PriceBar]:
    tz = ZoneInfo("Asia/Shanghai")
    return [
        PriceBar(
            symbol="513100.SH",
            high=1.1,
            low=1.0,
            close=1.05,
            timestamp=datetime(2026, 6, 1, tzinfo=tz),
        ),
        PriceBar(
            symbol="513100.SH",
            high=1.2,
            low=1.05,
            close=1.18,
            timestamp=datetime(2026, 6, 2, tzinfo=tz),
        ),
        PriceBar(
            symbol="513100.SH",
            high=1.3,
            low=1.15,
            close=1.24,
            timestamp=datetime(2026, 6, 3, tzinfo=tz),
        ),
    ]


def account() -> AccountSnapshot:
    return AccountSnapshot(account_id="acct", total_asset=100_000, cash=90_000)


def position() -> Position:
    return Position(
        symbol="513100.SH",
        quantity=1000,
        sellable_quantity=1000,
        market_value=1200,
        cost_price=1.0,
    )


def second_position() -> Position:
    return Position(
        symbol="159915.SZ",
        quantity=1000,
        sellable_quantity=1000,
        market_value=1200,
        cost_price=1.0,
    )


def condition_event_payload(storage: Storage, condition_id: str) -> dict[str, object]:
    with closing(storage._connect()) as conn:
        row = conn.execute(
            """
            SELECT event_type, payload_json
            FROM condition_order_events
            WHERE condition_id=?
            ORDER BY id
            """,
            (condition_id,),
        ).fetchone()
    assert row["event_type"] == "evaluation_error"
    return json.loads(row["payload_json"])


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


def test_extract_condition_orders_requires_explicit_action() -> None:
    task = RebalanceTask.model_validate(
        {
            "task_id": "task-missing-action",
            "portfolio_id": "prod",
            "account_id": "acct",
            "mode": "dry_run",
            "created_at": "2026-06-03T09:35:00+08:00",
            "expires_at": None,
            "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
            "constraints": {
                "condition_orders": [
                    {
                        "condition_id": "cond-missing-action",
                        "symbol": "513100.SH",
                        "purpose": "take_profit",
                        "method": "static_pct",
                        "reference_price": 1.0,
                        "params": {"take_profit_pct": 0.1},
                    }
                ]
            },
        }
    )

    with pytest.raises(ValueError, match="action"):
        extract_condition_orders(task)


def test_storage_persists_active_condition_orders(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    orders = extract_condition_orders(task_with_conditions())

    storage.upsert_condition_orders(orders)

    active = storage.list_active_condition_orders()
    assert [order.condition_id for order in active] == ["cond-static"]
    assert active[0].params == {"take_profit_pct": 0.1}


def test_storage_returns_condition_order_triggered_at(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    order = extract_condition_orders(task_with_conditions())[0]
    storage.upsert_condition_orders([order])

    assert storage.get_condition_order_triggered_at("cond-static") is None

    storage.update_condition_order_status("cond-static", "triggered")

    triggered_at = storage.get_condition_order_triggered_at("cond-static")
    assert triggered_at is not None
    assert datetime.fromisoformat(triggered_at).tzinfo is not None


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


def test_condition_engine_uses_pct_when_trailing_pct_is_none(tmp_path) -> None:
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
                params={"trail_pct": None, "pct": 0.08},
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
    assert storage.get_condition_order("cond-trailing").trigger_price == 1.104


def test_condition_engine_triggers_atr_trailing_stop_loss(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="atr_trailing",
                high_water_price=1.30,
                params={
                    "atr_window": 3,
                    "atr_multiple": 2.5,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    provider = BarProvider(price_bars())
    engine = ConditionEngine(storage, market_data=provider)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 0.95},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-atr"]
    state = storage.get_condition_market_state("cond-atr")
    assert state is not None
    expected_atr = 0.1333
    assert provider.calls == [("513100.SH", "1d", 3)]
    assert state["atr_value"] == pytest.approx(expected_atr)
    assert state["trigger_price"] == pytest.approx(1.30 - 2.5 * expected_atr)


def test_condition_engine_caps_same_symbol_triggered_sells(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-static-tp",
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
            ),
            ConditionOrder(
                condition_id="cond-trailing-sl",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="trailing_pct",
                high_water_price=1.3,
                params={"trail_pct": 0.08},
                action=ConditionAction(type="clear"),
            ),
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static-tp"]
    assert plans[0].plan.orders[0].quantity == 1000
    assert storage.get_condition_order("cond-static-tp").status == "triggered"
    assert storage.get_condition_order("cond-trailing-sl").status == "failed"


def test_condition_engine_trailing_take_profit_requires_activation(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-trailing-tp",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="take_profit",
                method="trailing_pct",
                reference_price=1.0,
                params={"trail_pct": 0.08, "activation_profit_pct": 0.2},
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    engine = ConditionEngine(storage)
    tz = ZoneInfo("Asia/Shanghai")

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.1},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=tz),
    )

    assert plans == []
    state = storage.get_condition_market_state("cond-trailing-tp")
    assert state is not None
    assert state["activated"] is False

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.25},
        now=datetime(2026, 6, 3, 10, 1, tzinfo=tz),
    )

    assert plans == []
    state = storage.get_condition_market_state("cond-trailing-tp")
    assert state is not None
    assert state["activated"] is True
    assert state["high_water_price"] == 1.25

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.14},
        now=datetime(2026, 6, 3, 10, 2, tzinfo=tz),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-trailing-tp"]


def test_condition_engine_triggers_hv_log_trailing_stop_loss(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-hv",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="hv_log_trailing",
                high_water_price=1.30,
                params={
                    "hv_window": 3,
                    "hv_annualization": 252,
                    "lambda": 0.37,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    provider = BarProvider(price_bars())
    engine = ConditionEngine(storage, market_data=provider)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.0},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-hv"]
    state = storage.get_condition_market_state("cond-hv")
    assert state is not None
    close_prices = [bar.close for bar in price_bars()]
    returns = [
        math.log(current / previous)
        for previous, current in zip(close_prices, close_prices[1:])
    ]
    mean_return = sum(returns) / len(returns)
    expected_hv = math.sqrt(
        sum((item - mean_return) ** 2 for item in returns) / len(returns)
    ) * math.sqrt(252)
    expected_trigger = 1.30 * math.exp(-0.37 * expected_hv)
    linear_trigger = 1.30 * (1 - 0.37 * expected_hv)
    assert provider.calls == [("513100.SH", "1d", 3)]
    assert state["hv_value"] == pytest.approx(expected_hv)
    assert state["trigger_price"] == pytest.approx(expected_trigger)
    assert abs(expected_trigger - linear_trigger) > 0.01


def test_condition_engine_triggers_std_trailing_stop_loss(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-std",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="std_trailing",
                high_water_price=1.30,
                params={
                    "std_window": 3,
                    "std_multiple": 2.5,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    provider = BarProvider(price_bars())
    engine = ConditionEngine(storage, market_data=provider)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.09},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-std"]
    state = storage.get_condition_market_state("cond-std")
    assert state is not None
    close_prices = [bar.close for bar in price_bars()]
    mean_close = sum(close_prices) / len(close_prices)
    expected_std = math.sqrt(
        sum((item - mean_close) ** 2 for item in close_prices) / len(close_prices)
    )
    assert provider.calls == [("513100.SH", "1d", 3)]
    assert state["std_value"] == pytest.approx(expected_std)
    assert state["trigger_price"] == pytest.approx(1.30 - 2.5 * expected_std)


def test_deferred_take_profit_with_activation_price_can_activate_without_reference(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-tp",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="take_profit",
                method="atr_trailing",
                reference_price=None,
                params={
                    "atr_window": 3,
                    "atr_multiple": 1.0,
                    "bar_interval": "1d",
                    "activation_price": 1.2,
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    engine = ConditionEngine(storage, market_data=BarProvider(price_bars()))

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.21},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert plans == []
    state = storage.get_condition_market_state("cond-atr-tp")
    assert state is not None
    assert state["activated"] is True
    assert state["activated_at"] == "2026-06-03T10:00:00+08:00"
    assert state["high_water_price"] == 1.21


def test_atr_take_profit_activates_then_triggers_on_pullback(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-tp-pullback",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="take_profit",
                method="atr_trailing",
                reference_price=1.0,
                params={
                    "atr_window": 3,
                    "atr_multiple": 1.0,
                    "bar_interval": "1d",
                    "activation_profit_pct": 0.2,
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    provider = BarProvider(price_bars())
    engine = ConditionEngine(storage, market_data=provider)
    tz = ZoneInfo("Asia/Shanghai")

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.1},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=tz),
    )

    assert plans == []
    assert provider.calls == []
    state = storage.get_condition_market_state("cond-atr-tp-pullback")
    assert state is not None
    assert state["activated"] is False
    assert state["trigger_price"] is None

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.25},
        now=datetime(2026, 6, 3, 10, 1, tzinfo=tz),
    )

    assert plans == []
    assert provider.calls == [("513100.SH", "1d", 3)]
    state = storage.get_condition_market_state("cond-atr-tp-pullback")
    assert state is not None
    assert state["activated"] is True
    assert state["activated_at"] == "2026-06-03T10:01:00+08:00"
    assert state["high_water_price"] == 1.25
    assert state["trigger_price"] == pytest.approx(1.25 - 0.1333)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.1},
        now=datetime(2026, 6, 3, 10, 2, tzinfo=tz),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-atr-tp-pullback"]
    assert provider.calls == [("513100.SH", "1d", 3), ("513100.SH", "1d", 3)]
    state = storage.get_condition_market_state("cond-atr-tp-pullback")
    assert state is not None
    assert state["activated_at"] == "2026-06-03T10:01:00+08:00"
    assert state["high_water_price"] == 1.25


def test_inactive_indicator_take_profit_does_not_require_market_data(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-tp-inactive",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="take_profit",
                method="atr_trailing",
                reference_price=1.0,
                params={
                    "atr_window": 3,
                    "atr_multiple": 1.0,
                    "bar_interval": "1d",
                    "activation_profit_pct": 0.2,
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.1},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert plans == []
    state = storage.get_condition_market_state("cond-atr-tp-inactive")
    assert state is not None
    assert state["activated"] is False
    assert state["trigger_price"] is None
    assert state["atr_value"] is None


@pytest.mark.parametrize(
    ("method", "params"),
    [
        (
            "atr_trailing",
            {
                "atr_window": 3,
                "atr_multiple": 2.0,
                "bar_interval": "1d",
                "trail_pct": 0.08,
            },
        ),
        (
            "hv_log_trailing",
            {
                "hv_window": 3,
                "hv_annualization": 252,
                "lambda": 0.2,
                "bar_interval": "1d",
                "trail_pct": 0.08,
            },
        ),
        (
            "std_trailing",
            {
                "std_window": 3,
                "std_multiple": 1.0,
                "bar_interval": "1d",
                "trail_pct": 0.08,
            },
        ),
    ],
)
def test_indicator_method_requires_market_data_without_trailing_fallback(
    method,
    params,
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id=f"cond-{method}",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method=method,
                high_water_price=1.2,
                params=params,
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert plans == []
    reason = f"condition cond-{method} requires market_data for {method}"
    assert condition_event_payload(storage, f"cond-{method}") == {
        "method": method,
        "reason": reason,
    }
    state = storage.get_condition_market_state(f"cond-{method}")
    assert state is not None
    assert state["state"]["evaluation_error"] == reason


def test_missing_market_data_does_not_block_other_triggered_conditions(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-error",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="atr_trailing",
                high_water_price=1.2,
                params={
                    "atr_window": 3,
                    "atr_multiple": 2.0,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static",
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
            ),
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static"]
    assert storage.get_condition_order("cond-atr-error").status == "armed"
    assert storage.get_condition_order("cond-static").status == "triggered"
    reason = "condition cond-atr-error requires market_data for atr_trailing"
    assert condition_event_payload(storage, "cond-atr-error") == {
        "method": "atr_trailing",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-atr-error")
    assert state is not None
    assert state["state"]["evaluation_error"] == reason


def test_indicator_error_preserves_new_high_water(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-high-water-error",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="atr_trailing",
                high_water_price=1.2,
                params={
                    "atr_window": 3,
                    "atr_multiple": 2.0,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.3},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert plans == []
    reason = "condition cond-atr-high-water-error requires market_data for atr_trailing"
    assert condition_event_payload(storage, "cond-atr-high-water-error") == {
        "method": "atr_trailing",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-atr-high-water-error")
    assert state is not None
    assert state["high_water_price"] == 1.3
    assert state["state"]["evaluation_error"] == reason
    stored = storage.get_condition_order("cond-atr-high-water-error")
    assert stored.high_water_price == 1.3
    assert stored.trigger_price is None


def test_mock_broker_missing_bars_do_not_block_later_condition(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-mock-missing",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="atr_trailing",
                high_water_price=1.2,
                params={
                    "atr_window": 3,
                    "atr_multiple": 2.0,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static",
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
            ),
        ]
    )
    market_data = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=90_000,
        prices={"513100.SH": 1.12},
    )
    engine = ConditionEngine(storage, market_data=market_data)

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static"]
    reason = "mock bars missing for 513100.SH interval 1d"
    assert condition_event_payload(storage, "cond-atr-mock-missing") == {
        "method": "atr_trailing",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-atr-mock-missing")
    assert state is not None
    assert state["state"]["evaluation_error"] == reason
    assert state["latest_price"] == 1.12


def test_provider_timeout_does_not_block_later_condition(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-timeout",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="atr_trailing",
                high_water_price=1.2,
                params={
                    "atr_window": 3,
                    "atr_multiple": 2.0,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static",
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
            ),
        ]
    )
    engine = ConditionEngine(storage, market_data=TimeoutBarProvider())

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static"]
    assert condition_event_payload(storage, "cond-atr-timeout") == {
        "method": "atr_trailing",
        "reason": "bars timed out",
    }
    state = storage.get_condition_market_state("cond-atr-timeout")
    assert state is not None
    assert state["state"]["evaluation_error"] == "bars timed out"


def test_indicator_error_preserves_new_take_profit_activation(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-tp-timeout",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="take_profit",
                method="atr_trailing",
                reference_price=1.0,
                params={
                    "activation_profit_pct": 0.2,
                    "atr_window": 3,
                    "atr_multiple": 1.5,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static-second",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="159915.SZ",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
        ]
    )
    engine = ConditionEngine(storage, market_data=TimeoutBarProvider())
    now = datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    plans = engine.evaluate(
        account=account(),
        positions=[position(), second_position()],
        prices={"513100.SH": 1.25, "159915.SZ": 1.12},
        now=now,
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static-second"]
    state = storage.get_condition_market_state("cond-atr-tp-timeout")
    assert state is not None
    assert state["activated"] is True
    assert state["activated_at"] == now.isoformat()
    assert state["high_water_price"] == 1.25
    assert state["state"]["evaluation_error"] == "bars timed out"


def test_malformed_indicator_params_do_not_block_later_condition(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-malformed",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="atr_trailing",
                high_water_price=1.2,
                params={
                    "atr_window": [],
                    "atr_multiple": 2.0,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static",
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
            ),
        ]
    )
    engine = ConditionEngine(storage, market_data=BarProvider(price_bars()))

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static"]
    reason = (
        "condition cond-atr-malformed missing/invalid condition params: "
        "atr_window"
    )
    assert condition_event_payload(storage, "cond-atr-malformed") == {
        "method": "atr_trailing",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-atr-malformed")
    assert state is not None
    assert state["state"]["evaluation_error"] == reason


def test_unwired_bar_provider_does_not_block_later_condition(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-atr-unwired",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="atr_trailing",
                high_water_price=1.2,
                params={
                    "atr_window": 3,
                    "atr_multiple": 2.0,
                    "bar_interval": "1d",
                },
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static",
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
            ),
        ]
    )
    engine = ConditionEngine(storage, market_data=UnwiredBarProvider())

    plans = engine.evaluate(
        account=account(),
        positions=[position()],
        prices={"513100.SH": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static"]
    reason = "bars are not wired"
    assert condition_event_payload(storage, "cond-atr-unwired") == {
        "method": "atr_trailing",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-atr-unwired")
    assert state is not None
    assert state["state"]["evaluation_error"] == reason
    assert state["latest_price"] == 1.12


@pytest.mark.parametrize(
    ("condition_id", "prices", "reason"),
    [
        (
            "cond-missing-price",
            {"159915.SZ": 1.12},
            "condition cond-missing-price missing latest_price",
        ),
        (
            "cond-zero-price",
            {"513100.SH": 0, "159915.SZ": 1.12},
            "condition cond-zero-price invalid latest_price: 0",
        ),
        (
            "cond-negative-price",
            {"513100.SH": -1.0, "159915.SZ": 1.12},
            "condition cond-negative-price invalid latest_price: -1.0",
        ),
        (
            "cond-inf-price",
            {"513100.SH": math.inf, "159915.SZ": 1.12},
            "condition cond-inf-price invalid latest_price: inf",
        ),
        (
            "cond-nan-price",
            {"513100.SH": math.nan, "159915.SZ": 1.12},
            "condition cond-nan-price invalid latest_price: nan",
        ),
    ],
)
def test_invalid_latest_price_records_error_state_and_continues(
    condition_id,
    prices,
    reason,
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id=condition_id,
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
            ),
            ConditionOrder(
                condition_id="cond-static-second",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="159915.SZ",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position(), second_position()],
        prices=prices,
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static-second"]
    assert condition_event_payload(storage, condition_id) == {
        "method": "static_pct",
        "reason": reason,
    }
    state = storage.get_condition_market_state(condition_id)
    assert state is not None
    assert state["trigger_price"] is None
    assert state["state"]["evaluation_error"] == reason


def test_invalid_stored_take_profit_param_records_error_and_continues(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-negative-take-profit",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": -0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static-second",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="159915.SZ",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position(), second_position()],
        prices={"513100.SH": 0.95, "159915.SZ": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static-second"]
    assert storage.get_condition_order("cond-negative-take-profit").status == "armed"
    reason = (
        "condition cond-negative-take-profit missing/invalid condition params: "
        "take_profit_pct"
    )
    assert condition_event_payload(storage, "cond-negative-take-profit") == {
        "method": "static_pct",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-negative-take-profit")
    assert state is not None
    assert state["trigger_price"] is None
    assert state["state"]["evaluation_error"] == reason


def test_non_finite_stored_reference_price_records_error_and_continues(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-inf-reference",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="static_pct",
                reference_price=math.inf,
                params={"stop_loss_pct": 0.05},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static-second",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="159915.SZ",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position(), second_position()],
        prices={"513100.SH": 0.5, "159915.SZ": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static-second"]
    assert storage.get_condition_order("cond-inf-reference").status == "armed"
    reason = (
        "condition cond-inf-reference missing/invalid condition params: "
        "reference_price"
    )
    assert condition_event_payload(storage, "cond-inf-reference") == {
        "method": "static_pct",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-inf-reference")
    assert state is not None
    assert state["trigger_price"] is None
    assert state["state"]["evaluation_error"] == reason


def test_non_finite_stored_high_water_price_records_error_and_continues(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-inf-high-water",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="trailing_pct",
                high_water_price=math.inf,
                params={"trail_pct": 0.08},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static-second",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="159915.SZ",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
        ]
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position(), second_position()],
        prices={"513100.SH": 1.0, "159915.SZ": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static-second"]
    assert storage.get_condition_order("cond-inf-high-water").status == "armed"
    reason = (
        "condition cond-inf-high-water missing/invalid condition params: "
        "high_water_price"
    )
    assert condition_event_payload(storage, "cond-inf-high-water") == {
        "method": "trailing_pct",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-inf-high-water")
    assert state is not None
    assert state["trigger_price"] is None
    assert state["state"]["evaluation_error"] == reason


def test_non_finite_market_state_high_water_records_error_and_continues(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-state-inf-high-water",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="trailing_pct",
                high_water_price=1.2,
                params={"trail_pct": 0.08},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static-second",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="159915.SZ",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
        ]
    )
    storage.record_condition_market_state(
        condition_id="cond-state-inf-high-water",
        symbol="513100.SH",
        latest_price=1.0,
        high_water_price=math.inf,
        trigger_price=math.inf,
        activated=True,
        activated_at=None,
        atr_value=None,
        hv_value=None,
        std_value=None,
        computed_at=datetime(2026, 6, 3, 9, 59, tzinfo=ZoneInfo("Asia/Shanghai")).isoformat(),
        market_data_source="prices",
        state={"method": "trailing_pct", "purpose": "stop_loss"},
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position(), second_position()],
        prices={"513100.SH": 1.0, "159915.SZ": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static-second"]
    assert storage.get_condition_order("cond-state-inf-high-water").status == "armed"
    reason = (
        "condition cond-state-inf-high-water invalid stored market_state "
        "high_water_price"
    )
    assert condition_event_payload(storage, "cond-state-inf-high-water") == {
        "method": "trailing_pct",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-state-inf-high-water")
    assert state is not None
    assert state["high_water_price"] == 1.2
    assert state["trigger_price"] is None
    assert state["state"]["evaluation_error"] == reason


def test_non_finite_market_state_trigger_price_records_error_and_continues(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-state-inf-trigger",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="513100.SH",
                purpose="stop_loss",
                method="trailing_pct",
                high_water_price=1.2,
                params={"trail_pct": 0.08},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
            ConditionOrder(
                condition_id="cond-static-second",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="dry_run",
                symbol="159915.SZ",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
        ]
    )
    storage.record_condition_market_state(
        condition_id="cond-state-inf-trigger",
        symbol="513100.SH",
        latest_price=1.0,
        high_water_price=1.2,
        trigger_price=math.inf,
        activated=True,
        activated_at=None,
        atr_value=None,
        hv_value=None,
        std_value=None,
        computed_at=datetime(2026, 6, 3, 9, 59, tzinfo=ZoneInfo("Asia/Shanghai")).isoformat(),
        market_data_source="prices",
        state={"method": "trailing_pct", "purpose": "stop_loss"},
    )
    engine = ConditionEngine(storage)

    plans = engine.evaluate(
        account=account(),
        positions=[position(), second_position()],
        prices={"513100.SH": 1.0, "159915.SZ": 1.12},
        now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [plan.order.condition_id for plan in plans] == ["cond-static-second"]
    assert storage.get_condition_order("cond-state-inf-trigger").status == "armed"
    reason = (
        "condition cond-state-inf-trigger invalid stored market_state "
        "trigger_price"
    )
    assert condition_event_payload(storage, "cond-state-inf-trigger") == {
        "method": "trailing_pct",
        "reason": reason,
    }
    state = storage.get_condition_market_state("cond-state-inf-trigger")
    assert state is not None
    assert state["high_water_price"] == 1.2
    assert state["trigger_price"] is None
    assert state["state"]["evaluation_error"] == reason


def test_extract_condition_orders_rejects_missing_required_params() -> None:
    task = RebalanceTask.model_validate(
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
                        "condition_id": "cond-missing",
                        "symbol": "513100.SH",
                        "purpose": "stop_loss",
                        "method": "trailing_pct",
                        "reference_price": 1.0,
                        "params": {},
                        "action": {"type": "sell_pct", "pct": 1.0},
                    }
                ]
            },
        }
    )

    with pytest.raises(
        ValueError,
        match="condition cond-missing missing/invalid condition params: trail_pct",
    ):
        extract_condition_orders(task)


def test_extract_condition_orders_skips_disabled_false_like_rules() -> None:
    task = RebalanceTask.model_validate(
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
                        "condition_id": "cond-disabled-0",
                        "symbol": "513100.SH",
                        "purpose": "stop_loss",
                        "method": "trailing_pct",
                        "reference_price": 1.0,
                        "enabled": 0,
                        "params": {},
                    },
                    {
                        "condition_id": "cond-disabled-false",
                        "symbol": "513100.SH",
                        "purpose": "stop_loss",
                        "method": "trailing_pct",
                        "reference_price": 1.0,
                        "enabled": "false",
                        "params": {},
                    },
                    {
                        "condition_id": "cond-disabled-string-0",
                        "symbol": "513100.SH",
                        "purpose": "stop_loss",
                        "method": "trailing_pct",
                        "reference_price": 1.0,
                        "enabled": "0",
                        "params": {},
                    },
                ]
            },
        }
    )

    assert extract_condition_orders(task) == []
