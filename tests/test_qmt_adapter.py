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


def test_connect_is_idempotent_after_success(monkeypatch) -> None:
    created_traders = []

    class Trader:
        def __init__(self, path, session_id):
            self.path = path
            self.session_id = session_id
            self.connect_calls = 0
            self.subscribe_calls = 0
            created_traders.append(self)

        def register_callback(self, callback):
            self.callback = callback

        def start(self):
            pass

        def connect(self):
            self.connect_calls += 1
            return 0

        def subscribe(self, account):
            self.subscribe_calls += 1
            return 0

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
        QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct")
    )

    adapter.connect()
    adapter.connect()

    assert len(created_traders) == 1
    assert created_traders[0].connect_calls == 1
    assert created_traders[0].subscribe_calls == 1


def test_cancel_order_accepts_zero_return_code() -> None:
    class Trader:
        def __init__(self) -> None:
            self.cancelled: list[int] = []

        def cancel_order_stock(self, account, order_id):
            self.cancelled.append(order_id)
            return 0

    trader = Trader()
    adapter = QmtAdapter(
        QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct")
    )
    adapter.trader = trader
    adapter.account = SimpleNamespace(account_id="acct")
    adapter._connected = True

    adapter.cancel_order("1082169287")

    assert trader.cancelled == [1082169287]


@pytest.mark.parametrize("return_code", [-1, 1])
def test_cancel_order_rejects_nonzero_return_code(return_code: int) -> None:
    class Trader:
        def cancel_order_stock(self, account, order_id):
            return return_code

    adapter = QmtAdapter(
        QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct")
    )
    adapter.trader = Trader()
    adapter.account = SimpleNamespace(account_id="acct")
    adapter._connected = True

    with pytest.raises(RuntimeError) as exc:
        adapter.cancel_order("1082169287")

    message = str(exc.value)
    assert "order_id=1082169287" in message
    assert f"return_code={return_code}" in message
