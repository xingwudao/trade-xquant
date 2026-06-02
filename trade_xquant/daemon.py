from __future__ import annotations

import logging
import time
from typing import Any

from trade_xquant.config import Settings
from trade_xquant.execution_engine import ExecutionEngine
from trade_xquant.models import AccountSnapshot, ExecutionResult, Position, RebalanceTask
from trade_xquant.mock_qmt_adapter import MockBrokerAdapter
from trade_xquant.portfolio_engine import PortfolioEngine
from trade_xquant.qmt_adapter import QmtAdapter
from trade_xquant.risk_control import RiskControl
from trade_xquant.storage import Storage
from trade_xquant.xquant_adapter import XquantAdapter

logger = logging.getLogger(__name__)


class GatewayService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = Storage(settings.runtime.db_path)
        self.xquant = XquantAdapter(
            settings.xquant.base_url,
            api_token=settings.xquant.api_token,
            timeout_seconds=settings.xquant.timeout_seconds,
            trust_env=settings.xquant.trust_env,
        )
        if settings.runtime.broker_adapter == "mock":
            self.qmt = MockBrokerAdapter(
                account_id=settings.qmt.account_id,
                total_asset=settings.runtime.mock_total_asset,
                cash=settings.runtime.mock_cash,
                prices=settings.runtime.mock_prices,
                order_behavior=settings.runtime.mock_order_behavior,
                partial_fill_ratio=settings.runtime.mock_partial_fill_ratio,
                event_handler=self._record_qmt_event,
            )
        elif settings.runtime.broker_adapter == "qmt":
            self.qmt = QmtAdapter(settings.qmt, event_handler=self._record_qmt_event)
        else:
            raise ValueError("runtime.broker_adapter must be 'qmt' or 'mock'")
        self.portfolio = PortfolioEngine()
        self.risk = RiskControl(settings)

    def poll_once(self, force_dry_run: bool = False, task_id: str | None = None) -> list[dict[str, object]]:
        self.storage.initialize()
        using_signal_endpoint = bool(self.settings.xquant.product_code)
        if self.settings.xquant.product_code:
            signal_task = self.xquant.fetch_latest_signal_task(
                product_code=self.settings.xquant.product_code,
                account_id=self.settings.qmt.account_id,
                mode="dry_run" if force_dry_run or self.settings.runtime.dry_run_default else "real",
                cash_buffer_ratio=self.settings.risk.cash_buffer_ratio,
            )
            tasks = [signal_task] if signal_task else []
        else:
            tasks = self.xquant.fetch_pending_tasks(self.settings.qmt.account_id)
        if task_id:
            tasks = [task for task in tasks if task.task_id == task_id]
        results: list[dict[str, object]] = []
        if not tasks:
            logger.info("no pending tasks")
            return results

        self.qmt.connect()
        account = self.qmt.get_account_snapshot()
        positions = self.qmt.get_positions()
        for task in tasks:
            if force_dry_run:
                task.mode = "dry_run"
            prices: dict[str, float] = {}
            try:
                self.storage.claim_task(task)
                prices = self.qmt.get_prices([target.symbol for target in task.targets] + [p.symbol for p in positions])
                plan = self.portfolio.build_plan(task, account, positions, prices)
                self.risk.validate(task, account, plan, known_symbols=set(prices))
                self.storage.record_plan(plan)
                if not using_signal_endpoint:
                    self.xquant.report_plan(task.task_id, plan.model_dump(mode="json"))
                result = ExecutionEngine(self.qmt, self.settings.runtime).execute(plan, task.mode)
                attach_account_snapshot(result, account, positions, prices, task)
                self.storage.record_execution_result(result)
                status = result.status if result.status in {"dry_run_success", "submitted"} else "failed"
                self.storage.mark_task_result(task.task_id, status, result.model_dump(mode="json"))
                if not using_signal_endpoint:
                    self.xquant.report_result(task.task_id, status, result)
                results.append({"task_id": task.task_id, "status": status})
            except Exception as exc:  # noqa: BLE001 - each task must be audited
                logger.exception("task failed: %s", task.task_id)
                payload = {
                    "mode": task.mode,
                    "planned_orders": [],
                    "submitted_orders": [],
                    "trades": [],
                    "events": [],
                    **account_result_snapshot(account, positions, prices, task),
                    "errors": [str(exc)],
                    "meta": {"error": str(exc)},
                }
                self.storage.mark_task_result(task.task_id, "failed", payload)
                if not using_signal_endpoint:
                    try:
                        self.xquant.report_result(task.task_id, "failed", payload)
                    except Exception:
                        logger.exception("failed to report task failure to Xquant")
                results.append({"task_id": task.task_id, "status": "failed", "error": str(exc)})
        return results

    def run_forever(self) -> None:
        while True:
            self.poll_once()
            time.sleep(self.settings.runtime.poll_interval_seconds)

    def _record_qmt_event(self, event) -> None:
        self.storage.record_order_event(
            event.event_type,
            event.payload,
            task_id=event.payload.get("task_id"),
            order_id=event.order_id,
            symbol=event.symbol,
        )
        if event.event_type == "stock_trade":
            self.storage.record_trade_event(event.payload, order_id=event.order_id, symbol=event.symbol)


def attach_account_snapshot(
    result: ExecutionResult,
    account: AccountSnapshot,
    positions: list[Position],
    prices: dict[str, float],
    task: RebalanceTask,
) -> None:
    snapshot = account_result_snapshot(account, positions, prices, task)
    result.cash = snapshot["cash"]
    result.total_asset = snapshot["total_asset"]
    result.holdings = snapshot["holdings"]


def account_result_snapshot(
    account: AccountSnapshot,
    positions: list[Position],
    prices: dict[str, float],
    task: RebalanceTask,
) -> dict[str, Any]:
    target_weights = {target.symbol: target.target_weight for target in task.targets}
    holdings = []
    for position in positions:
        reference_price = prices.get(position.symbol)
        market_value = position.market_value
        if not market_value and reference_price is not None:
            market_value = position.quantity * reference_price
        weight = market_value / account.total_asset if account.total_asset > 0 else None
        holdings.append(
            {
                "symbol": position.symbol,
                "shares": position.quantity,
                "reference_price": reference_price,
                "market_value": market_value,
                "weight": weight,
                "target_weight": target_weights.get(position.symbol),
            }
        )
    return {"cash": account.cash, "total_asset": account.total_asset, "holdings": holdings}
