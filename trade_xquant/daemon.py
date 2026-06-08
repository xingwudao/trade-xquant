from __future__ import annotations

import logging
import math
import socket
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from trade_xquant import __version__
from trade_xquant.condition_orders import (
    ConditionEngine,
    ConditionOrder,
    TriggeredConditionPlan,
    extract_condition_orders,
)
from trade_xquant.config import Settings
from trade_xquant.execution_engine import ExecutionEngine
from trade_xquant.heartbeat import (
    account_result_snapshot,
    append_heartbeat_error,
    check_qmt_connection_for_heartbeat,
    xtquant_importable,
)
from trade_xquant.local_task_file import LocalTaskFileAdapter
from trade_xquant.models import (
    AccountSnapshot,
    ExecutionResult,
    OrderPlan,
    Position,
    RebalanceTask,
    TargetPosition,
)
from trade_xquant.mock_qmt_adapter import MockBrokerAdapter
from trade_xquant.portfolio_engine import PortfolioEngine
from trade_xquant.qmt_adapter import QmtAdapter, compact_obj
from trade_xquant.risk_control import RiskControl, RiskError
from trade_xquant.storage import Storage
from trade_xquant.xquant_adapter import XquantAdapter, XquantAdapterError

logger = logging.getLogger(__name__)

_CONDITION_REARM_BLOCKING_STATUSES = {
    "triggered",
    "submitting",
    "submitted",
    "needs_reconcile",
}


