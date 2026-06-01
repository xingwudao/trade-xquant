from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trade_xquant.config import RiskConfig, RuntimeConfig, Settings, XquantConfig, QmtConfig
from trade_xquant.models import AccountSnapshot, OrderPlan, PlannedOrder, RebalanceTask, TargetPosition
from trade_xquant.risk_control import RiskControl
from trade_xquant.execution_engine import ExecutionEngine


def test_risk_control_allows_null_expires_at_task() -> None:
    task = RebalanceTask.model_validate(
        {
            "task_id": "task-1",
            "portfolio_id": "prod",
            "account_id": "acct",
            "mode": "dry_run",
            "created_at": "2026-05-27T09:35:00+08:00",
            "expires_at": None,
            "targets": [TargetPosition(symbol="513100.SH", target_weight=0.5)],
        }
    )
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(),
        risk=RiskConfig(),
    )
    plan = OrderPlan(
        task_id="task-1",
        account_id="acct",
        total_asset=100_000,
        turnover_amount=10_000,
        turnover_ratio=0.1,
        orders=[
            PlannedOrder(
                task_id="task-1",
                symbol="513100.SH",
                side="buy",
                quantity=1000,
                price=10,
                amount=10_000,
            )
        ],
    )

    RiskControl(settings).validate(
        task,
        AccountSnapshot(account_id="acct", total_asset=100_000, cash=100_000),
        plan,
        now=datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def test_dry_run_execution_uses_server_status_name() -> None:
    plan = OrderPlan(
        task_id="task-1",
        account_id="acct",
        total_asset=100_000,
        turnover_amount=0,
        turnover_ratio=0,
        orders=[],
    )

    result = ExecutionEngine(broker=None, runtime=RuntimeConfig()).execute(plan, "dry_run")

    assert result.status == "dry_run_success"
