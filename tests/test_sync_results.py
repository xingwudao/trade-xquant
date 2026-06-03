from __future__ import annotations

import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from trade_xquant.config import QmtConfig, RiskConfig, RuntimeConfig, Settings, XquantConfig
from trade_xquant.daemon import GatewayService, GatewaySyncReportError
from trade_xquant.models import (
    AccountSnapshot,
    ExecutionResult,
    OrderPlan,
    PlannedOrder,
    Position,
    RebalanceTask,
    SubmittedOrder,
)
from trade_xquant.xquant_adapter import XquantAdapterError


class FakeXquant:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, dict]] = []

    def report_result(self, task_id: str, status: str, payload) -> None:
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        self.results.append((task_id, status, body))


class ConflictXquant:
    def report_result(self, task_id: str, status: str, payload) -> None:
        raise XquantAdapterError(
            'Xquant API error 409: {"detail":"invalid_task_transition"}',
            status_code=409,
        )


class FilledBroker:
    def connect(self) -> None:
        return None

    def get_orders(self):
        return [
            SimpleNamespace(
                order_id=1082169287,
                stock_code="513100.SH",
                order_status=56,
                traded_volume=1000,
                price=1.0,
                m_strRemark="task-1",
            )
        ]

    def get_trades(self):
        return [
            SimpleNamespace(
                order_id=1082169287,
                stock_code="513100.SH",
                quantity=1000,
                price=1.0,
                amount=1000.0,
                m_strRemark="task-1",
            )
        ]


class MixedBroker:
    def connect(self) -> None:
        return None

    def get_orders(self):
        return [
            SimpleNamespace(
                order_id=1082169287,
                stock_code="510300.SH",
                order_status=56,
                traded_volume=1000,
                price=1.0,
                m_strRemark="task-1",
            ),
            SimpleNamespace(
                order_id=1082169288,
                stock_code="513100.SH",
                order_status=57,
                traded_volume=0,
                price=1.0,
                error_msg="停牌废单",
                m_strRemark="task-1",
            ),
        ]

    def get_trades(self):
        return [
            SimpleNamespace(
                order_id=1082169287,
                stock_code="510300.SH",
                quantity=1000,
                price=1.0,
                amount=1000.0,
                m_strRemark="task-1",
            )
        ]


class SnapshotBroker(FilledBroker):
    def get_account_snapshot(self):
        return AccountSnapshot(
            account_id="acct",
            total_asset=100_000,
            cash=98_000,
            market_value=2_000,
        )

    def get_positions(self):
        return [
            Position(
                symbol="513100.SH",
                quantity=1000,
                sellable_quantity=1000,
                market_value=2000,
                cost_price=1.5,
            )
        ]

    def get_prices(self, symbols):
        return {symbol: 2.0 for symbol in symbols}


def task() -> RebalanceTask:
    return RebalanceTask.model_validate(
        {
            "task_id": "task-1",
            "portfolio_id": "prod",
            "account_id": "acct",
            "mode": "real",
            "created_at": "2026-05-27T09:35:00+08:00",
            "expires_at": None,
            "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
        }
    )


def submitted_result() -> ExecutionResult:
    order = PlannedOrder(
        task_id="task-1",
        symbol="513100.SH",
        side="buy",
        quantity=1000,
        price=1.0,
        amount=1000.0,
    )
    submitted = SubmittedOrder(
        task_id="task-1",
        symbol="513100.SH",
        side="buy",
        quantity=1000,
        price=1.0,
        amount=1000.0,
        local_order_id="1082169287",
        status="submitted",
    )
    return ExecutionResult(
        task_id="task-1",
        status="submitted",
        mode="real",
        planned_orders=[order],
        submitted_orders=[submitted],
    )


def mixed_submitted_result() -> ExecutionResult:
    orders = [
        PlannedOrder(
            task_id="task-1",
            symbol="510300.SH",
            side="buy",
            quantity=1000,
            price=1.0,
            amount=1000.0,
        ),
        PlannedOrder(
            task_id="task-1",
            symbol="513100.SH",
            side="buy",
            quantity=1000,
            price=1.0,
            amount=1000.0,
        ),
    ]
    submitted_orders = [
        SubmittedOrder(
            task_id="task-1",
            symbol="510300.SH",
            side="buy",
            quantity=1000,
            price=1.0,
            amount=1000.0,
            local_order_id="1082169287",
            status="submitted",
        ),
        SubmittedOrder(
            task_id="task-1",
            symbol="513100.SH",
            side="buy",
            quantity=1000,
            price=1.0,
            amount=1000.0,
            local_order_id="1082169288",
            status="submitted",
        ),
    ]
    return ExecutionResult(
        task_id="task-1",
        status="submitted",
        mode="real",
        planned_orders=orders,
        submitted_orders=submitted_orders,
    )


def test_sync_results_reports_success_when_qmt_orders_are_fully_traded(tmp_path) -> None:
    service = make_service_with_submitted_task(tmp_path, result_status="submitted")

    result = service.sync_results(task_id="task-1")

    assert result == [{"task_id": "task-1", "status": "success"}]
    assert service.xquant.results[0][0] == "task-1"  # type: ignore[attr-defined]
    assert service.xquant.results[0][1] == "success"  # type: ignore[attr-defined]
    body = service.xquant.results[0][2]  # type: ignore[attr-defined]
    assert body["status"] == "success"
    assert body["submitted_orders"][0]["local_order_id"] == "1082169287"
    assert body["trades"][0]["order_id"] == "1082169287"
    assert body["trades"][0]["symbol"] == "513100.SH"


