from __future__ import annotations

import argparse
import json

from trade_xquant.cli import _run_gateway_command, build_parser
from trade_xquant.daemon import GatewaySyncReportError


def test_mock_run_command_is_registered() -> None:
    args = build_parser().parse_args(["mock-run", "--task-id", "task-1"])

    assert args.command == "mock-run"
    assert args.task_id == "task-1"


def test_sync_results_command_is_registered() -> None:
    args = build_parser().parse_args(["sync-results", "--task-id", "task-1", "--status", "all"])

    assert args.command == "sync-results"
    assert args.task_id == "task-1"
    assert args.status == "all"


def test_gateway_command_prints_sync_report_error_results(capsys) -> None:
    error = GatewaySyncReportError(
        [
            {
                "task_id": "task-1",
                "status": "partial",
                "xquant_synced": False,
                "status_code": 409,
                "hint": "remote task is terminal",
            }
        ]
    )

    code = _run_gateway_command(lambda: raise_error(error))

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["status_code"] == 409
    assert payload["hint"] == "remote task is terminal"
    assert payload["results"][0]["status"] == "partial"


def raise_error(error):
    raise error
