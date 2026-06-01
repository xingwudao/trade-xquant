from __future__ import annotations

import pytest

from trade_xquant.mock_qmt_adapter import MockBrokerAdapter
from trade_xquant.models import PlannedOrder


def order() -> PlannedOrder:
    return PlannedOrder(
        task_id="task-1",
        symbol="513100.SS",
        side="buy",
        quantity=1000,
        price=1.23,
        amount=1230,
    )


def test_mock_qmt_filled_order_emits_order_and_trade_events() -> None:
    events = []
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.23},
        event_handler=events.append,
    )

    response = adapter.place_order(order())

    assert response["order_id"] == "1"
    assert response["broker_order_id"] == "MOCK-000001"
    assert [event.event_type for event in events] == ["order_response", "stock_order", "stock_trade"]
    assert events[-1].symbol == "513100.SH"
    assert events[-1].payload["quantity"] == 1000


def test_mock_qmt_partial_fill_emits_partial_trade() -> None:
    events = []
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.23},
        order_behavior="partial_fill",
        partial_fill_ratio=0.25,
        event_handler=events.append,
    )

    adapter.place_order(order())

    trade = events[-1]
    assert trade.event_type == "stock_trade"
    assert trade.payload["status"] == "partial_filled"
    assert trade.payload["quantity"] == 250


def test_mock_qmt_reject_emits_order_error_and_raises() -> None:
    events = []
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.23},
        order_behavior="reject",
        event_handler=events.append,
    )

    with pytest.raises(RuntimeError, match="mock order rejected"):
        adapter.place_order(order())

    assert [event.event_type for event in events] == ["order_response", "order_error"]
    assert events[-1].payload["error_msg"] == "mock order rejected"
