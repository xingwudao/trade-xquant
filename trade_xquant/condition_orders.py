from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from trade_xquant.models import (
    AccountSnapshot,
    OrderPlan,
    PlannedOrder,
    Position,
    RebalanceTask,
    TargetPosition,
    normalize_symbol,
)


ConditionScope = Literal["instrument", "portfolio"]
ConditionPurpose = Literal["stop_loss", "take_profit"]
ConditionMethod = Literal[
    "static_pct",
    "trailing_pct",
    "atr_trailing",
    "hv_log_trailing",
    "std_trailing",
]
DEFERRED_CONDITION_METHODS = {"atr_trailing", "hv_log_trailing", "std_trailing"}
CONDITION_PARAM_ALIASES = {
    "stop_loss_pct": ("pct",),
    "take_profit_pct": ("pct",),
    "trail_pct": ("pct",),
}
ConditionStatus = Literal[
    "received",
    "armed",
    "triggered",
    "submitting",
    "submitted",
    "completed",
    "expired",
    "canceled",
    "failed",
    "needs_reconcile",
]


class ConditionAction(BaseModel):
    type: Literal["sell_pct", "clear"] = "sell_pct"
    pct: float = Field(default=1.0, ge=0, le=1)


class ConditionOrder(BaseModel):
    condition_id: str
    task_id: str
    portfolio_id: str
    account_id: str
    mode: Literal["dry_run", "real"] = "dry_run"
    symbol: str
    scope: ConditionScope = "instrument"
    purpose: ConditionPurpose
    method: ConditionMethod
    side: Literal["sell"] = "sell"
    status: ConditionStatus = "armed"
    reference_price: float | None = Field(default=None, gt=0)
    high_water_price: float | None = Field(default=None, gt=0)
    trigger_price: float | None = Field(default=None, gt=0)
    params: dict[str, Any] = Field(default_factory=dict)
    action: ConditionAction = Field(default_factory=ConditionAction)
    enabled: bool = True
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol_value(cls, value: str) -> str:
        return normalize_symbol(value)


@dataclass(frozen=True)
class TriggeredConditionPlan:
    order: ConditionOrder
    task: RebalanceTask
    plan: OrderPlan


