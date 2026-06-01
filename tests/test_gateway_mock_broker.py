from __future__ import annotations

from trade_xquant.config import QmtConfig, RiskConfig, RuntimeConfig, Settings, XquantConfig
from trade_xquant.daemon import GatewayService
from trade_xquant.models import RebalanceTask


class FakeXquant:
    def __init__(self) -> None:
        self.plans: list[dict] = []
        self.results: list[tuple[str, str, int, int, int]] = []
        self.result_bodies: list[dict] = []

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
    with service.storage._connect() as conn:
        submitted_count = conn.execute("SELECT COUNT(*) FROM submitted_orders").fetchone()[0]
        event_types = [
            row[0]
            for row in conn.execute("SELECT event_type FROM order_events ORDER BY id").fetchall()
        ]
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert submitted_count == 1
    assert event_types == ["connected", "order_response", "stock_order", "stock_trade"]
    assert trade_count == 1


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
    with service.storage._connect() as conn:
        event_types = [
            row[0]
            for row in conn.execute("SELECT event_type FROM order_events ORDER BY id").fetchall()
        ]
    assert event_types == ["connected", "order_response", "order_error"]
