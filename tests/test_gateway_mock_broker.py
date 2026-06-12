from __future__ import annotations

from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trade_xquant.config import QmtConfig, RiskConfig, RuntimeConfig, Settings, XquantConfig
from trade_xquant.daemon import GatewayService
from trade_xquant.models import AccountSnapshot, Position, RebalanceTask


class FakeXquant:
    def __init__(self) -> None:
        self.plans: list[dict] = []
        self.results: list[tuple[str, str, int, int, int]] = []
        self.result_bodies: list[dict] = []
        self.heartbeats: list[dict] = []

    def fetch_pending_tasks(self, account_id: str):
        return [
            RebalanceTask.model_validate(
                {
                    "task_id": "task-1",
                    "portfolio_id": "prod",
                    "account_id": account_id,
                    "mode": "dry_run",
                    "created_at": "2026-05-27T09:35:00+08:00",
                    "expires_at": None,
                    "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                }
            )
        ]

    def report_plan(self, task_id: str, plan: dict) -> None:
        self.plans.append(plan)

    def report_result(self, task_id: str, status: str, payload) -> None:
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        self.result_bodies.append(body)
        self.results.append(
            (
                task_id,
                status,
                len(body.get("submitted_orders", [])),
                len(body.get("trades", [])),
                len(body.get("events", [])),
            )
        )

    def heartbeat(self, account_id: str, **payload) -> dict:
        self.heartbeats.append({"account_id": account_id, **payload})
        return {"ok": True}


def freeze_gateway_now(monkeypatch, value: datetime) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    monkeypatch.setattr("trade_xquant.daemon.datetime", FrozenDateTime)


def real_order_settings(tmp_path) -> Settings:
    return Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="qmt",
            allow_real_order=True,
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(max_single_order_amount=100_000),
    )


def real_task(account_id: str = "acct", *, expires_at: str | None = None) -> RebalanceTask:
    return RebalanceTask.model_validate(
        {
            "task_id": "task-real-pending-session",
            "portfolio_id": "prod",
            "account_id": account_id,
            "mode": "real",
            "created_at": "2026-06-11T08:00:00+08:00",
            "expires_at": expires_at,
            "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
        }
    )


class RealOrderBroker:
    events = []

    def __init__(self) -> None:
        self.submitted_orders = []

    def connect(self) -> None:
        return None

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(account_id="acct", total_asset=100_000, cash=100_000)

    def get_positions(self) -> list[Position]:
        return []

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        return {symbol: 1.0 for symbol in symbols}

    def place_order(self, order):
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


class PriceFailingRealOrderBroker(RealOrderBroker):
    def __init__(self) -> None:
        super().__init__()
        self.price_calls: list[list[str]] = []

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        self.price_calls.append(symbols)
        raise RuntimeError("cannot fetch valid price for 513100.SH")


def test_gateway_poll_once_can_use_mock_broker_without_qmt(tmp_path) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            simulate_real_orders=True,
            mock_total_asset=100_000,
            mock_cash=100_000,
            mock_prices={"513100.SH": 1.0},
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()
    service.xquant = fake_xquant  # type: ignore[assignment]

    result = service.poll_once(force_dry_run=True)

    assert result == [{"task_id": "task-1", "status": "dry_run_success"}]
    assert fake_xquant.plans
    assert fake_xquant.results == [("task-1", "dry_run_success", 0, 0, 0)]


def test_gateway_poll_once_uses_task_endpoint_even_when_product_code_is_configured(tmp_path) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1", product_code="prod"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            simulate_real_orders=True,
            mock_total_asset=100_000,
            mock_cash=100_000,
            mock_prices={"513100.SH": 1.0},
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()
    service.xquant = fake_xquant  # type: ignore[assignment]

    result = service.poll_once(force_dry_run=True)

    assert result == [{"task_id": "task-1", "status": "dry_run_success"}]
    assert fake_xquant.plans
    assert fake_xquant.results == [("task-1", "dry_run_success", 0, 0, 0)]


