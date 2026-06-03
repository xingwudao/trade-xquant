from __future__ import annotations

import pytest

from trade_xquant.models import AccountSnapshot, Position, RebalanceTask, TargetPosition
from trade_xquant.portfolio_engine import PortfolioEngine, PortfolioError


def make_task(**kwargs) -> RebalanceTask:
    data = {
        "task_id": "task-1",
        "portfolio_id": "demo",
        "account_id": "acct",
        "mode": "dry_run",
        "created_at": "2026-05-27T09:35:00+08:00",
        "expires_at": "2026-05-27T14:50:00+08:00",
        "cash_buffer_ratio": 0.002,
        "targets": [
            TargetPosition(symbol="513100.SH", target_weight=0.5),
            TargetPosition(symbol="510300.SH", target_weight=0.5),
        ],
        "constraints": {
            "max_turnover_ratio": 0.8,
            "max_single_order_amount": 50_000,
            "min_order_amount": 1_000,
        },
    }
    data.update(kwargs)
    return RebalanceTask.model_validate(data)


def test_rebalance_uses_100_share_lots_and_cash_buffer() -> None:
    account = AccountSnapshot(account_id="acct", total_asset=100_000, cash=100_000)
    plan = PortfolioEngine().build_plan(
        make_task(),
        account,
        holdings=[],
        prices={"513100.SH": 2.31, "510300.SH": 4.02},
    )

    assert [order.side for order in plan.orders] == ["buy", "buy"]
    assert all(order.quantity % 100 == 0 for order in plan.orders)
    assert plan.total_buy_amount <= 100_000 * (1 - 0.002)
    assert plan.turnover_ratio <= 0.8


def test_sell_does_not_exceed_sellable_quantity() -> None:
    account = AccountSnapshot(account_id="acct", total_asset=100_000, cash=10_000)
    holdings = [Position(symbol="513100.SH", quantity=10_000, sellable_quantity=300)]
    task = make_task(targets=[TargetPosition(symbol="510300.SH", target_weight=0.5)])

    plan = PortfolioEngine().build_plan(
        task,
        account,
        holdings=holdings,
        prices={"513100.SH": 2.0, "510300.SH": 4.0},
    )

    sell = next(order for order in plan.orders if order.side == "sell")
    assert sell.symbol == "513100.SH"
    assert sell.quantity == 300


def test_min_order_amount_filters_small_orders() -> None:
    account = AccountSnapshot(account_id="acct", total_asset=100_000, cash=100_000)
    task = make_task(targets=[TargetPosition(symbol="513100.SH", target_weight=0.005)])

    plan = PortfolioEngine().build_plan(
        task,
        account,
        holdings=[],
        prices={"513100.SH": 2.0},
    )

    assert plan.orders == []


def test_plan_amounts_are_serialized_with_server_decimal_precision() -> None:
    account = AccountSnapshot(account_id="acct", total_asset=100_000, cash=100_000)
    task = make_task(
        cash_buffer_ratio=0,
        targets=[TargetPosition(symbol="513100.SH", target_weight=0.01)],
        constraints={
            "max_turnover_ratio": 0.8,
            "max_single_order_amount": 50_000,
            "min_order_amount": 0,
        },
    )

    plan = PortfolioEngine().build_plan(
        task,
        account,
        holdings=[],
        prices={"513100.SH": 9.876},
    )

    payload = plan.model_dump(mode="json")

    assert payload["orders"][0]["amount"] == 987.6
    assert payload["turnover_amount"] == 987.6


def test_rejects_weights_above_one() -> None:
    account = AccountSnapshot(account_id="acct", total_asset=100_000, cash=100_000)
    task = make_task(
        targets=[
            TargetPosition(symbol="513100.SH", target_weight=0.7),
            TargetPosition(symbol="510300.SH", target_weight=0.4),
        ]
    )

    with pytest.raises(PortfolioError, match="target weights"):
        PortfolioEngine().build_plan(
            task,
            account,
            holdings=[],
            prices={"513100.SH": 2.0, "510300.SH": 4.0},
        )


def test_max_turnover_ratio_rejects_large_plan() -> None:
    account = AccountSnapshot(account_id="acct", total_asset=100_000, cash=100_000)
    task = make_task(
        constraints={
            "max_turnover_ratio": 0.1,
            "max_single_order_amount": 200_000,
            "min_order_amount": 1_000,
        }
    )

    with pytest.raises(PortfolioError, match="turnover"):
        PortfolioEngine().build_plan(
            task,
            account,
            holdings=[],
            prices={"513100.SH": 2.0, "510300.SH": 4.0},
        )
