from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trade_xquant.condition_indicators import PriceBar
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


def test_mock_qmt_query_orders_and_trades_after_partial_fill() -> None:
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.23},
        order_behavior="partial_fill",
        partial_fill_ratio=0.25,
    )

    adapter.place_order(order())

    orders = adapter.get_orders()
    trades = adapter.get_trades()
    assert orders[0]["status"] == "partial_filled"
    assert orders[0]["traded_volume"] == 250
    assert trades[0]["status"] == "partial_filled"
    assert trades[0]["quantity"] == 250


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


def test_mock_qmt_returns_price_bars() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    bars = [
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
    ]
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.18},
        price_bars={"513100.SH": {"1d": bars}},
    )

    result = adapter.get_price_bars("513100.SS", interval="1d", window=2)

    assert [bar.close for bar in result] == [1.05, 1.18]
    assert result[0].symbol == "513100.SH"


def test_mock_qmt_missing_price_bars_raise_clear_error() -> None:
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.18},
    )

    with pytest.raises(
        RuntimeError,
        match=r"mock bars missing for 513100\.SH interval 1d",
    ):
        adapter.get_price_bars("513100.SS", interval="1d", window=1)


def test_mock_qmt_insufficient_price_bars_raise_clear_error() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    bars = [
        PriceBar(
            symbol="513100.SH",
            high=1.1,
            low=1.0,
            close=1.05,
            timestamp=datetime(2026, 6, 1, tzinfo=tz),
        ),
    ]
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.05},
        price_bars={"513100.SH": {"1d": bars}},
    )

    with pytest.raises(
        RuntimeError,
        match=r"mock bars insufficient for 513100\.SH: need 2, got 1",
    ):
        adapter.get_price_bars("513100.SS", interval="1d", window=2)


def test_mock_qmt_non_positive_price_bar_window_raises_value_error() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    bars = [
        PriceBar(
            symbol="513100.SH",
            high=1.1,
            low=1.0,
            close=1.05,
            timestamp=datetime(2026, 6, 1, tzinfo=tz),
        ),
    ]
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.05},
        price_bars={"513100.SH": {"1d": bars}},
    )

    with pytest.raises(ValueError, match="window must be positive"):
        adapter.get_price_bars("513100.SS", interval="1d", window=0)


def test_mock_qmt_non_positive_price_bar_window_validates_before_lookup() -> None:
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.05},
    )

    with pytest.raises(ValueError, match="window must be positive"):
        adapter.get_price_bars("513100.SS", interval="1d", window=0)


def test_mock_qmt_returns_latest_window_and_normalizes_bar_symbols() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    bars = [
        PriceBar(
            symbol="513100.SS",
            high=1.1,
            low=1.0,
            close=1.05,
            timestamp=datetime(2026, 6, 1, tzinfo=tz),
        ),
        PriceBar(
            symbol="513100.SS",
            high=1.2,
            low=1.05,
            close=1.18,
            timestamp=datetime(2026, 6, 2, tzinfo=tz),
        ),
        PriceBar(
            symbol="513100.SS",
            high=1.3,
            low=1.15,
            close=1.24,
            timestamp=datetime(2026, 6, 3, tzinfo=tz),
        ),
    ]
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.24},
        price_bars={"513100.SS": {"1d": bars}},
    )

    result = adapter.get_price_bars("513100.SH", interval="1d", window=2)

    assert [bar.close for bar in result] == [1.18, 1.24]
    assert [bar.symbol for bar in result] == ["513100.SH", "513100.SH"]


def test_mock_qmt_returned_price_bars_do_not_mutate_mock_state() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    bars = [
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
    ]
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.18},
        price_bars={"513100.SH": {"1d": bars}},
    )

    result = adapter.get_price_bars("513100.SS", interval="1d", window=2)
    result[0].close = 1.09

    fresh_result = adapter.get_price_bars("513100.SS", interval="1d", window=2)

    assert [bar.close for bar in fresh_result] == [1.05, 1.18]
    assert fresh_result[0] is not result[0]


def test_mock_qmt_input_price_bars_do_not_mutate_mock_state() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    bars = [
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
    ]
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.18},
        price_bars={"513100.SH": {"1d": bars}},
    )

    bars[0].close = 1.09
    bars.append(
        PriceBar(
            symbol="513100.SH",
            high=1.3,
            low=1.15,
            close=1.24,
            timestamp=datetime(2026, 6, 3, tzinfo=tz),
        )
    )

    result = adapter.get_price_bars("513100.SS", interval="1d", window=2)

    assert [bar.close for bar in result] == [1.05, 1.18]
