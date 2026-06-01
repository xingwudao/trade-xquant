from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from trade_xquant.models import ExecutionResult, OrderPlan, RebalanceTask


class Storage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
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
                """
            )

    def claim_task(self, task: RebalanceTask) -> None:
        if self.is_terminal_task(task.task_id):
            raise ValueError(f"task {task.task_id} is terminal and cannot be reprocessed")
        self.record_task_received(task, status="claimed")

    def record_task_received(self, task: RebalanceTask, status: str = "received") -> None:
        now = utc_now()
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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

    def record_order_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        task_id: str | None = None,
        order_id: str | None = None,
        symbol: str | None = None,
    ) -> None:
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM task_results WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return bool(row and row["status"] in {"success", "failed", "dry_run", "dry_run_success", "submitted"})

    def reset_task(self, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM task_results WHERE task_id=?", (task_id,))
            conn.execute(
                "UPDATE tasks SET status='reset', updated_at=? WHERE task_id=?",
                (utc_now(), task_id),
            )

    def status_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            tasks = conn.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
            recent = conn.execute(
                "SELECT task_id, status, updated_at FROM tasks ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
        return {
            "tasks_by_status": {row["status"]: row["count"] for row in tasks},
            "recent_tasks": [dict(row) for row in recent],
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_json(value: BaseModel) -> str:
    return value.model_dump_json()