def test_sync_results_can_refresh_previously_successful_task(tmp_path) -> None:
    service = make_service_with_submitted_task(tmp_path, result_status="success")

    result = service.sync_results()

    assert result == [{"task_id": "task-1", "status": "success"}]
    assert service.xquant.results[0][1] == "success"  # type: ignore[attr-defined]


def test_sync_results_preserves_current_account_snapshot_in_reported_result(tmp_path) -> None:
    service = make_service_with_result(
        tmp_path,
        broker=SnapshotBroker(),
        result=submitted_result(),
        result_status="submitted",
    )

    service.sync_results(task_id="task-1")

    body = service.xquant.results[0][2]  # type: ignore[attr-defined]
    assert body["cash"] == 98_000
    assert body["total_asset"] == 100_000
    assert body["holdings"] == [
        {
            "symbol": "513100.SH",
            "shares": 1000,
            "reference_price": 2.0,
            "market_value": 2000,
            "weight": 0.02,
            "target_weight": 0.5,
        }
    ]


def test_sync_results_reports_partial_when_some_orders_fill_and_some_fail(tmp_path) -> None:
    service = make_service_with_result(
        tmp_path,
        broker=MixedBroker(),
        result=mixed_submitted_result(),
        result_status="submitted",
    )

    result = service.sync_results(task_id="task-1")

    assert result == [{"task_id": "task-1", "status": "partial"}]
    assert service.xquant.results[0][1] == "partial"  # type: ignore[attr-defined]
    body = service.xquant.results[0][2]  # type: ignore[attr-defined]
    assert body["status"] == "partial"
    assert [(order["symbol"], order["status"]) for order in body["submitted_orders"]] == [
        ("510300.SH", "filled"),
        ("513100.SH", "failed"),
    ]
    assert body["trades"][0]["symbol"] == "510300.SH"
    assert body["errors"] == ["513100.SH buy failed: 停牌废单"]
    assert body["meta"]["sync_summary"] == {
        "filled_orders": [
            {
                "symbol": "510300.SH",
                "side": "buy",
                "quantity": 1000,
                "traded_quantity": 1000,
                "local_order_id": "1082169287",
                "broker_order_id": None,
            }
        ],
        "failed_orders": [
            {
                "symbol": "513100.SH",
                "side": "buy",
                "quantity": 1000,
                "traded_quantity": 0,
                "local_order_id": "1082169288",
                "broker_order_id": None,
                "error": "停牌废单",
            }
        ],
        "pending_orders": [],
    }


def test_sync_results_reports_xquant_conflict_without_losing_local_partial_result(tmp_path) -> None:
    service = make_service_with_result(
        tmp_path,
        broker=MixedBroker(),
        result=mixed_submitted_result(),
        result_status="failed",
    )
    service.xquant = ConflictXquant()  # type: ignore[assignment]

    with pytest.raises(GatewaySyncReportError) as exc:
        service.sync_results(task_id="task-1")

    assert exc.value.status_code == 409
    assert exc.value.results[0]["task_id"] == "task-1"
    assert exc.value.results[0]["status"] == "partial"
    assert exc.value.results[0]["xquant_synced"] is False
    assert exc.value.results[0]["status_code"] == 409
    assert exc.value.results[0]["hint"] is not None
    assert exc.value.results[0]["sync_summary"]["filled_orders"][0]["symbol"] == "510300.SH"
    assert exc.value.results[0]["sync_summary"]["failed_orders"][0]["symbol"] == "513100.SH"

    with closing(service.storage._connect()) as conn:
        row = conn.execute(
            "SELECT status, payload_json FROM task_results WHERE task_id=?",
            ("task-1",),
        ).fetchone()
    payload = json.loads(row["payload_json"])
    assert row["status"] == "partial"
    assert payload["meta"]["sync_summary"]["failed_orders"][0]["error"] == "停牌废单"


def make_service_with_submitted_task(tmp_path, *, result_status: str) -> GatewayService:
    return make_service_with_result(
        tmp_path,
        broker=FilledBroker(),
        result=submitted_result(),
        result_status=result_status,
    )


def make_service_with_result(
    tmp_path,
    *,
    broker,
    result: ExecutionResult,
    result_status: str,
) -> GatewayService:
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
    service.qmt = broker  # type: ignore[assignment]
    service.xquant = FakeXquant()  # type: ignore[assignment]
    service.storage.initialize()
    service.storage.record_task_received(task(), status="submitted")
    plan = OrderPlan(
        task_id="task-1",
        account_id="acct",
        total_asset=100_000,
        turnover_amount=1000,
        turnover_ratio=0.01,
        orders=result.planned_orders,
    )
    service.storage.record_plan(plan)
    service.storage.record_execution_result(result)
    payload = result.model_dump(mode="json")
    payload["status"] = result_status
    service.storage.mark_task_result("task-1", result_status, payload)
    return service