def test_gateway_real_task_outside_trading_session_defers_without_terminal_result(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    freeze_gateway_now(
        monkeypatch,
        datetime(2026, 6, 11, 8, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    service = GatewayService(real_order_settings(tmp_path))
    fake_xquant = FakeXquant()
    fake_xquant.fetch_pending_tasks = lambda account_id: [real_task(account_id)]  # type: ignore[method-assign]
    broker = RealOrderBroker()
    service.xquant = fake_xquant  # type: ignore[assignment]
    service.qmt = broker  # type: ignore[assignment]

    result = service.poll_once()

    assert result == [
        {
            "task_id": "task-real-pending-session",
            "status": "pending_execution",
            "error": "real order outside trading session",
        }
    ]
    assert broker.submitted_orders == []
    assert fake_xquant.results == []
    assert service.storage.load_task_result_payload("task-real-pending-session") is None
    with closing(service.storage._connect()) as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE task_id=?",
            ("task-real-pending-session",),
        ).fetchone()
    assert row["status"] == "pending_execution"


def test_gateway_real_task_outside_session_defers_before_fetching_prices(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    freeze_gateway_now(
        monkeypatch,
        datetime(2026, 6, 12, 8, 47, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    service = GatewayService(real_order_settings(tmp_path))
    fake_xquant = FakeXquant()
    fake_xquant.fetch_pending_tasks = lambda account_id: [real_task(account_id)]  # type: ignore[method-assign]
    broker = PriceFailingRealOrderBroker()
    service.xquant = fake_xquant  # type: ignore[assignment]
    service.qmt = broker  # type: ignore[assignment]

    result = service.poll_once()

    assert result == [
        {
            "task_id": "task-real-pending-session",
            "status": "pending_execution",
            "error": "real order outside trading session",
        }
    ]
    assert broker.price_calls == []
    assert broker.submitted_orders == []
    assert fake_xquant.results == []
    assert service.storage.load_task_result_payload("task-real-pending-session") is None


def test_gateway_real_task_outside_session_checks_config_before_deferring(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    freeze_gateway_now(
        monkeypatch,
        datetime(2026, 6, 12, 8, 47, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    settings = real_order_settings(tmp_path)
    settings.runtime.allow_real_order = False
    service = GatewayService(settings)
    fake_xquant = FakeXquant()
    fake_xquant.fetch_pending_tasks = lambda account_id: [real_task(account_id)]  # type: ignore[method-assign]
    broker = PriceFailingRealOrderBroker()
    service.xquant = fake_xquant  # type: ignore[assignment]
    service.qmt = broker  # type: ignore[assignment]

    result = service.poll_once()

    assert result == [
        {
            "task_id": "task-real-pending-session",
            "status": "failed",
            "error": "real order disabled by config",
        }
    ]
    assert broker.price_calls == []
    assert broker.submitted_orders == []
    assert fake_xquant.results == [("task-real-pending-session", "failed", 0, 0, 0)]
    payload = service.storage.load_task_result_payload("task-real-pending-session")
    assert payload["errors"] == ["real order disabled by config"]


def test_gateway_real_task_outside_session_checks_env_before_deferring(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("TRADE_XQUANT_ENABLE_REAL_ORDER", raising=False)
    freeze_gateway_now(
        monkeypatch,
        datetime(2026, 6, 12, 8, 47, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    service = GatewayService(real_order_settings(tmp_path))
    fake_xquant = FakeXquant()
    fake_xquant.fetch_pending_tasks = lambda account_id: [real_task(account_id)]  # type: ignore[method-assign]
    broker = PriceFailingRealOrderBroker()
    service.xquant = fake_xquant  # type: ignore[assignment]
    service.qmt = broker  # type: ignore[assignment]

    result = service.poll_once()

    assert result == [
        {
            "task_id": "task-real-pending-session",
            "status": "failed",
            "error": "real order disabled by environment",
        }
    ]
    assert broker.price_calls == []
    assert broker.submitted_orders == []
    assert fake_xquant.results == [("task-real-pending-session", "failed", 0, 0, 0)]
    payload = service.storage.load_task_result_payload("task-real-pending-session")
    assert payload["errors"] == ["real order disabled by environment"]


def test_gateway_retries_pending_execution_task_when_trading_session_opens(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    service = GatewayService(real_order_settings(tmp_path))
    fake_xquant = FakeXquant()
    broker = RealOrderBroker()
    service.xquant = fake_xquant  # type: ignore[assignment]
    service.qmt = broker  # type: ignore[assignment]

    freeze_gateway_now(
        monkeypatch,
        datetime(2026, 6, 11, 8, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    fake_xquant.fetch_pending_tasks = lambda account_id: [real_task(account_id)]  # type: ignore[method-assign]
    assert service.poll_once()[0]["status"] == "pending_execution"

    freeze_gateway_now(
        monkeypatch,
        datetime(2026, 6, 11, 9, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    fake_xquant.fetch_pending_tasks = lambda account_id: []  # type: ignore[method-assign]

    result = service.poll_once()

    assert result == [{"task_id": "task-real-pending-session", "status": "submitted"}]
    assert len(broker.submitted_orders) == 1
    assert fake_xquant.results == [("task-real-pending-session", "submitted", 1, 0, 0)]
    assert service.storage.load_task_result_payload("task-real-pending-session")["status"] == "submitted"


def test_gateway_pending_execution_task_fails_when_expired_before_session_opens(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    service = GatewayService(real_order_settings(tmp_path))
    fake_xquant = FakeXquant()
    broker = RealOrderBroker()
    service.xquant = fake_xquant  # type: ignore[assignment]
    service.qmt = broker  # type: ignore[assignment]

    freeze_gateway_now(
        monkeypatch,
        datetime(2026, 6, 11, 8, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    fake_xquant.fetch_pending_tasks = lambda account_id: [  # type: ignore[method-assign]
        real_task(account_id, expires_at="2026-06-11T09:45:00+08:00")
    ]
    assert service.poll_once()[0]["status"] == "pending_execution"

    freeze_gateway_now(
        monkeypatch,
        datetime(2026, 6, 11, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    fake_xquant.fetch_pending_tasks = lambda account_id: []  # type: ignore[method-assign]

    result = service.poll_once()

    assert result == [
        {
            "task_id": "task-real-pending-session",
            "status": "failed",
            "error": "task expired",
        }
    ]
    assert broker.submitted_orders == []
    assert fake_xquant.results == [("task-real-pending-session", "failed", 0, 0, 0)]
    payload = service.storage.load_task_result_payload("task-real-pending-session")
    assert payload["status"] == "failed"
    assert payload["errors"] == ["task expired"]


def test_daemon_loop_sends_heartbeat_with_holdings_when_no_tasks(tmp_path, monkeypatch) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()
    fake_xquant.fetch_pending_tasks = lambda account_id: []  # type: ignore[method-assign]
    service.xquant = fake_xquant  # type: ignore[assignment]

    class SnapshotBroker:
        def connect(self) -> None:
            return None

        def get_account_snapshot(self) -> AccountSnapshot:
            return AccountSnapshot(
                account_id="acct",
                total_asset=100_000,
                cash=98_000,
                market_value=2_000,
            )

        def get_positions(self) -> list[Position]:
            return [
                Position(
                    symbol="513100.SH",
                    quantity=1000,
                    sellable_quantity=1000,
                    market_value=2000,
                )
            ]

        def get_prices(self, symbols: list[str]) -> dict[str, float]:
            return {symbol: 2.0 for symbol in symbols}

    class StopDaemon(Exception):
        pass

    def stop_sleep(seconds: float) -> None:
        raise StopDaemon()

    monkeypatch.setattr("trade_xquant.daemon.time.sleep", stop_sleep)
    service.qmt = SnapshotBroker()  # type: ignore[assignment]

    with pytest.raises(StopDaemon):
        service.run_forever()

    assert fake_xquant.heartbeats
    heartbeat = fake_xquant.heartbeats[0]
    assert heartbeat["qmt_connected"] is True
    assert heartbeat["cash"] == 98_000
    assert heartbeat["total_asset"] == 100_000
    assert heartbeat["holdings"] == [
        {
            "symbol": "513100.SH",
            "shares": 1000,
            "reference_price": 2.0,
            "market_value": 2000,
            "weight": 0.02,
            "target_weight": None,
        }
    ]


def test_daemon_loop_runs_submitted_order_sync(tmp_path, monkeypatch) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            poll_interval_seconds=60,
            condition_poll_interval_seconds=60,
            order_sync_interval_seconds=1,
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    calls = {"sync": 0, "sleep": 0}
    events: list[str] = []
    current_time = {"value": 0.0}

    def poll_once():
        events.append("poll")
        return []

    def condition_poll_once():
        events.append("condition")
        return []

    def heartbeat_once(last_error=None):
        events.append("heartbeat")
        return {"ok": True}

    def sync_once():
        events.append("sync")
        calls["sync"] += 1
        if calls["sync"] >= 2:
            raise KeyboardInterrupt
        return []

    service.poll_once = poll_once  # type: ignore[method-assign]
    service.condition_poll_once = condition_poll_once  # type: ignore[method-assign]
    service.heartbeat_once = heartbeat_once  # type: ignore[method-assign]
    service.sync_submitted_orders_once = sync_once  # type: ignore[method-assign]

    def fake_monotonic():
        return current_time["value"]

    def fake_sleep(seconds):
        calls["sleep"] += 1
        current_time["value"] += seconds

    monkeypatch.setattr("trade_xquant.daemon.time.monotonic", fake_monotonic)
    monkeypatch.setattr("trade_xquant.daemon.time.sleep", fake_sleep)

    with pytest.raises(KeyboardInterrupt):
        service.run_forever()

    assert events[:4] == ["poll", "condition", "heartbeat", "sync"]
    assert calls["sync"] == 2


def test_daemon_heartbeat_reports_qmt_disconnected_when_check_fails(tmp_path) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()
    service.xquant = fake_xquant  # type: ignore[assignment]

    class FailingBroker:
        def connect(self) -> None:
            raise ConnectionError("qmt offline")

    service.qmt = FailingBroker()  # type: ignore[assignment]

    assert service.heartbeat_once()["ok"] is True

    heartbeat = fake_xquant.heartbeats[0]
    assert heartbeat["qmt_connected"] is False
    assert "heartbeat qmt check failed: qmt offline" in heartbeat["last_error"]
    assert heartbeat["cash"] is None


def test_gateway_mock_broker_records_events_trades_and_submitted_orders(tmp_path) -> None:
    settings = Settings(
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
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()
    service.xquant = fake_xquant  # type: ignore[assignment]

    result = service.poll_once(force_dry_run=True)

    assert result == [{"task_id": "task-1", "status": "dry_run_success"}]
    assert fake_xquant.results == [("task-1", "dry_run_success", 1, 1, 3)]
    assert fake_xquant.result_bodies[0]["trades"][0]["symbol"] == "513100.SH"
    with closing(service.storage._connect()) as conn:
        submitted_count = conn.execute("SELECT COUNT(*) FROM submitted_orders").fetchone()[0]
        event_types = [
            row[0]
            for row in conn.execute("SELECT event_type FROM order_events ORDER BY id").fetchall()
        ]
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert submitted_count == 1
    assert event_types == ["connected", "order_response", "stock_order", "stock_trade"]
    assert trade_count == 1


def test_gateway_reports_account_and_holding_snapshot_in_result(tmp_path) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()

    def fetch_pending_tasks(account_id: str):
        return [
            RebalanceTask.model_validate(
                {
                    "task_id": "task-1",
                    "portfolio_id": "prod",
                    "account_id": account_id,
                    "mode": "dry_run",
                    "created_at": "2026-05-27T09:35:00+08:00",
                    "expires_at": None,
                    "targets": [{"symbol": "513100.SH", "target_weight": 0.02}],
                }
            )
        ]

    class SnapshotBroker:
        def connect(self) -> None:
            return None

        def get_account_snapshot(self) -> AccountSnapshot:
            return AccountSnapshot(
                account_id="acct",
                total_asset=100_000,
                cash=98_000,
                market_value=2_000,
            )

        def get_positions(self) -> list[Position]:
            return [
                Position(
                    symbol="513100.SH",
                    quantity=1000,
                    sellable_quantity=1000,
                    market_value=2000,
                    cost_price=1.5,
                )
            ]

        def get_prices(self, symbols: list[str]) -> dict[str, float]:
            return {symbol: 2.0 for symbol in symbols}

    fake_xquant.fetch_pending_tasks = fetch_pending_tasks  # type: ignore[method-assign]
    service.xquant = fake_xquant  # type: ignore[assignment]
    service.qmt = SnapshotBroker()  # type: ignore[assignment]

    result = service.poll_once(force_dry_run=True)

    assert result == [{"task_id": "task-1", "status": "dry_run_success"}]
    result_body = fake_xquant.result_bodies[0]
    assert result_body["cash"] == 98_000
    assert result_body["total_asset"] == 100_000
    assert result_body["holdings"] == [
        {
            "symbol": "513100.SH",
            "shares": 1000,
            "reference_price": 2.0,
            "market_value": 2000,
            "weight": 0.02,
            "target_weight": 0.02,
        }
    ]


def test_gateway_reports_current_snapshot_after_order_execution(tmp_path) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            mock_submit_dry_run_orders=True,
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()

    def fetch_pending_tasks(account_id: str):
        return [
            RebalanceTask.model_validate(
                {
                    "task_id": "task-1",
                    "portfolio_id": "prod",
                    "account_id": account_id,
                    "mode": "dry_run",
                    "created_at": "2026-05-27T09:35:00+08:00",
                    "expires_at": None,
                    "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                }
            )
        ]

    class ChangingBroker:
        events = []

        def __init__(self) -> None:
            self.ordered = False

        def connect(self) -> None:
            return None

        def get_account_snapshot(self) -> AccountSnapshot:
            return AccountSnapshot(
                account_id="acct",
                total_asset=100_000,
                cash=50_000 if self.ordered else 100_000,
                market_value=50_000 if self.ordered else 0,
            )

        def get_positions(self) -> list[Position]:
            if not self.ordered:
                return []
            return [
                Position(
                    symbol="513100.SH",
                    quantity=50_000,
                    sellable_quantity=50_000,
                    market_value=50_000,
                )
            ]

        def get_prices(self, symbols: list[str]) -> dict[str, float]:
            return {symbol: 1.0 for symbol in symbols}

        def place_order(self, order):
            self.ordered = True
            return {
                "task_id": order.task_id,
                "order_id": "1",
                "broker_order_id": "MOCK-000001",
            }

    fake_xquant.fetch_pending_tasks = fetch_pending_tasks  # type: ignore[method-assign]
    service.xquant = fake_xquant  # type: ignore[assignment]
    service.qmt = ChangingBroker()  # type: ignore[assignment]

    result = service.poll_once(force_dry_run=True)

    assert result == [{"task_id": "task-1", "status": "dry_run_success"}]
    result_body = fake_xquant.result_bodies[0]
    assert result_body["cash"] == 50_000
    assert result_body["total_asset"] == 100_000
    assert result_body["holdings"] == [
        {
            "symbol": "513100.SH",
            "shares": 50_000,
            "reference_price": 1.0,
            "market_value": 50_000,
            "weight": 0.5,
            "target_weight": 0.5,
        }
    ]


def test_gateway_mock_broker_records_reject_event(tmp_path) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            mock_submit_dry_run_orders=True,
            mock_order_behavior="reject",
            mock_total_asset=100_000,
            mock_cash=100_000,
            mock_prices={"513100.SH": 1.0},
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()
    service.xquant = fake_xquant  # type: ignore[assignment]

    result = service.poll_once(force_dry_run=True)

    assert result == [{"task_id": "task-1", "status": "failed"}]
    assert fake_xquant.results == [("task-1", "failed", 0, 0, 2)]
    with closing(service.storage._connect()) as conn:
        event_types = [
            row[0]
            for row in conn.execute("SELECT event_type FROM order_events ORDER BY id").fetchall()
        ]
    assert event_types == ["connected", "order_response", "order_error"]


def test_gateway_reports_plan_build_failure_with_xquant_result_shape(tmp_path) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            mock_total_asset=100_000,
            mock_cash=100_000,
            mock_prices={"513100.SH": 1.0},
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    fake_xquant = FakeXquant()

    def fetch_pending_tasks(account_id: str):
        return [
            RebalanceTask.model_validate(
                {
                    "task_id": "task-1",
                    "portfolio_id": "prod",
                    "account_id": account_id,
                    "mode": "dry_run",
                    "created_at": "2026-05-27T09:35:00+08:00",
                    "expires_at": None,
                    "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                    "constraints": {
                        "max_turnover_ratio": 0.1,
                        "max_single_order_amount": 50_000,
                        "min_order_amount": 1_000,
                    },
                }
            )
        ]

    fake_xquant.fetch_pending_tasks = fetch_pending_tasks  # type: ignore[method-assign]
    service.xquant = fake_xquant  # type: ignore[assignment]

    result = service.poll_once(force_dry_run=True)

    assert result == [
        {
            "task_id": "task-1",
            "status": "failed",
            "error": "turnover ratio required by task exceeds max_turnover_ratio 0.1000",
        }
    ]
    assert fake_xquant.results == [("task-1", "failed", 0, 0, 0)]
    assert fake_xquant.result_bodies[0]["mode"] == "dry_run"
    assert fake_xquant.result_bodies[0]["cash"] == 100_000
    assert fake_xquant.result_bodies[0]["total_asset"] == 100_000
    assert fake_xquant.result_bodies[0]["holdings"] == []
    assert fake_xquant.result_bodies[0]["errors"] == [
        "turnover ratio required by task exceeds max_turnover_ratio 0.1000"
    ]