class ConditionEngine:
    lot_size = 100

    def __init__(self, storage) -> None:
        self.storage = storage

    def evaluate(
        self,
        account: AccountSnapshot,
        positions: list[Position],
        prices: dict[str, float],
        now: datetime,
    ) -> list[TriggeredConditionPlan]:
        position_map = {position.symbol: position for position in positions}
        normalized_prices = {normalize_symbol(symbol): price for symbol, price in prices.items()}
        triggered: list[TriggeredConditionPlan] = []
        for order in self.storage.list_active_condition_orders():
            if order.account_id != account.account_id:
                continue
            if order.expires_at is not None and order.expires_at <= now:
                self.storage.update_condition_order_status(order.condition_id, "expired")
                self.storage.record_condition_event(order.condition_id, "expired", {"now": now.isoformat()})
                continue
            if order.valid_from is not None and order.valid_from > now:
                continue
            latest_price = normalized_prices.get(order.symbol)
            if latest_price is None or latest_price <= 0:
                continue
            if order.method in DEFERRED_CONDITION_METHODS:
                self.storage.record_condition_event(
                    order.condition_id,
                    "deferred_method",
                    {
                        "method": order.method,
                        "reason": "trigger calculation is not implemented",
                    },
                )
                continue

            evaluated = self._with_market_state(order, latest_price)
            self.storage.update_condition_order_market_state(
                evaluated.condition_id,
                high_water_price=evaluated.high_water_price,
                trigger_price=evaluated.trigger_price,
            )
            if not self._is_triggered(evaluated, latest_price):
                continue

            plan = self._build_sell_plan(evaluated, account, position_map.get(evaluated.symbol), latest_price)
            if plan is None:
                self.storage.update_condition_order_status(evaluated.condition_id, "failed")
                self.storage.record_condition_event(
                    evaluated.condition_id,
                    "failed",
                    {"reason": "no sellable lot", "price": latest_price},
                )
                continue
            self.storage.update_condition_order_status(evaluated.condition_id, "triggered")
            self.storage.record_condition_event(
                evaluated.condition_id,
                "triggered",
                {"price": latest_price, "trigger_price": evaluated.trigger_price},
            )
            triggered.append(plan)
        return triggered

    def _with_market_state(self, order: ConditionOrder, latest_price: float) -> ConditionOrder:
        high_water_price = order.high_water_price
        if order.method == "trailing_pct":
            high_water_price = max(high_water_price or order.reference_price or latest_price, latest_price)
        trigger_price = self._trigger_price(order, high_water_price)
        return order.model_copy(update={"high_water_price": high_water_price, "trigger_price": trigger_price})

    def _trigger_price(self, order: ConditionOrder, high_water_price: float | None) -> float:
        if order.method == "static_pct":
            reference_price = order.reference_price
            if reference_price is None:
                raise ValueError(f"condition {order.condition_id} missing reference_price")
            if order.purpose == "stop_loss":
                return reference_price * (1 - _param(order, "stop_loss_pct", "pct"))
            return reference_price * (1 + _param(order, "take_profit_pct", "pct"))
        if order.method == "trailing_pct":
            if high_water_price is None:
                raise ValueError(f"condition {order.condition_id} missing high_water_price")
            return high_water_price * (1 - _param(order, "trail_pct", "pct"))
        raise ValueError(
            f"condition {order.condition_id} method {order.method} "
            "trigger calculation is not implemented"
        )

    def _is_triggered(self, order: ConditionOrder, latest_price: float) -> bool:
        if order.trigger_price is None:
            return False
        if order.method == "static_pct" and order.purpose == "take_profit":
            return latest_price >= order.trigger_price
        return latest_price <= order.trigger_price

    def _build_sell_plan(
        self,
        order: ConditionOrder,
        account: AccountSnapshot,
        position: Position | None,
        latest_price: float,
    ) -> TriggeredConditionPlan | None:
        if position is None or position.sellable_quantity <= 0:
            return None
        pct = 1.0 if order.action.type == "clear" else order.action.pct
        quantity = self._floor_lot(position.sellable_quantity * pct)
        if quantity <= 0:
            return None
        condition_task_id = f"condition:{order.condition_id}"
        planned_order = PlannedOrder(
            task_id=condition_task_id,
            symbol=order.symbol,
            side="sell",
            quantity=quantity,
            price=latest_price,
            amount=quantity * latest_price,
            price_type="latest",
            remark=f"cond:{order.condition_id}",
        )
        plan = OrderPlan(
            task_id=condition_task_id,
            account_id=account.account_id,
            total_asset=account.total_asset,
            turnover_amount=planned_order.amount,
            turnover_ratio=planned_order.amount / account.total_asset if account.total_asset > 0 else 0,
            orders=[planned_order],
        )
        task = RebalanceTask(
            task_id=condition_task_id,
            portfolio_id=order.portfolio_id,
            account_id=order.account_id,
            mode=order.mode,
            created_at=datetime.now(tz=now_tz(order)),
            expires_at=order.expires_at,
            targets=[TargetPosition(symbol=order.symbol, target_weight=0)],
            raw={"condition_id": order.condition_id, "source_task_id": order.task_id},
        )
        return TriggeredConditionPlan(order=order, task=task, plan=plan)

    def _floor_lot(self, shares: float) -> int:
        return int(shares // self.lot_size) * self.lot_size


def extract_condition_orders(task: RebalanceTask) -> list[ConditionOrder]:
    raw_specs = list(task.constraints.condition_orders)
    if not raw_specs:
        constraints = task.raw.get("constraints") if isinstance(task.raw, dict) else None
        if isinstance(constraints, dict):
            maybe_specs = constraints.get("condition_orders", [])
            if isinstance(maybe_specs, list):
                raw_specs = maybe_specs
    orders: list[ConditionOrder] = []
    for spec in raw_specs:
        if not isinstance(spec, dict):
            continue
        order = ConditionOrder.model_validate(
            {
                **spec,
                "task_id": task.task_id,
                "portfolio_id": task.portfolio_id,
                "account_id": task.account_id,
                "mode": task.mode,
                "raw": spec,
                "status": spec.get("status", "armed"),
            }
        )
        if not order.enabled:
            continue
        missing = validate_condition_hyperparameters(order)
        if missing:
            missing_keys = ", ".join(missing)
            raise ValueError(
                f"condition {order.condition_id} missing condition params: {missing_keys}"
            )
        orders.append(order)
    return orders


def required_condition_params(order: ConditionOrder) -> set[str]:
    if order.method == "static_pct" and order.purpose == "stop_loss":
        return {"stop_loss_pct"}
    if order.method == "static_pct" and order.purpose == "take_profit":
        return {"take_profit_pct"}
    if order.method == "trailing_pct":
        required = {"trail_pct"}
    elif order.method == "atr_trailing":
        required = {"atr_window", "atr_multiple", "bar_interval"}
    elif order.method == "hv_log_trailing":
        required = {"hv_window", "hv_annualization", "lambda", "bar_interval"}
    elif order.method == "std_trailing":
        required = {"std_window", "std_multiple", "bar_interval"}
    else:
        return set()
    if order.purpose == "take_profit" and order.method in DEFERRED_CONDITION_METHODS:
        required.add("activation_profit_pct|activation_price")
    return required


def validate_condition_hyperparameters(order: ConditionOrder) -> list[str]:
    missing: list[str] = []
    for key in sorted(required_condition_params(order)):
        if "|" in key:
            alternatives = key.split("|")
            if not any(order.params.get(name) is not None for name in alternatives):
                missing.append(key)
        elif not _has_condition_param(order, key):
            missing.append(key)
    if order.scope != "instrument":
        missing.append("scope:instrument")
    if _requires_reference_price(order) and order.reference_price is None:
        missing.append("reference_price")
    return missing


def _requires_reference_price(order: ConditionOrder) -> bool:
    if order.method == "static_pct":
        return True
    if order.method in DEFERRED_CONDITION_METHODS and order.purpose == "take_profit":
        return (
            order.params.get("activation_profit_pct") is not None
            and order.params.get("activation_price") is None
        )
    return False


def _has_condition_param(order: ConditionOrder, key: str) -> bool:
    if order.params.get(key) is not None:
        return True
    return any(
        order.params.get(alias) is not None
        for alias in CONDITION_PARAM_ALIASES.get(key, ())
    )


def _param(order: ConditionOrder, primary: str, fallback: str) -> float:
    value = order.params.get(primary, order.params.get(fallback))
    if value is None:
        raise ValueError(f"condition {order.condition_id} missing {primary}")
    return float(value)


def now_tz(order: ConditionOrder):
    if order.valid_from and order.valid_from.tzinfo:
        return order.valid_from.tzinfo
    if order.expires_at and order.expires_at.tzinfo:
        return order.expires_at.tzinfo
    return None
