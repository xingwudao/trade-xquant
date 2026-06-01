from __future__ import annotations

import argparse

from trade_xquant.cli import build_parser


def test_mock_run_command_is_registered() -> None:
    args = build_parser().parse_args(["mock-run", "--task-id", "task-1"])

    assert args.command == "mock-run"
    assert args.task_id == "task-1"
