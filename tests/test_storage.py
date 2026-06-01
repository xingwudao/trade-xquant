from __future__ import annotations

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
