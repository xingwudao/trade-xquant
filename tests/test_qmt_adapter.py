from __future__ import annotations

from types import SimpleNamespace

import pytest

from trade_xquant.broker import QmtGatewayEvent
from trade_xquant.config import QmtConfig
from trade_xquant.models import PlannedOrder
from trade_xquant.qmt_adapter import QmtAdapter, normalize_qmt_event


def test_normalize_order_error_event() -> None:
    event = normalize_qmt_event(
        "order_error",
        SimpleNamespace(order_id=12, error_id=100, error_msg="reject"),
    )

    assert event == QmtGatewayEvent(
        event_type="order_error",
        order_id="12",
        symbol=None,
        payload={"error_id": 100, "error_msg": "reject", "order_id": 12},
    )


def test_planned_order_maps_to_qmt_stock_order_constants() -> None:
    order = PlannedOrder(
        task_id="task-1",
        symbol="513100.SH",
        side="sell",
        quantity=100,
        price=1.23,
        amount=123,
    )

    assert order.qmt_order_type == 24
    assert order.qmt_price_type == 11


def test_subscribe_failure_message_includes_result_and_account_diagnostics(monkeypatch) -> None:
    class Trader:
        def __init__(self, path, session_id):
            self.path = path
            self.session_id = session_id

        def register_callback(self, callback):
            self.callback = callback

        def start(self):
            pass

        def connect(self):
            return 0

        def subscribe(self, account):
            return -1

        def query_account_infos(self):
            return [SimpleNamespace(account_id="actual-acct", status="ok")]

    class Callback:
        pass

    class Account:
        def __init__(self, account_id):
            self.account_id = account_id

    monkeypatch.setattr(
        "trade_xquant.qmt_adapter.load_xtquant",
        lambda: (Trader, Callback, Account),
    )
    adapter = QmtAdapter(
        QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="wrong-acct")
    )

    with pytest.raises(ConnectionError) as exc:
        adapter.connect()

    message = str(exc.value)
    assert "subscribe_result=-1" in message
    assert "wrong-acct" in message
    assert "actual-acct" in message
