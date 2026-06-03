from __future__ import annotations

import logging
import time
from typing import Any

from trade_xquant.config import Settings
from trade_xquant.execution_engine import ExecutionEngine
from trade_xquant.models import AccountSnapshot, ExecutionResult, Position, RebalanceTask
from trade_xquant.mock_qmt_adapter import MockBrokerAdapter
from trade_xquant.portfolio_engine import PortfolioEngine
from trade_xquant.qmt_adapter import QmtAdapter, compact_obj
from trade_xquant.risk_control import RiskControl
from trade_xquant.storage import Storage
from trade_xquant.xquant_adapter import XquantAdapter, XquantAdapterError

logger = logging.getLogger(__name__)


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
            if force_dry_run:
                task.mode = "dry_run"
            prices: dict[str, float] = {}
            try:
                self.storage.claim_task(task)
                prices = self.qmt.get_prices([target.symbol for target in task.targets] + [p.symbol for p in positions])
                plan = self.portfolio.build_plan(task, account, positions, prices)
                self.risk.validate(task, account, plan, known_symbols=set(prices))
                self.storage.record_plan(plan)
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
                try:
                    self.xquant.report_result(task.task_id, "failed", failure)
                except Exception:
                    logger.exception("failed to report task failure to Xquant")
                results.append({"task_id": task.task_id, "status": "failed", "error": str(exc)})
        return results

    def run_forever(self) -> None:
        while True:
            self.poll_once()
            time.sleep(self.settings.runtime.poll_interval_seconds)

    def sync_results(self, task_id: str | None = None, status: str = "all") -> list[dict[str, object]]:
        self.storage.initialize()
        task_ids = self.storage.list_syncable_task_ids(task_id=task_id, status=status)
        if not task_ids:
            logger.info("no matching tasks to sync")
            return []

        self.qmt.connect()
        qmt_orders = [compact_obj(order) for order in self.qmt.get_orders()]
        qmt_trades = [compact_obj(trade) for trade in self.qmt.get_trades()]
        results: list[dict[str, object]] = []

        for submitted_task_id in task_ids:
            planned_orders = self.storage.load_planned_orders(submitted_task_id)
            submitted_orders = self.storage.load_submitted_orders(submitted_task_id)
            matched_orders = [
                payload
                for payload in qmt_orders
                if _payload_matches_task(payload, submitted_task_id, submitted_orders)
            ]
            matched_trades = [
                payload
                for payload in qmt_trades
                if _payload_matches_task(payload, submitted_task_id, submitted_orders)
            ]
            synced_orders, synced_status, errors, sync_summary = _summarize_synced_orders(
                submitted_orders,
                matched_orders,
                matched_trades,
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


def account_result_snapshot(
    account: AccountSnapshot,
    positions: list[Position],
    prices: dict[str, float],
    task: RebalanceTask | None = None,
) -> dict[str, Any]:
    target_weights = {target.symbol: target.target_weight for target in task.targets} if task else {}
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
):
    if not submitted_orders:
        return [], "success", [], {"filled_orders": [], "failed_orders": [], "pending_orders": []}

    synced_orders = []
    errors: list[str] = []
    filled_orders = []
    failed_orders = []
    pending_orders = []
    all_filled = True
    any_filled = False
    for submitted in submitted_orders:
        order_rows = [
            payload for payload in qmt_orders if _payload_matches_submitted_order(payload, submitted)
        ]
        trade_rows = [
            payload for payload in qmt_trades if _payload_matches_submitted_order(payload, submitted)
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


def _payload_matches_task(payload: dict[str, Any], task_id: str, submitted_orders) -> bool:
    if _payload_remark(payload) == task_id:
        return True
    return any(_payload_matches_submitted_order(payload, submitted) for submitted in submitted_orders)


def _payload_matches_submitted_order(payload: dict[str, Any], submitted_order) -> bool:
    payload_ids = _payload_order_ids(payload)
    submitted_ids = {
        str(value)
        for value in (submitted_order.local_order_id, submitted_order.broker_order_id)
        if value not in (None, "")
    }
    if payload_ids and submitted_ids and payload_ids.intersection(submitted_ids):
        return True
    return bool(_payload_remark(payload) == submitted_order.task_id and _payload_symbol(payload) == submitted_order.symbol)


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
