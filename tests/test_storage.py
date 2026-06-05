from __future__ import annotations

import sqlite3

import pytest

from trade_xquant.condition_orders import ConditionAction, ConditionOrder
from trade_xquant.models import ExecutionResult, RebalanceTask, SubmittedOrder, TargetPosition
from trade_xquant.storage import Storage


def task(task_id: str = "task-1") -> RebalanceTask:
    return RebalanceTask.model_validate(
        {
            "task_id": task_id,
            "portfolio_id": "demo",
            "account_id": "acct",
            "mode": "dry_run",
            "created_at": "2026-05-27T09:35:00+08:00",
            "expires_at": "2026-05-27T14:50:00+08:00",
            "targets": [TargetPosition(symbol="513100.SH", target_weight=0.5)],
        }
    )


def test_task_id_is_unique_and_terminal_tasks_are_not_reprocessed(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    storage.record_task_received(task())
    storage.mark_task_result("task-1", "success", {"ok": True})

    assert storage.is_terminal_task("task-1") is True
    with pytest.raises(ValueError, match="terminal"):
        storage.claim_task(task())


def test_reset_allows_reprocessing(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.record_task_received(task())
    storage.mark_task_result("task-1", "failed", {"reason": "x"})

    storage.reset_task("task-1")

    assert storage.is_terminal_task("task-1") is False
    storage.claim_task(task())


def test_dry_run_success_and_submitted_are_terminal(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    storage.record_task_received(task("dry-task"))
    storage.mark_task_result("dry-task", "dry_run_success", {"ok": True})
    storage.record_task_received(task("submitted-task"))
    storage.mark_task_result("submitted-task", "submitted", {"ok": True})

    assert storage.is_terminal_task("dry-task") is True
    assert storage.is_terminal_task("submitted-task") is True


def test_storage_closes_sqlite_connections_after_operations(tmp_path, monkeypatch) -> None:
    original_connect = sqlite3.connect
    connections = []

    class TrackingConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.closed = False

        def close(self) -> None:
            self.closed = True
            super().close()

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=TrackingConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr("trade_xquant.storage.sqlite3.connect", connect)
    storage = Storage(tmp_path / "audit.db")

    storage.initialize()

    assert connections
    assert all(connection.closed for connection in connections)


def test_condition_market_state_roundtrip(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    storage.record_condition_market_state(
        condition_id="cond-1",
        symbol="513100.SH",
        latest_price=1.23,
        high_water_price=1.4,
        trigger_price=1.18,
        activated=True,
        activated_at="2026-06-03T10:00:00+08:00",
        atr_value=0.03,
        hv_value=None,
        std_value=None,
        computed_at="2026-06-03T10:30:00+08:00",
        market_data_source="mock",
        state={"reason": "test"},
    )

    state = storage.get_condition_market_state("cond-1")

    assert state is not None
    assert set(state) == {
        "condition_id",
        "symbol",
        "latest_price",
        "high_water_price",
        "trigger_price",
        "activated",
        "activated_at",
        "atr_value",
        "hv_value",
        "std_value",
        "computed_at",
        "market_data_source",
        "activation_price",
        "state",
    }
    assert state["condition_id"] == "cond-1"
    assert state["symbol"] == "513100.SH"
    assert state["latest_price"] == 1.23
    assert state["high_water_price"] == 1.4
    assert state["trigger_price"] == 1.18
    assert state["activated"] is True
    assert state["activated_at"] == "2026-06-03T10:00:00+08:00"
    assert state["atr_value"] == 0.03
    assert state["hv_value"] is None
    assert state["std_value"] is None
    assert state["computed_at"] == "2026-06-03T10:30:00+08:00"
    assert state["market_data_source"] == "mock"
    assert state["activation_price"] is None
    assert state["state"] == {"reason": "test"}


def test_condition_market_state_upsert_updates_existing_row(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    storage.record_condition_market_state(
        condition_id="cond-1",
        symbol="513100.SH",
        latest_price=1.23,
        high_water_price=1.4,
        trigger_price=1.18,
        activated=True,
        activated_at="2026-06-03T10:00:00+08:00",
        atr_value=0.03,
        hv_value=None,
        std_value=None,
        computed_at="2026-06-03T10:30:00+08:00",
        market_data_source="mock",
        state={"reason": "initial"},
    )
    storage.record_condition_market_state(
        condition_id="cond-1",
        symbol="513100.SH",
        latest_price=1.25,
        high_water_price=1.42,
        trigger_price=1.2,
        activated=False,
        activated_at=None,
        atr_value=None,
        hv_value=0.2,
        std_value=0.1,
        computed_at="2026-06-03T10:35:00+08:00",
        market_data_source="mock-v2",
        state={"reason": "updated"},
    )

    state = storage.get_condition_market_state("cond-1")

    assert state is not None
    assert state["latest_price"] == 1.25
    assert state["high_water_price"] == 1.42
    assert state["activated"] is False
    assert state["activated_at"] is None
    assert state["activation_price"] is None
    assert state["market_data_source"] == "mock-v2"
    assert state["state"] == {"reason": "updated"}


def test_condition_trigger_audit_roundtrip_and_report_status(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    storage.record_condition_trigger_audit(
        condition_id="cond-1",
        source_task_id="task-1",
        condition_task_id="condition:cond-1",
        symbol="513100.SH",
        purpose="take_profit",
        method="static_pct",
        rule={"params": {"take_profit_pct": 0.1}},
        market_state={"latest_price": 1.1},
        trigger={"reason": "latest_price >= trigger_price"},
        execution_result={"status": "dry_run_success"},
    )
    storage.update_condition_audit_report_status("condition:cond-1", "failed", "http 409")

    audit = storage.get_condition_trigger_audit("condition:cond-1")

    assert audit is not None
    assert set(audit) == {
        "condition_task_id",
        "condition_id",
        "source_task_id",
        "symbol",
        "purpose",
        "method",
        "xquant_report_status",
        "xquant_report_error",
        "created_at",
        "updated_at",
        "rule",
        "market_state",
        "trigger",
        "execution_result",
    }
    assert audit["condition_task_id"] == "condition:cond-1"
    assert audit["condition_id"] == "cond-1"
    assert audit["source_task_id"] == "task-1"
    assert audit["symbol"] == "513100.SH"
    assert audit["purpose"] == "take_profit"
    assert audit["method"] == "static_pct"
    assert audit["rule"] == {"params": {"take_profit_pct": 0.1}}
    assert audit["market_state"] == {"latest_price": 1.1}
    assert audit["trigger"] == {"reason": "latest_price >= trigger_price"}
    assert audit["execution_result"] == {"status": "dry_run_success"}
    assert audit["xquant_report_status"] == "failed"
    assert audit["xquant_report_error"] == "http 409"
    assert audit["created_at"]
    assert audit["updated_at"]


def test_submitting_condition_orders_are_not_active_retry_candidates(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-submitting",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="real",
                symbol="513100.SH",
                purpose="take_profit",
                method="static_pct",
                status="submitting",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            )
        ]
    )

    assert storage.list_active_condition_orders() == []


def test_new_condition_task_ids_are_not_active_retry_candidates(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond-result",
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
            ),
            ConditionOrder(
                condition_id="cond-submitted",
                task_id="task-1",
                portfolio_id="prod",
                account_id="acct",
                mode="real",
                symbol="159915.SZ",
                purpose="take_profit",
                method="static_pct",
                reference_price=1.0,
                params={"take_profit_pct": 0.1},
                action=ConditionAction(type="sell_pct", pct=1.0),
            ),
        ]
    )
    storage.mark_task_result("condition:task-1:cond-result", "submitted", {"ok": True})
    storage.record_execution_result(
        ExecutionResult(
            task_id="condition:task-1:cond-submitted",
            status="submitted",
            mode="real",
            planned_orders=[],
            submitted_orders=[
                SubmittedOrder(
                    task_id="condition:task-1:cond-submitted",
                    symbol="159915.SZ",
                    side="sell",
                    quantity=100,
                    price=1.0,
                    amount=100.0,
                    status="submitted",
                )
            ],
        )
    )

    assert storage.list_active_condition_orders() == []


def test_condition_task_id_lookup_preserves_colons_in_condition_id(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders(
        [
            ConditionOrder(
                condition_id="cond:with:colon",
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

    assert (
        storage.find_condition_id_for_condition_task_id(
            "condition:task-1:cond:with:colon"
        )
        == "cond:with:colon"
    )


def test_condition_trigger_audit_rerecord_updates_payloads_and_preserves_created_at(
    tmp_path, monkeypatch
) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    timestamps = iter(
        [
            "2026-06-03T02:00:00+00:00",
            "2026-06-03T02:01:00+00:00",
            "2026-06-03T02:02:00+00:00",
        ]
    )
    monkeypatch.setattr("trade_xquant.storage.utc_now", lambda: next(timestamps))

    storage.record_condition_trigger_audit(
        condition_id="cond-1",
        source_task_id="task-1",
        condition_task_id="condition:cond-1",
        symbol="513100.SH",
        purpose="take_profit",
        method="static_pct",
        rule={"params": {"take_profit_pct": 0.1}},
        market_state={"latest_price": 1.1},
        trigger={"reason": "latest_price >= trigger_price"},
        execution_result={"status": "dry_run_success"},
    )
    storage.update_condition_audit_report_status("condition:cond-1", "failed", "http 409")

    storage.record_condition_trigger_audit(
        condition_id="cond-2",
        source_task_id="task-2",
        condition_task_id="condition:cond-1",
        symbol="159915.SZ",
        purpose="stop_loss",
        method="trailing_pct",
        rule={"params": {"trail_pct": 0.08}},
        market_state={"latest_price": 0.9, "high_water_price": 1.2},
        trigger={"reason": "latest_price <= trigger_price"},
        execution_result={"status": "submitted"},
    )

    audit = storage.get_condition_trigger_audit("condition:cond-1")

    assert audit is not None
    assert audit["condition_id"] == "cond-2"
    assert audit["source_task_id"] == "task-2"
    assert audit["symbol"] == "159915.SZ"
    assert audit["purpose"] == "stop_loss"
    assert audit["method"] == "trailing_pct"
    assert audit["rule"] == {"params": {"trail_pct": 0.08}}
    assert audit["market_state"] == {"latest_price": 0.9, "high_water_price": 1.2}
    assert audit["trigger"] == {"reason": "latest_price <= trigger_price"}
    assert audit["execution_result"] == {"status": "submitted"}
    assert audit["xquant_report_status"] == "pending"
    assert audit["xquant_report_error"] is None
    assert audit["created_at"] == "2026-06-03T02:00:00+00:00"
    assert audit["updated_at"] == "2026-06-03T02:02:00+00:00"


def test_condition_trigger_audit_report_status_update_preserves_payloads(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    storage.record_condition_trigger_audit(
        condition_id="cond-1",
        source_task_id="task-1",
        condition_task_id="condition:cond-1",
        symbol="513100.SH",
        purpose="take_profit",
        method="static_pct",
        rule={"params": {"take_profit_pct": 0.1}},
        market_state={"latest_price": 1.1},
        trigger={"reason": "latest_price >= trigger_price"},
        execution_result={"status": "dry_run_success"},
    )
    before = storage.get_condition_trigger_audit("condition:cond-1")
    assert before is not None

    storage.update_condition_audit_report_status("condition:cond-1", "reported")
    after = storage.get_condition_trigger_audit("condition:cond-1")

    assert after is not None
    assert after["rule"] == before["rule"]
    assert after["market_state"] == before["market_state"]
    assert after["trigger"] == before["trigger"]
    assert after["execution_result"] == before["execution_result"]
    assert after["xquant_report_status"] == "reported"
    assert after["xquant_report_error"] is None
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] >= before["updated_at"]


def test_condition_state_and_audit_getters_return_none_for_missing_keys(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    assert storage.get_condition_market_state("missing") is None
    assert storage.get_condition_trigger_audit("condition:missing") is None


def test_condition_audit_report_status_update_missing_raises(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    with pytest.raises(KeyError) as exc:
        storage.update_condition_audit_report_status("condition:missing", "failed")

    assert exc.value.args == ("condition:missing",)
