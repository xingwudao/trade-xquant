from __future__ import annotations

import json

from trade_xquant.condition_orders import extract_condition_orders
from trade_xquant.local_task_file import LocalTaskFileAdapter


def test_local_task_file_adapter_parses_tasks_and_condition_orders(tmp_path) -> None:
    task_file = tmp_path / "tasks.json"
    task_file.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "portfolio_id": "prod",
                        "account_id": "acct",
                        "mode": "dry_run",
                        "created_at": "2026-06-03T09:35:00+08:00",
                        "expires_at": None,
                        "targets": [{"symbol": "513100.SS", "target_weight": 0.5}],
                        "constraints": {
                            "max_turnover_ratio": 0.8,
                            "condition_orders": [
                                {
                                    "condition_id": "cond-1",
                                    "symbol": "513100.SS",
                                    "purpose": "stop_loss",
                                    "method": "static_pct",
                                    "reference_price": 1.0,
                                    "params": {"stop_loss_pct": 0.05},
                                    "action": {"type": "sell_pct", "pct": 1.0},
                                }
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    adapter = LocalTaskFileAdapter(task_file)

    tasks = adapter.fetch_pending_tasks("acct")

    assert len(tasks) == 1
    assert tasks[0].task_id == "task-1"
    assert tasks[0].targets[0].symbol == "513100.SH"
    assert tasks[0].raw["constraints"]["condition_orders"][0]["symbol"] == "513100.SS"


def test_local_task_file_adapter_preserves_new_condition_methods(tmp_path) -> None:
    task_file = tmp_path / "tasks.json"
    task_file.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "portfolio_id": "prod",
                        "account_id": "acct",
                        "mode": "dry_run",
                        "created_at": "2026-06-03T09:35:00+08:00",
                        "expires_at": None,
                        "targets": [{"symbol": "513100.SS", "target_weight": 0.5}],
                        "constraints": {
                            "condition_orders": [
                                {
                                    "condition_id": "cond-atr",
                                    "symbol": "513100.SS",
                                    "purpose": "stop_loss",
                                    "method": "atr_trailing",
                                    "reference_price": 1.0,
                                    "params": {
                                        "atr_window": 3,
                                        "atr_multiple": 2.0,
                                        "bar_interval": "1d",
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    adapter = LocalTaskFileAdapter(task_file)

    tasks = adapter.fetch_pending_tasks("acct")
    orders = extract_condition_orders(tasks[0])

    assert orders[0].condition_id == "cond-atr"
    assert orders[0].symbol == "513100.SH"
    assert orders[0].method == "atr_trailing"


def test_local_task_file_adapter_accepts_top_level_task_list(tmp_path) -> None:
    task_file = tmp_path / "tasks.json"
    task_file.write_text(
        json.dumps(
            [
                {
                    "task_id": "task-1",
                    "portfolio_id": "prod",
                    "account_id": "acct",
                    "mode": "dry_run",
                    "created_at": "2026-06-03T09:35:00+08:00",
                    "expires_at": None,
                    "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                }
            ]
        ),
        encoding="utf-8",
    )

    adapter = LocalTaskFileAdapter(task_file)

    assert [task.task_id for task in adapter.fetch_pending_tasks("acct")] == ["task-1"]
