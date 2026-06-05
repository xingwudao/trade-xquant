from __future__ import annotations

import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from trade_xquant.condition_orders import ConditionAction, ConditionOrder
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
        self.condition_results: list[tuple[str, str, dict]] = []

    def report_result(self, task_id: str, status: str, payload) -> None:
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        self.results.append((task_id, status, body))

    def report_condition_result(
        self,
        source_task_id: str,
        condition_id: str,
        payload: dict,
    ) -> None:
        self.condition_results.append((source_task_id, condition_id, payload))


class FailingConditionXquant(FakeXquant):
    def report_condition_result(
        self,
        source_task_id: str,
        condition_id: str,
        payload: dict,
    ) -> None:
        raise XquantAdapterError(
            'Xquant API error 503: {"detail":"condition_result_unavailable"}',
            status_code=503,
        )


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


class ConditionRemarkBroker(FilledBroker):
    def get_orders(self):
        return [
            SimpleNamespace(
                stock_code="513100.SH",
                order_status=56,
                traded_volume=1000,
                price=1.0,
                m_strRemark="cond:cond-sync",
            )
        ]

    def get_trades(self):
        return [
            SimpleNamespace(
                stock_code="513100.SH",
                quantity=1000,
                price=1.0,
                amount=1000.0,
                m_strRemark="cond:cond-sync",
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


def submitted_condition_result() -> ExecutionResult:
    task_id = "condition:cond-sync"
    order = PlannedOrder(
        task_id=task_id,
        symbol="513100.SH",
        side="sell",
        quantity=1000,
        price=1.0,
        amount=1000.0,
    )
    submitted = SubmittedOrder(
        task_id=task_id,
        symbol="513100.SH",
        side="sell",
        quantity=1000,
        price=1.0,
        amount=1000.0,
        local_order_id="1082169287",
        status="submitted",
    )
    return ExecutionResult(
        task_id=task_id,
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


def test_sync_results_reports_condition_tasks_to_condition_result_endpoint(tmp_path) -> None:
    service = make_service_with_condition_result(tmp_path, result_status="submitted")

    result = service.sync_results(task_id="condition:cond-sync")

    assert result == [{"task_id": "condition:cond-sync", "status": "success"}]
    assert service.xquant.results == []  # type: ignore[attr-defined]
    assert service.xquant.condition_results[0][0] == "task-1"  # type: ignore[attr-defined]
    assert service.xquant.condition_results[0][1] == "cond-sync"  # type: ignore[attr-defined]
    payload = service.xquant.condition_results[0][2]  # type: ignore[attr-defined]
    assert payload["condition_task_id"] == "condition:cond-sync"
    assert payload["execution_result"]["status"] == "success"
    assert service.storage.get_condition_order("cond-sync").status == "completed"


def test_sync_results_matches_condition_tasks_by_cond_remark(tmp_path) -> None:
    service = make_service_with_condition_result(tmp_path, result_status="submitted")
    service.qmt = ConditionRemarkBroker()  # type: ignore[assignment]

    result = service.sync_results(task_id="condition:cond-sync")

    assert result == [{"task_id": "condition:cond-sync", "status": "success"}]
    payload = service.xquant.condition_results[0][2]  # type: ignore[attr-defined]
    assert payload["execution_result"]["status"] == "success"
    assert payload["execution_result"]["submitted_orders"][0]["status"] == "filled"


def test_sync_results_surfaces_condition_result_report_failure(tmp_path) -> None:
    service = make_service_with_condition_result(tmp_path, result_status="submitted")
    service.xquant = FailingConditionXquant()  # type: ignore[assignment]

    with pytest.raises(GatewaySyncReportError) as exc:
        service.sync_results(task_id="condition:cond-sync")

    assert exc.value.status_code == 503
    assert exc.value.results[0]["task_id"] == "condition:cond-sync"
    assert exc.value.results[0]["status"] == "success"
    assert exc.value.results[0]["xquant_synced"] is False
    assert exc.value.results[0]["status_code"] == 503
    stored = service.storage.get_condition_trigger_audit("condition:cond-sync")
    assert stored is not None
    assert stored["xquant_report_status"] == "failed"


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


def test_sync_results_refreshes_condition_reference_from_position_cost(tmp_path) -> None:
    service = make_service_with_result(
        tmp_path,
        broker=SnapshotBroker(),
        result=submitted_result(),
        result_status="submitted",
    )
    service.storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-position-cost",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="real",
                symbol="513100.SH",
                purpose="take_profit",
                method="trailing_pct",
                status="pending_reference",
                params={"trail_pct": 0.03, "activation_profit_pct": 0.05},
                raw={"reference": {"source": "position_cost_price"}},
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )

    service.sync_results(task_id="task-1")

    order = service.storage.get_condition_order("cond-position-cost")
    assert order.status == "armed"
    assert order.reference_price == 1.5
    assert order.high_water_price == 1.5
    with closing(service.storage._connect()) as conn:
        row = conn.execute(
            """
            SELECT event_type, payload_json
            FROM condition_order_events
            WHERE condition_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            ("cond-position-cost",),
        ).fetchone()
    assert row["event_type"] == "reference_updated"
    payload = json.loads(row["payload_json"])
    assert payload["reference"]["source"] == "position_cost_price"
    assert payload["reference"]["price"] == 1.5
    assert payload["reference"]["activation_price"] == pytest.approx(1.575)


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


def make_service_with_condition_result(tmp_path, *, result_status: str) -> GatewayService:
    result = submitted_condition_result()
    service = make_service_with_result(
        tmp_path,
        broker=FilledBroker(),
        result=result,
        result_status=result_status,
    )
    service.storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-sync",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="real",
                symbol="513100.SH",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )
    service.storage.update_condition_order_status("cond-sync", "triggered")
    service.storage.update_condition_order_status("cond-sync", "submitted")
    service.storage.record_condition_market_state(
        condition_id="cond-sync",
        symbol="513100.SH",
        latest_price=1.1,
        high_water_price=None,
        trigger_price=1.1,
        activated=True,
        activated_at=None,
        atr_value=None,
        hv_value=None,
        std_value=None,
        computed_at="2026-06-04T10:00:00+08:00",
        market_data_source="prices",
        state={
            "method": "static_pct",
            "purpose": "take_profit",
            "params": {"take_profit_pct": 0.1},
            "activation_price": None,
        },
    )
    return service


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
        task_id=result.task_id,
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
    service.storage.mark_task_result(result.task_id, result_status, payload)
    return service
