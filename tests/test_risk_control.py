from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trade_xquant.config import RiskConfig, Settings, XquantConfig, QmtConfig, RuntimeConfig
from trade_xquant.models import AccountSnapshot, OrderPlan, PlannedOrder, RebalanceTask, TargetPosition
from trade_xquant.risk_control import RiskControl, RiskError


def settings(real_order: bool = False) -> Settings:
    return Settings(
        xquant=XquantConfig(base_url="http://xquant.local", api_token="token"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(allow_real_order=real_order, dry_run_default=True),
        risk=RiskConfig(max_single_order_amount=50_000, max_turnover_ratio=0.8),
    )


def task(**kwargs) -> RebalanceTask:
    data = {
        "task_id": "task-1",
        "portfolio_id": "demo",
        "account_id": "acct",
        "mode": "dry_run",
        "created_at": "2026-05-27T09:35:00+08:00",
        "expires_at": "2026-05-27T14:50:00+08:00",
        "targets": [TargetPosition(symbol="513100.SH", target_weight=0.5)],
    }
    data.update(kwargs)
    return RebalanceTask.model_validate(data)


def plan(amount: float = 10_000, turnover_ratio: float = 0.1) -> OrderPlan:
    return OrderPlan(
        task_id="task-1",
        account_id="acct",
        total_asset=100_000,
        turnover_amount=turnover_ratio * 100_000,
        turnover_ratio=turnover_ratio,
        orders=[
            PlannedOrder(
                task_id="task-1",
                symbol="513100.SH",
                side="buy",
                quantity=1000,
                price=amount / 1000,
                amount=amount,
            )
        ],
    )


def test_real_order_requires_config_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADE_XQUANT_ENABLE_REAL_ORDER", raising=False)
    rc = RiskControl(settings(real_order=True))

    with pytest.raises(RiskError, match="real order"):
        rc.validate(
            task(mode="real"),
            AccountSnapshot(account_id="acct", total_asset=1, cash=1),
            plan(),
            now=datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    RiskControl(settings(real_order=True)).validate(
        task(mode="real"),
        AccountSnapshot(account_id="acct", total_asset=1, cash=1),
        plan(),
        now=datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def test_account_mismatch_rejected() -> None:
    with pytest.raises(RiskError, match="account"):
        RiskControl(settings()).validate(
            task(account_id="other"),
            AccountSnapshot(account_id="acct", total_asset=1, cash=1),
            plan(),
        )


def test_expired_task_rejected() -> None:
    with pytest.raises(RiskError, match="expired"):
        RiskControl(settings()).validate(
            task(),
            AccountSnapshot(account_id="acct", total_asset=1, cash=1),
            plan(),
            now=datetime(2026, 5, 27, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )


def test_order_amount_threshold_rejected() -> None:
    with pytest.raises(RiskError, match="single order"):
        RiskControl(settings()).validate(
            task(),
            AccountSnapshot(account_id="acct", total_asset=1, cash=1),
            plan(amount=60_000),
            now=datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )


def test_real_order_outside_trading_session_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    with pytest.raises(RiskError, match="trading session"):
        RiskControl(settings(real_order=True)).validate(
            task(mode="real"),
            AccountSnapshot(account_id="acct", total_asset=1, cash=1),
            plan(),
            now=datetime(2026, 5, 27, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
