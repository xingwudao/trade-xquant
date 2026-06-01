from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


TaskMode = Literal["dry_run", "real"]
OrderSide = Literal["buy", "sell"]


class TargetPosition(BaseModel):
    symbol: str
    target_weight: float = Field(ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_symbol(value)


class TaskConstraints(BaseModel):
    max_turnover_ratio: float | None = None
    max_single_order_amount: float | None = None
    min_order_amount: float | None = None


class RebalanceTask(BaseModel):
    task_id: str
    portfolio_id: str
    account_id: str
    mode: TaskMode = "dry_run"
    signal_as_of_date: date | None = None
    signal_effective_date: date | None = None
    created_at: datetime
    expires_at: datetime | None = None
    cash_buffer_ratio: float = Field(default=0.002, ge=0, le=1)
    targets: list[TargetPosition]
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("targets")
    @classmethod
    def require_targets(cls, value: list[TargetPosition]) -> list[TargetPosition]:
        if not value:
            raise ValueError("targets must not be empty")
        return value


class AccountSnapshot(BaseModel):
    account_id: str
    total_asset: float
    cash: float
    market_value: float = 0.0
    frozen_cash: float = 0.0


class Position(BaseModel):
    symbol: str
    quantity: int
    sellable_quantity: int
    market_value: float = 0.0
    cost_price: float | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_symbol(value)


class PlannedOrder(BaseModel):
    task_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    amount: float
    price_type: str = "fix"
    remark: str | None = None

    @property
    def qmt_order_type(self) -> int:
        return 23 if self.side == "buy" else 24

    @property
    def qmt_price_type(self) -> int:
        if self.price_type == "latest":
            return 5
        if self.price_type == "counterparty":
            return 14
        return 11


class OrderPlan(BaseModel):
    task_id: str
    account_id: str
    total_asset: float
    turnover_amount: float
    turnover_ratio: float
    orders: list[PlannedOrder]

    @property
    def total_buy_amount(self) -> float:
        return sum(order.amount for order in self.orders if order.side == "buy")

    @property
    def total_sell_amount(self) -> float:
        return sum(order.amount for order in self.orders if order.side == "sell")


class SubmittedOrder(BaseModel):
    task_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    amount: float
    local_order_id: str | None = None
    broker_order_id: str | None = None
    status: str = "submitted"
    raw: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    task_id: str
    status: str
    mode: TaskMode
    planned_orders: list[PlannedOrder]
    submitted_orders: list[SubmittedOrder] = Field(default_factory=list)
    trades: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if symbol.endswith(".SS"):
        return f"{symbol[:-3]}.SH"
    return symbol
