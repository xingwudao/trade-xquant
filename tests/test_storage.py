from __future__ import annotations

import sqlite3

import pytest

from trade_xquant.models import RebalanceTask, TargetPosition
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
    assert state["symbol"] == "513100.SH"
    assert state["latest_price"] == 1.23
    assert state["activated"] is True
    assert state["state"]["reason"] == "test"


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
    assert audit["source_task_id"] == "task-1"
    assert audit["rule"]["params"]["take_profit_pct"] == 0.1
    assert audit["xquant_report_status"] == "failed"
    assert audit["xquant_report_error"] == "http 409"


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