class GatewaySyncReportError(RuntimeError):
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.results = results
        failed_results = [result for result in results if result.get("xquant_synced") is False]
        status_codes = [
            result.get("status_code")
            for result in failed_results
            if result.get("status_code") is not None
        ]
        self.status_code = status_codes[0] if len(set(status_codes)) == 1 else None
        self.hint = next(
            (str(result["hint"]) for result in failed_results if result.get("hint")),
            None,
        )
        super().__init__("one or more Xquant result reports failed")


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
        self.local_task_file = (
            LocalTaskFileAdapter(settings.runtime.local_task_file)
            if settings.runtime.local_task_file
            else None
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
        using_local_task_file = self.local_task_file is not None
        should_report_gateway = not using_local_task_file
        if self.local_task_file is not None:
            tasks = self.local_task_file.fetch_pending_tasks(self.settings.qmt.account_id)
        else:
            if self.settings.xquant.product_code:
                logger.warning(
                    "xquant.product_code is ignored; poll_once uses trading-gateway tasks"
                )
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
            if self.storage.is_terminal_task(task.task_id):
                logger.info("skip terminal task: %s", task.task_id)
                continue
            if force_dry_run:
                task.mode = "dry_run"
            prices: dict[str, float] = {}
            try:
                self.storage.claim_task(task)
                condition_orders = extract_condition_orders(task)
                prices = self.qmt.get_prices([target.symbol for target in task.targets] + [p.symbol for p in positions])
                plan = self.portfolio.build_plan(task, account, positions, prices)
                self.risk.validate(task, account, plan, known_symbols=set(prices))
                self.storage.record_plan(plan)
                if should_report_gateway:
                    self.xquant.report_plan(task.task_id, plan.model_dump(mode="json"))
                result = ExecutionEngine(self.qmt, self.settings.runtime).execute(plan, task.mode)
                self._attach_current_account_snapshot(
                    result,
                    task,
                    fallback_account=account,
                    fallback_positions=positions,
                    fallback_prices=prices,
                )
                self.storage.record_execution_result(result)
                status = result.status if result.status in {"dry_run_success", "submitted"} else "failed"
                self.storage.mark_task_result(task.task_id, status, result.model_dump(mode="json"))
                if status in {"dry_run_success", "submitted"}:
                    self._arm_condition_orders(task, condition_orders)
                if should_report_gateway:
                    self.xquant.report_result(task.task_id, status, result)
                results.append({"task_id": task.task_id, "status": status})
            except Exception as exc:  # noqa: BLE001 - each task must be audited
                logger.exception("task failed: %s", task.task_id)
                failure = ExecutionResult(
                    task_id=task.task_id,
                    status="failed",
                    mode=task.mode,
                    planned_orders=[],
                    errors=[str(exc)],
                    meta={"error": str(exc)},
                )
                self._attach_current_account_snapshot(
                    failure,
                    task,
                    fallback_account=account,
                    fallback_positions=positions,
                    fallback_prices=prices,
                )
                payload = failure.model_dump(mode="json")
                self.storage.mark_task_result(task.task_id, "failed", payload)
                if should_report_gateway:
                    try:
                        self.xquant.report_result(task.task_id, "failed", failure)
                    except Exception:
                        logger.exception("failed to report task failure to Xquant")
                results.append({"task_id": task.task_id, "status": "failed", "error": str(exc)})
        return results

    def condition_poll_once(self) -> list[dict[str, object]]:
        self.storage.initialize()
        active_orders = self.storage.list_active_condition_orders()
        pending_reference_orders = self.storage.list_pending_reference_condition_orders(
            account_id=self.settings.qmt.account_id
        )
        if not active_orders and not pending_reference_orders:
            logger.info("no active condition orders")
            return []

        self.qmt.connect()
        account = self.qmt.get_account_snapshot()
        positions = self.qmt.get_positions()
        if pending_reference_orders:
            self._refresh_pending_condition_references(
                pending_reference_orders,
                {position.symbol: position for position in positions},
            )
            active_orders = self.storage.list_active_condition_orders()
            if not active_orders:
                logger.info("no active condition orders after reference refresh")
                return []
        symbols = sorted({order.symbol for order in active_orders})
        prices = self._condition_prices(symbols)
        now = datetime.now(ZoneInfo(self.settings.risk.timezone))
        triggered_plans = ConditionEngine(self.storage, market_data=self.qmt).evaluate(
            account,
            positions,
            prices,
            now=now,
        )
        results: list[dict[str, object]] = []
        remaining_turnover_amount = account.total_asset * self.settings.risk.max_turnover_ratio
        for triggered in triggered_plans:
            condition_id = triggered.order.condition_id
            try:
                self.storage.record_task_received(triggered.task, status="claimed")
                self.storage.record_plan(triggered.plan)
                self.storage.update_condition_order_status(condition_id, "triggered")
                if triggered.plan.turnover_amount > remaining_turnover_amount + 1e-9:
                    raise RiskError("condition turnover exceeds remaining threshold")
                self.risk.validate(triggered.task, account, triggered.plan, now=now, known_symbols=set(prices))
                remaining_turnover_amount -= triggered.plan.turnover_amount
            except Exception as exc:  # noqa: BLE001 - risk-blocked triggers still need audit
                logger.exception("condition order blocked by risk: %s", condition_id)
                result = self._failed_condition_execution_result(
                    triggered,
                    str(exc),
                    account,
                    positions,
                    prices,
                    failure_stage="risk_validation",
                )
                self.storage.mark_task_result(
                    triggered.task.task_id,
                    "failed",
                    result.model_dump(mode="json"),
                )
                self.storage.update_condition_order_status(condition_id, "failed")
                self.storage.record_condition_event(
                    condition_id,
                    "failed",
                    {"error": str(exc), "stage": "risk_validation"},
                )
                self.storage.record_condition_event(
                    condition_id,
                    "execution_result",
                    {"status": result.status, "payload": result.model_dump(mode="json")},
                )
                self._record_and_report_condition_audit(triggered, result)
                results.append(
                    {
                        "condition_id": condition_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue
            try:
                self.storage.update_condition_order_status(condition_id, "submitting")
                result = ExecutionEngine(self.qmt, self.settings.runtime).execute(
                    triggered.plan,
                    triggered.task.mode,
                )
                self._attach_current_account_snapshot(
                    result,
                    triggered.task,
                    fallback_account=account,
                    fallback_positions=positions,
                    fallback_prices=prices,
                )
                self.storage.record_execution_result(result)
                status = result.status if result.status in {"dry_run_success", "submitted"} else "failed"
                self.storage.mark_task_result(
                    triggered.task.task_id,
                    status,
                    result.model_dump(mode="json"),
                )
                if status in {"dry_run_success", "submitted"}:
                    self.storage.update_condition_order_status(condition_id, "submitted")
                else:
                    self.storage.update_condition_order_status(condition_id, "failed")
                self.storage.record_condition_event(
                    condition_id,
                    "execution_result",
                    {"status": result.status, "payload": result.model_dump(mode="json")},
                )
                self._record_and_report_condition_audit(triggered, result)
                results.append(
                    {
                        "condition_id": condition_id,
                        "status": result.status,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - each condition must be audited
                logger.exception("condition order failed: %s", condition_id)
                self.storage.update_condition_order_status(condition_id, "failed")
                self.storage.record_condition_event(condition_id, "failed", {"error": str(exc)})
                results.append({"condition_id": condition_id, "status": "failed", "error": str(exc)})
        return results

    def _condition_prices(self, symbols: list[str]) -> dict[str, float]:
        prices: dict[str, float] = {}
        for symbol in symbols:
            try:
                prices.update(self.qmt.get_prices([symbol]))
            except Exception as exc:  # noqa: BLE001 - quote gaps are audited per condition
                logger.exception("failed to query condition price: %s", symbol)
        return prices

    def _failed_condition_execution_result(
        self,
        triggered: TriggeredConditionPlan,
        error: str,
        account: AccountSnapshot,
        positions: list[Position],
        prices: dict[str, float],
        *,
        failure_stage: str,
    ) -> ExecutionResult:
        result = ExecutionResult(
            task_id=triggered.task.task_id,
            status="failed",
            mode=triggered.task.mode,
            planned_orders=triggered.plan.orders,
            errors=[error],
            meta={"error": error, "failure_stage": failure_stage},
        )
        self._attach_current_account_snapshot(
            result,
            triggered.task,
            fallback_account=account,
            fallback_positions=positions,
            fallback_prices=prices,
        )
        return result

    def _record_and_report_condition_audit(
        self,
        triggered: TriggeredConditionPlan,
        result: ExecutionResult,
    ) -> Exception | None:
        condition_id = triggered.order.condition_id
        audit_payload = self._condition_audit_payload(triggered, result)
        self.storage.record_condition_trigger_audit(
            condition_id=condition_id,
            source_task_id=triggered.order.task_id,
            condition_task_id=triggered.task.task_id,
            symbol=triggered.order.symbol,
            purpose=triggered.order.purpose,
            method=triggered.order.method,
            rule=audit_payload["rule"],
            market_state=audit_payload["market_state"],
            trigger=audit_payload["trigger"],
            execution_result=result.model_dump(mode="json"),
        )
        if self.local_task_file is not None:
            self.storage.update_condition_audit_report_status(
                triggered.task.task_id,
                "skipped",
                "local_task_file",
            )
            return None
        try:
            self.xquant.report_condition_result(
                triggered.order.task_id,
                condition_id,
                audit_payload,
            )
            self.storage.update_condition_audit_report_status(
                triggered.task.task_id,
                "success",
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed to report condition result to Xquant")
            self.storage.update_condition_audit_report_status(
                triggered.task.task_id,
                "failed",
                str(exc),
            )
            self.storage.update_condition_order_status(condition_id, "needs_reconcile")
            return exc

    def _retry_condition_audit_report(self, condition_task_id: str) -> dict[str, object]:
        audit = self.storage.get_condition_trigger_audit(condition_task_id)
        if audit is None:
            return {
                "task_id": condition_task_id,
                "status": "missing_audit",
                "xquant_synced": False,
                "error": "condition audit record not found",
            }
        order = self.storage.get_condition_order(audit["condition_id"])
        execution_result = audit["execution_result"]
        execution_status = str(execution_result.get("status") or "unknown")
        payload = {
            "condition_task_id": audit["condition_task_id"],
            "account_id": order.account_id,
            "portfolio_id": order.portfolio_id,
            "symbol": audit["symbol"],
            "status": execution_status,
            "trigger": audit["trigger"],
            "rule": audit["rule"],
            "market_state": audit["market_state"],
            "execution_result": execution_result,
        }
        result_item: dict[str, object] = {
            "task_id": condition_task_id,
            "status": execution_status,
        }
        try:
            self.xquant.report_condition_result(
                audit["source_task_id"],
                audit["condition_id"],
                payload,
            )
            self.storage.update_condition_audit_report_status(condition_task_id, "success")
            self._update_synced_condition_status(audit["condition_id"], execution_status)
        except Exception as exc:  # noqa: BLE001 - sync should return all report failures
            self.storage.update_condition_audit_report_status(condition_task_id, "failed", str(exc))
            result_item.update(
                {
                    "xquant_synced": False,
                    "status_code": exc.status_code if isinstance(exc, XquantAdapterError) else None,
                    "error": str(exc),
                    "hint": _xquant_report_error_hint(exc) if isinstance(exc, XquantAdapterError) else None,
                }
            )
        return result_item

    def _condition_audit_payload(
        self,
        triggered: TriggeredConditionPlan,
        result: ExecutionResult,
    ) -> dict[str, Any]:
        market_state = self.storage.get_condition_market_state(triggered.order.condition_id) or {}
        triggered_at = self.storage.get_condition_order_triggered_at(triggered.order.condition_id)
        trigger = {
            "triggered_at": triggered_at
            or datetime.now(ZoneInfo(self.settings.risk.timezone)).isoformat(),
            "latest_price": market_state.get("latest_price"),
            "trigger_price": market_state.get("trigger_price"),
            "reason": self._condition_trigger_reason(triggered, market_state),
        }
        return {
            "condition_task_id": triggered.task.task_id,
            "account_id": triggered.order.account_id,
            "portfolio_id": triggered.order.portfolio_id,
            "symbol": triggered.order.symbol,
            "status": result.status,
            "trigger": trigger,
            "rule": {
                "scope": triggered.order.scope,
                "purpose": triggered.order.purpose,
                "method": triggered.order.method,
                "reference": self._condition_reference_payload(triggered.order),
                "params": triggered.order.params,
                "action": triggered.order.action.model_dump(mode="json"),
            },
            "market_state": market_state,
            "execution_result": result.model_dump(mode="json"),
        }

    def _condition_trigger_reason(
        self,
        triggered: TriggeredConditionPlan,
        market_state: dict[str, Any],
    ) -> str | None:
        state = market_state.get("state", {})
        if isinstance(state, dict) and state.get("trigger_reason"):
            return str(state["trigger_reason"])
        if market_state.get("latest_price") is None or market_state.get("trigger_price") is None:
            return None
        if triggered.order.method == "static_pct" and triggered.order.purpose == "take_profit":
            return "latest_price >= trigger_price"
        return "latest_price <= trigger_price"

    def run_forever(self) -> None:
        next_task_poll = 0.0
        next_condition_poll = 0.0
        last_error: str | None = None
        while True:
            current = time.monotonic()
            task_poll_due = current >= next_task_poll
            if current >= next_task_poll:
                try:
                    self.poll_once()
                except Exception as exc:  # noqa: BLE001 - daemon must keep reporting liveness
                    logger.exception("poll loop failed")
                    last_error = _append_error(last_error, str(exc))
                next_task_poll = current + self.settings.runtime.poll_interval_seconds
            if current >= next_condition_poll:
                try:
                    self.condition_poll_once()
                except Exception as exc:  # noqa: BLE001 - condition failures must not stop polling
                    logger.exception("condition poll loop failed")
                    last_error = _append_error(last_error, str(exc))
                next_condition_poll = current + self.settings.runtime.condition_poll_interval_seconds
            if task_poll_due:
                try:
                    self.heartbeat_once(last_error=last_error)
                    last_error = None
                except Exception:  # noqa: BLE001 - heartbeat failure must not stop polling
                    logger.exception("failed to report heartbeat to Xquant")
            sleep_until = min(next_task_poll, next_condition_poll)
            time.sleep(max(0.1, sleep_until - time.monotonic()))

    def heartbeat_once(self, last_error: str | None = None) -> dict[str, Any]:
        qmt_status = check_qmt_connection_for_heartbeat(self.qmt, last_error, logger)
        snapshot = qmt_status.snapshot

        return self.xquant.heartbeat(
            account_id=self.settings.qmt.account_id,
            client_version=__version__,
            hostname=socket.gethostname(),
            qmt_connected=qmt_status.qmt_connected,
            xtquant_importable=xtquant_importable(),
            last_error=qmt_status.last_error,
            cash=snapshot["cash"] if snapshot else None,
            total_asset=snapshot["total_asset"] if snapshot else None,
            holdings=snapshot["holdings"] if snapshot else None,
        )

    def sync_submitted_orders_once(self) -> list[dict[str, object]]:
        self.storage.initialize()
        partial_task_ids = self.storage.list_syncable_task_ids(status="partial")
        results = self.sync_results(status="submitted")
        for partial_task_id in partial_task_ids:
            results.extend(self.sync_results(task_id=partial_task_id, status="partial"))
        return results

    def sync_results(self, task_id: str | None = None, status: str = "all") -> list[dict[str, object]]:
        self.storage.initialize()
        task_ids = self.storage.list_syncable_task_ids(task_id=task_id, status=status)
        task_id_set = set(task_ids)
        audit_task_ids = [
            audit_task_id
            for audit_task_id in self.storage.list_retryable_condition_audit_task_ids(
                task_id=task_id,
                status=status,
            )
            if audit_task_id not in task_id_set
        ]
        if not task_ids and not audit_task_ids:
            logger.info("no matching tasks to sync")
            return []

        results: list[dict[str, object]] = []

        if task_ids:
            self.qmt.connect()
            qmt_orders = [compact_obj(order) for order in self.qmt.get_orders()]
            qmt_trades = [compact_obj(trade) for trade in self.qmt.get_trades()]

            for submitted_task_id in task_ids:
                planned_orders = self.storage.load_planned_orders(submitted_task_id)
                submitted_orders = self.storage.load_submitted_orders(submitted_task_id)
                matched_orders = [
                    payload
                    for payload in qmt_orders
                    if _payload_matches_task(
                        payload,
                        submitted_task_id,
                        submitted_orders,
                        planned_orders,
                    )
                ]
                matched_trades = [
                    payload
                    for payload in qmt_trades
                    if _payload_matches_task(
                        payload,
                        submitted_task_id,
                        submitted_orders,
                        planned_orders,
                    )
                ]
                synced_orders, synced_status, errors, sync_summary = _summarize_synced_orders(
                    submitted_orders,
                    matched_orders,
                    matched_trades,
                    planned_orders,
                )
                result = ExecutionResult(
                    task_id=submitted_task_id,
                    status=synced_status,
                    mode=self.storage.load_task_mode(submitted_task_id),  # type: ignore[arg-type]
                    planned_orders=planned_orders,
                    submitted_orders=synced_orders,
                    trades=[_normalize_trade_payload(trade) for trade in matched_trades],
                    events=[
                        {
                            "event_type": "stock_order",
                            "order_id": _payload_order_id(order),
                            "symbol": _payload_symbol(order),
                            "payload": order,
                        }
                        for order in matched_orders
                    ],
                    errors=errors,
                    meta={"sync_source": "qmt_query", "sync_summary": sync_summary},
                )
                self._attach_current_account_snapshot(result, self.storage.load_task(submitted_task_id))
                self.storage.mark_task_result(submitted_task_id, synced_status, result.model_dump(mode="json"))
                result_item: dict[str, object] = {
                    "task_id": submitted_task_id,
                    "status": synced_status,
                }
                if _is_condition_task_id(submitted_task_id):
                    report_error = self._record_and_report_synced_condition_result(
                        submitted_task_id,
                        result,
                    )
                    if report_error is not None:
                        result_item.update(
                            {
                                "xquant_synced": False,
                                "status_code": report_error.status_code
                                if isinstance(report_error, XquantAdapterError)
                                else None,
                                "error": str(report_error),
                                "hint": _xquant_report_error_hint(report_error)
                                if isinstance(report_error, XquantAdapterError)
                                else None,
                            }
                        )
                    results.append(result_item)
                    continue
                self._refresh_condition_orders_for_task(submitted_task_id)
                try:
                    self.xquant.report_result(submitted_task_id, synced_status, result)
                except XquantAdapterError as exc:
                    result_item.update(
                        {
                            "xquant_synced": False,
                            "status_code": exc.status_code,
                            "error": str(exc),
                            "hint": _xquant_report_error_hint(exc),
                            "sync_summary": sync_summary,
                        }
                    )
                results.append(result_item)

        for audit_task_id in audit_task_ids:
            results.append(self._retry_condition_audit_report(audit_task_id))

        if any(result.get("xquant_synced") is False for result in results):
            raise GatewaySyncReportError(results)
        return results

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

    def _record_and_report_synced_condition_result(
        self,
        condition_task_id: str,
        result: ExecutionResult,
    ) -> Exception | None:
        condition_id = self._condition_id_for_condition_task(condition_task_id)
        order = self.storage.get_condition_order(condition_id)
        self._update_synced_condition_status(condition_id, result.status)
        triggered = self._triggered_condition_from_synced_result(order, result)
        return self._record_and_report_condition_audit(triggered, result)

    def _condition_id_for_condition_task(self, condition_task_id: str) -> str:
        task = self.storage.load_task(condition_task_id)
        if task is not None and isinstance(task.raw, dict):
            raw_condition_id = task.raw.get("condition_id")
            if isinstance(raw_condition_id, str) and raw_condition_id.strip():
                return raw_condition_id
        audit = self.storage.get_condition_trigger_audit(condition_task_id)
        if audit is not None:
            return str(audit["condition_id"])
        stored_condition_id = self.storage.find_condition_id_for_condition_task_id(
            condition_task_id
        )
        if stored_condition_id is not None:
            return stored_condition_id
        return _condition_id_from_task_id(condition_task_id)

    def _triggered_condition_from_synced_result(
        self,
        order: ConditionOrder,
        result: ExecutionResult,
    ) -> TriggeredConditionPlan:
        task = RebalanceTask(
            task_id=result.task_id,
            portfolio_id=order.portfolio_id,
            account_id=order.account_id,
            mode=result.mode,
            created_at=datetime.now(ZoneInfo(self.settings.risk.timezone)),
            expires_at=order.expires_at,
            targets=[TargetPosition(symbol=order.symbol, target_weight=0)],
            raw={"condition_id": order.condition_id, "source_task_id": order.task_id},
        )
        turnover_amount = sum(item.amount for item in result.planned_orders)
        total_asset = result.total_asset or 0
        plan = OrderPlan(
            task_id=result.task_id,
            account_id=order.account_id,
            total_asset=total_asset,
            turnover_amount=turnover_amount,
            turnover_ratio=turnover_amount / total_asset if total_asset > 0 else 0,
            orders=result.planned_orders,
        )
        return TriggeredConditionPlan(order=order, task=task, plan=plan)

    def _update_synced_condition_status(self, condition_id: str, status: str) -> None:
        if status == "success":
            self.storage.update_condition_order_status(condition_id, "completed")
        elif status == "failed":
            self.storage.update_condition_order_status(condition_id, "failed")
        elif status == "partial":
            self.storage.update_condition_order_status(condition_id, "needs_reconcile")
        else:
            self.storage.update_condition_order_status(condition_id, "submitted")

    def _attach_current_account_snapshot(
        self,
        result: ExecutionResult,
        task: RebalanceTask | None,
        *,
        fallback_account: AccountSnapshot | None = None,
        fallback_positions: list[Position] | None = None,
        fallback_prices: dict[str, float] | None = None,
    ) -> None:
        try:
            account = self.qmt.get_account_snapshot()
            positions = self.qmt.get_positions()
        except Exception as exc:  # noqa: BLE001 - result sync must not lose order status
            logger.exception("failed to query account snapshot during result sync")
            result.meta["account_snapshot_error"] = str(exc)
            if fallback_account is None or fallback_positions is None:
                return
            account = fallback_account
            positions = fallback_positions

        price_symbols = sorted(
            {position.symbol for position in positions}
            | {order.symbol for order in result.planned_orders}
            | ({target.symbol for target in task.targets} if task else set())
        )
        prices: dict[str, float] = dict(fallback_prices or {})
        if price_symbols:
            try:
                prices.update(self.qmt.get_prices(price_symbols))
            except Exception as exc:  # noqa: BLE001 - holdings can still use broker market value
                logger.exception("failed to query account prices during result sync")
                result.meta["account_price_error"] = str(exc)
        attach_account_snapshot(result, account, positions, prices, task)

    def _arm_condition_orders(
        self,
        task: RebalanceTask,
        condition_orders: list[ConditionOrder],
    ) -> None:
        if not condition_orders:
            return
        position_map = self._current_position_map()
        refreshed_orders = [
            self._condition_order_with_position_reference(order, position_map)
            for order in condition_orders
        ]
        refreshed_orders = [
            order
            for order in refreshed_orders
            if self._can_arm_condition_order_refresh(order)
        ]
        if not refreshed_orders:
            return
        self.storage.upsert_condition_orders(refreshed_orders)
        for order in refreshed_orders:
            event_type = "armed" if order.status == "armed" else order.status
            payload = {
                "task_id": task.task_id,
                "symbol": order.symbol,
                "method": order.method,
                "reference": self._condition_reference_payload(order),
            }
            if order.status == "pending_reference":
                payload["reason"] = "missing position cost_price"
            self.storage.record_condition_event(
                order.condition_id,
                event_type,
                payload,
            )

    def _can_arm_condition_order_refresh(self, order: ConditionOrder) -> bool:
        try:
            existing_order = self.storage.get_condition_order(order.condition_id)
        except KeyError:
            return True
        if existing_order.status in _CONDITION_REARM_BLOCKING_STATUSES:
            self.storage.record_condition_event(
                order.condition_id,
                "arm_skipped",
                {
                    "task_id": order.task_id,
                    "existing_task_id": existing_order.task_id,
                    "existing_status": existing_order.status,
                    "reason": "condition execution already in flight",
                },
            )
            return False
        return True

    def _refresh_condition_orders_for_task(self, task_id: str) -> None:
        orders = self.storage.list_condition_orders_for_task(task_id)
        if not orders:
            return
        position_map = self._current_position_map()
        refreshed_orders = [
            self._condition_order_with_position_reference(order, position_map)
            for order in orders
        ]
        self.storage.upsert_condition_orders(refreshed_orders)
        for order in refreshed_orders:
            if order.status == "pending_reference":
                self.storage.record_condition_event(
                    order.condition_id,
                    "pending_reference",
                    {
                        "task_id": task_id,
                        "symbol": order.symbol,
                        "reason": "missing position cost_price",
                        "reference": self._condition_reference_payload(order),
                    },
                )
                continue
            if order.reference_price is None:
                continue
            self.storage.record_condition_event(
                order.condition_id,
                "reference_updated",
                {
                    "task_id": task_id,
                    "symbol": order.symbol,
                    "reference": self._condition_reference_payload(order),
                },
            )

    def _refresh_pending_condition_references(
        self,
        orders: list[ConditionOrder],
        position_map: dict[str, Position],
    ) -> None:
        refreshed_orders = [
            self._condition_order_with_position_reference(order, position_map)
            for order in orders
        ]
        self.storage.upsert_condition_orders(refreshed_orders)
        for order in refreshed_orders:
            if order.status == "pending_reference":
                self.storage.record_condition_event(
                    order.condition_id,
                    "pending_reference",
                    {
                        "task_id": order.task_id,
                        "symbol": order.symbol,
                        "reason": "missing position cost_price",
                        "reference": self._condition_reference_payload(order),
                    },
                )
                continue
            if order.reference_price is None:
                continue
            self.storage.record_condition_event(
                order.condition_id,
                "reference_updated",
                {
                    "task_id": order.task_id,
                    "symbol": order.symbol,
                    "reference": self._condition_reference_payload(order),
                },
            )

    def _current_position_map(self) -> dict[str, Position]:
        try:
            return {position.symbol: position for position in self.qmt.get_positions()}
        except Exception:  # noqa: BLE001 - condition references can retry on next sync
            logger.exception("failed to query QMT positions for condition references")
            return {}

    def _condition_order_with_position_reference(
        self,
        order: ConditionOrder,
        position_map: dict[str, Position],
    ) -> ConditionOrder:
        if not _uses_position_cost_reference(order):
            return order.model_copy(
                update={"status": _refreshed_condition_status(order, "armed")}
            )

        position = position_map.get(order.symbol)
        reference_price = _position_cost_price(position)
        if reference_price is not None:
            high_water_price = order.high_water_price
            if high_water_price is None or reference_price > high_water_price:
                high_water_price = reference_price
            return order.model_copy(
                update={
                    "reference_price": reference_price,
                    "high_water_price": high_water_price,
                    "status": _refreshed_condition_status(order, "armed"),
                }
            )
        if order.reference_price is not None:
            return order.model_copy(
                update={"status": _refreshed_condition_status(order, "armed")}
            )
        return order.model_copy(
            update={"status": _refreshed_condition_status(order, "pending_reference")}
        )

    def _condition_reference_payload(self, order: ConditionOrder) -> dict[str, object]:
        return {
            "source": _condition_reference_source(order),
            "price": order.reference_price,
            "activation_price": _condition_activation_price(order),
        }


def attach_account_snapshot(
    result: ExecutionResult,
    account: AccountSnapshot,
    positions: list[Position],
    prices: dict[str, float],
    task: RebalanceTask | None = None,
) -> None:
    snapshot = account_result_snapshot(account, positions, prices, task)
    result.cash = snapshot["cash"]
    result.total_asset = snapshot["total_asset"]
    result.holdings = snapshot["holdings"]


def _position_cost_price(position: Position | None) -> float | None:
    if position is None or position.cost_price is None:
        return None
    try:
        cost_price = float(position.cost_price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cost_price) or cost_price <= 0:
        return None
    return cost_price


def _condition_reference_source(order: ConditionOrder) -> str:
    reference = order.raw.get("reference") if isinstance(order.raw, dict) else None
    if isinstance(reference, dict):
        source = reference.get("source")
        if isinstance(source, str) and source.strip():
            return source
    if order.reference_price is not None:
        return "reference_price"
    return "unspecified"


def _uses_position_cost_reference(order: ConditionOrder) -> bool:
    return _condition_reference_source(order) == "position_cost_price"


def _refreshed_condition_status(order: ConditionOrder, status: str) -> str:
    if order.status in {"received", "pending_reference", "armed"}:
        return status
    return order.status


def _condition_activation_price(order: ConditionOrder) -> float | None:
    activation_price = order.params.get("activation_price")
    if activation_price is not None:
        try:
            return float(activation_price)
        except (TypeError, ValueError):
            return None
    activation_profit_pct = order.params.get("activation_profit_pct")
    if activation_profit_pct is None or order.reference_price is None:
        return None
    try:
        return float(order.reference_price) * (1 + float(activation_profit_pct))
    except (TypeError, ValueError):
        return None


def _derive_synced_status(
    submitted_orders,
    qmt_orders: list[dict[str, Any]],
    qmt_trades: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    _, status, errors, _ = _summarize_synced_orders(submitted_orders, qmt_orders, qmt_trades)
    return status, errors


def _summarize_synced_orders(
    submitted_orders,
    qmt_orders: list[dict[str, Any]],
    qmt_trades: list[dict[str, Any]],
    planned_orders=None,
):
    if not submitted_orders:
        return [], "success", [], {"filled_orders": [], "failed_orders": [], "pending_orders": []}

    planned_orders = planned_orders or []
    synced_orders = []
    errors: list[str] = []
    filled_orders = []
    failed_orders = []
    pending_orders = []
    all_filled = True
    any_filled = False
    for submitted in submitted_orders:
        submitted_planned_orders = [
            order
            for order in planned_orders
            if order.task_id == submitted.task_id
            and order.symbol == submitted.symbol
            and order.side == submitted.side
        ]
        order_rows = [
            payload
            for payload in qmt_orders
            if _payload_matches_synced_order(
                payload,
                submitted,
                submitted_planned_orders,
            )
        ]
        trade_rows = [
            payload
            for payload in qmt_trades
            if _payload_matches_synced_order(
                payload,
                submitted,
                submitted_planned_orders,
            )
        ]
        failed_rows = [payload for payload in order_rows if _is_failed_order(payload)]
        traded_quantity = max(
            [_payload_int(payload, "traded_volume", "m_nVolumeTraded") for payload in order_rows] + [0]
        )
        traded_quantity = max(
            traded_quantity,
            sum(_payload_int(payload, "quantity", "traded_volume", "m_nVolumeTraded") for payload in trade_rows),
        )
        if traded_quantity > 0:
            any_filled = True

        order_summary = _submitted_order_summary(submitted, traded_quantity=traded_quantity)
        if failed_rows:
            reason = _failure_reason(failed_rows[0])
            errors.append(f"{submitted.symbol} {submitted.side} failed: {reason}")
            failed_orders.append({**order_summary, "error": reason})
            status = "partial" if traded_quantity > 0 else "failed"
            synced_orders.append(submitted.model_copy(update={"status": status}))
            all_filled = False
            continue

        if traded_quantity >= submitted.quantity:
            filled_orders.append(order_summary)
            synced_orders.append(submitted.model_copy(update={"status": "filled"}))
        elif traded_quantity > 0:
            pending_orders.append(order_summary)
            synced_orders.append(submitted.model_copy(update={"status": "partial"}))
            all_filled = False
        else:
            pending_orders.append(order_summary)
            synced_orders.append(submitted.model_copy(update={"status": "submitted"}))
            all_filled = False

    if errors:
        if any_filled:
            status = "partial"
        else:
            status = "failed"
    elif all_filled:
        status = "success"
    elif any_filled:
        status = "partial"
    else:
        status = "submitted"

    return (
        synced_orders,
        status,
        errors,
        {
            "filled_orders": filled_orders,
            "failed_orders": failed_orders,
            "pending_orders": pending_orders,
        },
    )


def _submitted_order_summary(submitted_order, *, traded_quantity: int) -> dict[str, Any]:
    return {
        "symbol": submitted_order.symbol,
        "side": submitted_order.side,
        "quantity": submitted_order.quantity,
        "traded_quantity": traded_quantity,
        "local_order_id": submitted_order.local_order_id,
        "broker_order_id": submitted_order.broker_order_id,
    }


def _xquant_report_error_hint(exc: XquantAdapterError) -> str | None:
    if exc.status_code == 409 and "invalid_task_transition" in str(exc):
        return (
            "Xquant rejected this result because the remote task is already in a terminal "
            "status. The local QMT sync result was recorded, but refreshing the remote "
            "terminal task requires an Xquant-side reset or idempotent terminal-result update."
        )
    return None


def _payload_matches_task(
    payload: dict[str, Any],
    task_id: str,
    submitted_orders,
    planned_orders=None,
) -> bool:
    if _payload_remark(payload) == task_id:
        return True
    if not _is_condition_task_id(task_id) and _payload_matches_planned_order_remark(
        payload,
        planned_orders or [],
    ):
        return True
    return any(_payload_matches_submitted_order(payload, submitted) for submitted in submitted_orders)


def _is_condition_task_id(task_id: str) -> bool:
    return task_id.startswith("condition:")


def _condition_id_from_task_id(task_id: str) -> str:
    value = task_id.removeprefix("condition:")
    if ":" in value:
        return value.rsplit(":", 1)[1]
    return value


def _payload_matches_submitted_order(payload: dict[str, Any], submitted_order) -> bool:
    payload_ids = _payload_order_ids(payload)
    submitted_ids = {
        str(value)
        for value in (submitted_order.local_order_id, submitted_order.broker_order_id)
        if value not in (None, "")
    }
    if payload_ids and submitted_ids and payload_ids.intersection(submitted_ids):
        return True
    same_symbol = _payload_symbol(payload) == submitted_order.symbol
    if _payload_remark(payload) == submitted_order.task_id and same_symbol:
        return True
    return False


def _payload_matches_synced_order(
    payload: dict[str, Any],
    submitted_order,
    planned_orders,
) -> bool:
    if _payload_matches_submitted_order(payload, submitted_order):
        return True
    if _is_condition_task_id(submitted_order.task_id):
        return False
    return _payload_matches_planned_order_remark(payload, planned_orders)


def _payload_matches_planned_order_remark(payload: dict[str, Any], planned_orders) -> bool:
    remark = _payload_remark(payload)
    if not remark:
        return False
    symbol = _payload_symbol(payload)
    return any(
        order.remark == remark and (symbol is None or symbol == order.symbol)
        for order in planned_orders
    )


def _payload_matches_condition_remark(payload: dict[str, Any], task_id: str) -> bool:
    if not _is_condition_task_id(task_id):
        return False
    return _payload_remark(payload) == f"cond:{_condition_id_from_task_id(task_id)}"


def _payload_order_ids(payload: dict[str, Any]) -> set[str]:
    keys = ("order_id", "broker_order_id", "order_sysid", "m_strOrderSysID", "m_strOrderRef")
    return {str(payload[key]) for key in keys if payload.get(key) not in (None, "")}


def _payload_order_id(payload: dict[str, Any]) -> str | None:
    ids = _payload_order_ids(payload)
    return sorted(ids)[0] if ids else None


def _payload_symbol(payload: dict[str, Any]) -> str | None:
    value = payload.get("symbol") or payload.get("stock_code") or payload.get("m_strInstrumentID")
    return str(value) if value else None


def _payload_remark(payload: dict[str, Any]) -> str | None:
    value = payload.get("remark") or payload.get("order_remark") or payload.get("m_strRemark")
    return str(value) if value else None


def _payload_int(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return 0


def _is_failed_order(payload: dict[str, Any]) -> bool:
    if payload.get("error_msg") or payload.get("msg"):
        return True
    error_id = payload.get("error_id")
    if error_id not in (None, "", 0, "0"):
        return True
    status = payload.get("status") or payload.get("order_status") or payload.get("m_nOrderStatus")
    try:
        return int(status) in {52, 53, 54, 57}
    except (TypeError, ValueError):
        pass
    text = str(status or "").lower()
    return any(marker in text for marker in ("reject", "fail", "cancel", "error", "废", "撤", "拒"))


def _failure_reason(payload: dict[str, Any]) -> str:
    return str(
        payload.get("error_msg")
        or payload.get("msg")
        or payload.get("status")
        or payload.get("order_status")
        or payload.get("m_nOrderStatus")
        or "qmt order failed"
    )


def _normalize_trade_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    if not result.get("symbol"):
        result["symbol"] = _payload_symbol(payload)
    order_id = result.get("order_id") or _payload_order_id(payload)
    if order_id not in (None, ""):
        result["order_id"] = str(order_id)
    return result


def _append_error(last_error: str | None, error: str) -> str:
    return append_heartbeat_error(last_error, error)
