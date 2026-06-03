from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from trade_xquant.condition_orders import ConditionAction, ConditionOrder
from trade_xquant.models import ExecutionResult, OrderPlan, PlannedOrder, RebalanceTask, SubmittedOrder


class Storage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    portfolio_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS target_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    target_weight REAL NOT NULL,
                    UNIQUE(task_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS planned_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS submitted_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    local_order_id TEXT,
                    broker_order_id TEXT,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    event_type TEXT NOT NULL,
                    order_id TEXT,
                    symbol TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    order_id TEXT,
                    symbol TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_results (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS condition_orders (
                    condition_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    portfolio_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    method TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reference_price REAL,
                    high_water_price REAL,
                    trigger_price REAL,
                    params_json TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    valid_from TEXT,
                    expires_at TEXT,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    triggered_at TEXT
                );
                CREATE TABLE IF NOT EXISTS condition_order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    condition_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS condition_market_states (
                    condition_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    latest_price REAL,
                    high_water_price REAL,
                    trigger_price REAL,
                    activated INTEGER NOT NULL,
                    activated_at TEXT,
                    atr_value REAL,
                    hv_value REAL,
                    std_value REAL,
                    computed_at TEXT NOT NULL,
                    market_data_source TEXT NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS condition_trigger_audits (
                    condition_task_id TEXT PRIMARY KEY,
                    condition_id TEXT NOT NULL,
                    source_task_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    method TEXT NOT NULL,
                    rule_json TEXT NOT NULL,
                    market_state_json TEXT NOT NULL,
                    trigger_json TEXT NOT NULL,
                    execution_result_json TEXT NOT NULL,
                    xquant_report_status TEXT NOT NULL,
                    xquant_report_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def claim_task(self, task: RebalanceTask) -> None:
        if self.is_terminal_task(task.task_id):
            raise ValueError(f"task {task.task_id} is terminal and cannot be reprocessed")
        self.record_task_received(task, status="claimed")

    def record_task_received(self, task: RebalanceTask, status: str = "received") -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, portfolio_id, account_id, mode, status,
                    created_at, expires_at, raw_json, received_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    task.task_id,
                    task.portfolio_id,
                    task.account_id,
                    task.mode,
                    status,
                    task.created_at.isoformat(),
                    task.expires_at.isoformat() if task.expires_at else "",
                    to_json(task),
                    now,
                    now,
                ),
            )
            for target in task.targets:
                conn.execute(
                    """
                    INSERT INTO target_positions (task_id, symbol, target_weight)
                    VALUES (?, ?, ?)
                    ON CONFLICT(task_id, symbol) DO UPDATE SET
                        target_weight=excluded.target_weight
                    """,
                    (task.task_id, target.symbol, target.target_weight),
                )

    def record_plan(self, plan: OrderPlan) -> None:
        now = utc_now()
        with self._connection() as conn:
            for order in plan.orders:
                conn.execute(
                    """
                    INSERT INTO planned_orders (
                        task_id, symbol, side, quantity, price, amount, raw_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.task_id,
                        order.symbol,
                        order.side,
                        order.quantity,
                        order.price,
                        order.amount,
                        to_json(order),
                        now,
                    ),
                )

    def record_execution_result(self, result: ExecutionResult) -> None:
        now = utc_now()
        with self._connection() as conn:
            for order in result.submitted_orders:
                conn.execute(
                    """
                    INSERT INTO submitted_orders (
                        task_id, symbol, side, quantity, price, amount,
                        local_order_id, broker_order_id, status, raw_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.task_id,
                        order.symbol,
                        order.side,
                        order.quantity,
                        order.price,
                        order.amount,
                        order.local_order_id,
                        order.broker_order_id,
                        order.status,
                        to_json(order),
                        now,
                    ),
                )

    def upsert_condition_orders(self, orders: list[ConditionOrder]) -> None:
        now = utc_now()
        with self._connection() as conn:
            for order in orders:
                conn.execute(
                    """
                    INSERT INTO condition_orders (
                        condition_id, task_id, portfolio_id, account_id, mode,
                        symbol, scope, purpose, method, side, status,
                        reference_price, high_water_price, trigger_price,
                        params_json, action_json, enabled, valid_from, expires_at,
                        raw_json, created_at, updated_at, triggered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(condition_id) DO UPDATE SET
                        task_id=excluded.task_id,
                        portfolio_id=excluded.portfolio_id,
                        account_id=excluded.account_id,
                        mode=excluded.mode,
                        symbol=excluded.symbol,
                        scope=excluded.scope,
                        purpose=excluded.purpose,
                        method=excluded.method,
                        side=excluded.side,
                        status=excluded.status,
                        reference_price=excluded.reference_price,
                        high_water_price=excluded.high_water_price,
                        trigger_price=excluded.trigger_price,
                        params_json=excluded.params_json,
                        action_json=excluded.action_json,
                        enabled=excluded.enabled,
                        valid_from=excluded.valid_from,
                        expires_at=excluded.expires_at,
                        raw_json=excluded.raw_json,
                        updated_at=excluded.updated_at
                    """,
                    self._condition_order_params(order, now),
                )

    def list_active_condition_orders(self) -> list[ConditionOrder]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM condition_orders
                WHERE enabled=1 AND status IN ('received', 'armed')
                ORDER BY created_at, condition_id
                """
            ).fetchall()
        return [self._condition_order_from_row(row) for row in rows]

    def get_condition_order(self, condition_id: str) -> ConditionOrder:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM condition_orders WHERE condition_id=?",
                (condition_id,),
            ).fetchone()
        if row is None:
            raise KeyError(condition_id)
        return self._condition_order_from_row(row)

    def update_condition_order_status(self, condition_id: str, status: str) -> None:
        now = utc_now()
        triggered_at = now if status == "triggered" else None
        with self._connection() as conn:
            if triggered_at:
                conn.execute(
                    """
                    UPDATE condition_orders
                    SET status=?, updated_at=?, triggered_at=?
                    WHERE condition_id=?
                    """,
                    (status, now, triggered_at, condition_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE condition_orders
                    SET status=?, updated_at=?
                    WHERE condition_id=?
                    """,
                    (status, now, condition_id),
                )

    def update_condition_order_market_state(
        self,
        condition_id: str,
        high_water_price: float | None,
        trigger_price: float | None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE condition_orders
                SET high_water_price=?, trigger_price=?, updated_at=?
                WHERE condition_id=?
                """,
                (high_water_price, trigger_price, utc_now(), condition_id),
            )

    def record_condition_event(self, condition_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO condition_order_events (
                    condition_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (condition_id, event_type, json.dumps(payload, ensure_ascii=False), utc_now()),
            )

    def record_condition_market_state(
        self,
        *,
        condition_id: str,
        symbol: str,
        latest_price: float | None,
        high_water_price: float | None,
        trigger_price: float | None,
        activated: bool,
        activated_at: str | None,
        atr_value: float | None,
        hv_value: float | None,
        std_value: float | None,
        computed_at: str,
        market_data_source: str,
        state: dict[str, Any],
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO condition_market_states (
                    condition_id, symbol, latest_price, high_water_price,
                    trigger_price, activated, activated_at, atr_value,
                    hv_value, std_value, computed_at, market_data_source,
                    state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(condition_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    latest_price=excluded.latest_price,
                    high_water_price=excluded.high_water_price,
                    trigger_price=excluded.trigger_price,
                    activated=excluded.activated,
                    activated_at=excluded.activated_at,
                    atr_value=excluded.atr_value,
                    hv_value=excluded.hv_value,
                    std_value=excluded.std_value,
                    computed_at=excluded.computed_at,
                    market_data_source=excluded.market_data_source,
                    state_json=excluded.state_json
                """,
                (
                    condition_id,
                    symbol,
                    latest_price,
                    high_water_price,
                    trigger_price,
                    1 if activated else 0,
                    activated_at,
                    atr_value,
                    hv_value,
                    std_value,
                    computed_at,
                    market_data_source,
                    json.dumps(state, ensure_ascii=False),
                ),
            )

    def get_condition_market_state(self, condition_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM condition_market_states WHERE condition_id=?",
                (condition_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["activated"] = bool(result["activated"])
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def record_condition_trigger_audit(
        self,
        *,
        condition_id: str,
        source_task_id: str,
        condition_task_id: str,
        symbol: str,
        purpose: str,
        method: str,
        rule: dict[str, Any],
        market_state: dict[str, Any],
        trigger: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO condition_trigger_audits (
                    condition_task_id, condition_id, source_task_id, symbol,
                    purpose, method, rule_json, market_state_json,
                    trigger_json, execution_result_json, xquant_report_status,
                    xquant_report_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(condition_task_id) DO UPDATE SET
                    condition_id=excluded.condition_id,
                    source_task_id=excluded.source_task_id,
                    symbol=excluded.symbol,
                    purpose=excluded.purpose,
                    method=excluded.method,
                    rule_json=excluded.rule_json,
                    market_state_json=excluded.market_state_json,
                    trigger_json=excluded.trigger_json,
                    execution_result_json=excluded.execution_result_json,
                    xquant_report_status=excluded.xquant_report_status,
                    xquant_report_error=excluded.xquant_report_error,
                    updated_at=excluded.updated_at
                """,
                (
                    condition_task_id,
                    condition_id,
                    source_task_id,
                    symbol,
                    purpose,
                    method,
                    json.dumps(rule, ensure_ascii=False),
                    json.dumps(market_state, ensure_ascii=False),
                    json.dumps(trigger, ensure_ascii=False),
                    json.dumps(execution_result, ensure_ascii=False),
                    "pending",
                    None,
                    now,
                    now,
                ),
            )

    def update_condition_audit_report_status(
        self,
        condition_task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE condition_trigger_audits
                SET xquant_report_status=?, xquant_report_error=?, updated_at=?
                WHERE condition_task_id=?
                """,
                (status, error, utc_now(), condition_task_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(condition_task_id)

    def get_condition_trigger_audit(self, condition_task_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM condition_trigger_audits WHERE condition_task_id=?",
                (condition_task_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["rule"] = json.loads(result.pop("rule_json"))
        result["market_state"] = json.loads(result.pop("market_state_json"))
        result["trigger"] = json.loads(result.pop("trigger_json"))
        result["execution_result"] = json.loads(result.pop("execution_result_json"))
        return result

    def record_order_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        task_id: str | None = None,
        order_id: str | None = None,
        symbol: str | None = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO order_events (
                    task_id, event_type, order_id, symbol, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, event_type, order_id, symbol, json.dumps(payload, ensure_ascii=False), utc_now()),
            )

    def record_trade_event(
        self,
        payload: dict[str, Any],
        order_id: str | None = None,
        symbol: str | None = None,
    ) -> None:
        trade_symbol = symbol or payload.get("stock_code") or payload.get("symbol")
        quantity = payload.get("quantity", payload.get("traded_volume", payload.get("m_nVolumeTraded", 0)))
        price = payload.get("price", payload.get("trade_price", 0))
        amount = payload.get("amount", payload.get("trade_amount", payload.get("m_dTradeAmount", 0)))
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    task_id, order_id, symbol, quantity, price, amount, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("task_id"),
                    order_id,
                    trade_symbol,
                    int(quantity or 0),
                    float(price or 0),
                    float(amount or 0),
                    json.dumps(payload, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def mark_task_result(self, task_id: str, status: str, payload: dict[str, Any]) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO task_results (task_id, status, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    created_at=excluded.created_at
                """,
                (task_id, status, json.dumps(payload, ensure_ascii=False), now),
            )
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                (status, now, task_id),
            )

    def is_terminal_task(self, task_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT status FROM task_results WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return bool(row and row["status"] in {"success", "failed", "dry_run", "dry_run_success", "submitted"})

    def list_syncable_task_ids(self, task_id: str | None = None, status: str = "all") -> list[str]:
        statuses = ["submitted", "success", "failed", "partial"]
        if status != "all":
            statuses = [status]
        placeholders = ", ".join("?" for _ in statuses)
        params: list[str] = list(statuses)
        task_filter = ""
        if task_id:
            task_filter = " AND task_id=?"
            params.append(task_id)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT task_id
                FROM task_results
                WHERE status IN ({placeholders})
                  AND EXISTS (
                    SELECT 1 FROM submitted_orders
                    WHERE submitted_orders.task_id = task_results.task_id
                  )
                  {task_filter}
                ORDER BY created_at ASC
                """,
                params,
            ).fetchall()
        return [row["task_id"] for row in rows]

    def list_submitted_task_ids(self, task_id: str | None = None) -> list[str]:
        return self.list_syncable_task_ids(task_id=task_id, status="submitted")

    def load_task_mode(self, task_id: str) -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT mode FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return str(row["mode"]) if row else "real"

    def load_task(self, task_id: str) -> RebalanceTask | None:
        with self._connection() as conn:
            row = conn.execute("SELECT raw_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return RebalanceTask.model_validate_json(row["raw_json"])

    def load_planned_orders(self, task_id: str) -> list[PlannedOrder]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT raw_json FROM planned_orders WHERE task_id=? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [PlannedOrder.model_validate_json(row["raw_json"]) for row in rows]

    def load_submitted_orders(self, task_id: str) -> list[SubmittedOrder]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT raw_json FROM submitted_orders WHERE task_id=? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [SubmittedOrder.model_validate_json(row["raw_json"]) for row in rows]

    def reset_task(self, task_id: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM task_results WHERE task_id=?", (task_id,))
            conn.execute(
                "UPDATE tasks SET status='reset', updated_at=? WHERE task_id=?",
                (utc_now(), task_id),
            )

    def status_summary(self) -> dict[str, Any]:
        with self._connection() as conn:
            tasks = conn.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
            conditions = conn.execute(
                "SELECT status, COUNT(*) AS count FROM condition_orders GROUP BY status"
            ).fetchall()
            recent = conn.execute(
                "SELECT task_id, status, updated_at FROM tasks ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
        return {
            "tasks_by_status": {row["status"]: row["count"] for row in tasks},
            "condition_orders_by_status": {row["status"]: row["count"] for row in conditions},
            "recent_tasks": [dict(row) for row in recent],
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _condition_order_params(self, order: ConditionOrder, now: str) -> tuple[Any, ...]:
        return (
            order.condition_id,
            order.task_id,
            order.portfolio_id,
            order.account_id,
            order.mode,
            order.symbol,
            order.scope,
            order.purpose,
            order.method,
            order.side,
            order.status,
            order.reference_price,
            order.high_water_price,
            order.trigger_price,
            json.dumps(order.params, ensure_ascii=False),
            order.action.model_dump_json(),
            1 if order.enabled else 0,
            order.valid_from.isoformat() if order.valid_from else None,
            order.expires_at.isoformat() if order.expires_at else None,
            json.dumps(order.raw, ensure_ascii=False),
            now,
            now,
            None,
        )

    def _condition_order_from_row(self, row: sqlite3.Row) -> ConditionOrder:
        return ConditionOrder(
            condition_id=row["condition_id"],
            task_id=row["task_id"],
            portfolio_id=row["portfolio_id"],
            account_id=row["account_id"],
            mode=row["mode"],
            symbol=row["symbol"],
            scope=row["scope"],
            purpose=row["purpose"],
            method=row["method"],
            side=row["side"],
            status=row["status"],
            reference_price=row["reference_price"],
            high_water_price=row["high_water_price"],
            trigger_price=row["trigger_price"],
            params=json.loads(row["params_json"]),
            action=ConditionAction.model_validate(json.loads(row["action_json"])),
            enabled=bool(row["enabled"]),
            valid_from=datetime.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            raw=json.loads(row["raw_json"]),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_json(value: BaseModel) -> str:
    return value.model_dump_json()
