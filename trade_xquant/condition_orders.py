from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
import math
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from trade_xquant.condition_indicators import ConditionIndicatorEngine, PriceBar
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
BAR_CONDITION_METHODS = {"atr_trailing", "hv_log_trailing", "std_trailing"}
TRAILING_CONDITION_METHODS = {"trailing_pct", *BAR_CONDITION_METHODS}
CONDITION_TASK_ID_MAX_LENGTH = 160
CONDITION_TASK_ID_PREFIX = "condition:"
CONDITION_PARAM_ALIASES = {
    "stop_loss_pct": ("pct",),
    "take_profit_pct": ("pct",),
    "trail_pct": ("pct",),
}
ConditionStatus = Literal[
    "received",
    "pending_reference",
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
    type: Literal["sell_pct", "clear"]
    pct: float | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "ConditionAction":
        if self.type == "clear" and self.pct is None:
            return self
        if self.pct is None:
            raise ValueError("sell_pct action requires pct")
        if not math.isfinite(self.pct) or self.pct <= 0 or self.pct > 1:
            raise ValueError("action pct must be finite and 0 < pct <= 1")
        return self


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
    action: ConditionAction
    enabled: bool = True
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol_value(cls, value: str) -> str:
        return normalize_symbol(value)

    @field_validator("valid_from", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("condition datetimes must include timezone")
        return value


@dataclass(frozen=True)
class TriggeredConditionPlan:
    order: ConditionOrder
    task: RebalanceTask
    plan: OrderPlan


class ConditionEngine:
    lot_size = 100

    def __init__(self, storage, market_data=None) -> None:
        self.storage = storage
        self.market_data = market_data
        self.indicators = ConditionIndicatorEngine()

    def evaluate(
        self,
        account: AccountSnapshot,
        positions: list[Position],
        prices: dict[str, float],
        now: datetime,
    ) -> list[TriggeredConditionPlan]:
        position_map = {position.symbol: position for position in positions}
        planned_symbols: set[str] = set()
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

            latest_price: float | None = None
            try:
                latest_price = self._latest_price(order, normalized_prices)
                invalid = validate_condition_hyperparameters(order)
                if invalid:
                    invalid_keys = ", ".join(invalid)
                    raise ValueError(
                        f"condition {order.condition_id} missing/invalid "
                        f"condition params: {invalid_keys}"
                    )
                evaluated, market_state = self._with_market_state(order, latest_price, now)
            except (TypeError, ValueError) as exc:
                reason = str(exc)
                self._record_error_market_state(order, latest_price, reason, now)
                self._record_evaluation_error(order, reason)
                continue
            self.storage.update_condition_order_market_state(
                evaluated.condition_id,
                high_water_price=evaluated.high_water_price,
                trigger_price=evaluated.trigger_price,
            )
            self._record_condition_market_state(
                evaluated,
                latest_price,
                market_state,
                now,
            )
            if not self._is_triggered(
                evaluated,
                latest_price,
                activated=bool(market_state["activated"]),
            ):
                continue

            if evaluated.symbol in planned_symbols:
                self.storage.record_condition_event(
                    evaluated.condition_id,
                    "deferred",
                    {
                        "reason": "symbol already has triggered condition in poll",
                        "symbol": evaluated.symbol,
                    },
                )
                continue

            position = position_map.get(evaluated.symbol)
            if position is not None and position.sellable_quantity <= 0:
                self.storage.record_condition_event(
                    evaluated.condition_id,
                    "deferred",
                    {
                        "reason": "no sellable quantity",
                        "price": latest_price,
                        "symbol": evaluated.symbol,
                    },
                )
                continue

            plan = self._build_sell_plan(
                evaluated,
                account,
                position,
                latest_price,
            )
            if plan is None:
                self.storage.update_condition_order_status(evaluated.condition_id, "failed")
                self.storage.record_condition_event(
                    evaluated.condition_id,
                    "failed",
                    {"reason": "no sellable lot", "price": latest_price},
                )
                continue
            self.storage.record_condition_event(
                evaluated.condition_id,
                "triggered",
                {"price": latest_price, "trigger_price": evaluated.trigger_price},
            )
            triggered.append(plan)
            planned_symbols.add(evaluated.symbol)
        return triggered

    def _latest_price(
        self,
        order: ConditionOrder,
        prices: dict[str, float],
    ) -> float:
        if order.symbol not in prices:
            raise ValueError(f"condition {order.condition_id} missing latest_price")
        raw_price = prices[order.symbol]
        try:
            latest_price = float(raw_price)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"condition {order.condition_id} invalid latest_price: {raw_price}"
            ) from exc
        if latest_price <= 0 or not math.isfinite(latest_price):
            raise ValueError(
                f"condition {order.condition_id} invalid latest_price: {raw_price}"
            )
        return latest_price

    def _with_market_state(
        self,
        order: ConditionOrder,
        latest_price: float,
        now: datetime,
    ) -> tuple[ConditionOrder, dict[str, Any]]:
        stored = self.storage.get_condition_market_state(order.condition_id) or {}
        self._validate_stored_market_state(order, stored)
        activated, activated_at, activation_price = self._activation_state(
            order,
            latest_price,
            now,
            stored,
        )
        high_water_price = self._high_water_price(order, latest_price, stored)
        if (
            order.purpose == "take_profit"
            and order.method in TRAILING_CONDITION_METHODS
            and not activated
        ):
            indicator_values = self._empty_indicator_values()
            evaluated = order.model_copy(
                update={
                    "high_water_price": high_water_price,
                    "trigger_price": None,
                }
            )
            market_state = {
                "activated": activated,
                "activated_at": activated_at,
                "activation_price": activation_price,
                **indicator_values,
            }
            return evaluated, market_state

        indicator_values = self._indicator_values(order)
        trigger_price = self._trigger_price(
            order,
            high_water_price,
            indicator_values=indicator_values,
        )
        if not _finite_float_gt(trigger_price, 0):
            raise ValueError(f"condition {order.condition_id} invalid trigger_price")
        evaluated = order.model_copy(
            update={
                "high_water_price": high_water_price,
                "trigger_price": trigger_price,
            }
        )
        market_state = {
            "activated": activated,
            "activated_at": activated_at,
            "activation_price": activation_price,
            **indicator_values,
        }
        return evaluated, market_state

    def _activation_state(
        self,
        order: ConditionOrder,
        latest_price: float,
        now: datetime,
        stored: dict[str, Any],
    ) -> tuple[bool, str | None, float | None]:
        if order.purpose == "stop_loss" or order.method == "static_pct":
            return True, None, None
        if stored.get("activated"):
            return True, stored.get("activated_at"), self._activation_price(order)

        activation_price = self._activation_price(order)
        if activation_price is None:
            raise ValueError(
                f"condition {order.condition_id} missing "
                "activation_profit_pct|activation_price"
            )
        if latest_price >= activation_price:
            return True, now.isoformat(), activation_price
        return False, None, activation_price

    def _activation_price(self, order: ConditionOrder) -> float | None:
        activation_price = order.params.get("activation_price")
        if activation_price is not None:
            return float(activation_price)
        activation_profit_pct = order.params.get("activation_profit_pct")
        if activation_profit_pct is None:
            return None
        if order.reference_price is None:
            raise ValueError(f"condition {order.condition_id} missing reference_price")
        return order.reference_price * (1 + float(activation_profit_pct))

    def _high_water_price(
        self,
        order: ConditionOrder,
        latest_price: float,
        stored: dict[str, Any],
    ) -> float | None:
        if order.method not in TRAILING_CONDITION_METHODS:
            return order.high_water_price
        candidates = [
            stored.get("high_water_price"),
            order.high_water_price,
            order.reference_price,
            latest_price,
        ]
        return max(float(value) for value in candidates if value is not None)

    def _validate_stored_market_state(
        self,
        order: ConditionOrder,
        stored: dict[str, Any],
    ) -> None:
        for key in ("high_water_price", "trigger_price"):
            value = stored.get(key)
            if value is not None and not _finite_float_gt(value, 0):
                raise ValueError(
                    f"condition {order.condition_id} invalid stored "
                    f"market_state {key}"
                )

    def _empty_indicator_values(self) -> dict[str, float | None]:
        return {"atr_value": None, "hv_value": None, "std_value": None}

    def _indicator_values(self, order: ConditionOrder) -> dict[str, float | None]:
        values = self._empty_indicator_values()
        if order.method not in BAR_CONDITION_METHODS:
            return values
        if self.market_data is None:
            raise ValueError(
                f"condition {order.condition_id} requires market_data for "
                f"{order.method}"
            )

        interval = str(order.params["bar_interval"])
        if order.method == "atr_trailing":
            bars = self._price_bars(order, interval, int(order.params["atr_window"]))
            values["atr_value"] = self.indicators.atr(bars)
        elif order.method == "hv_log_trailing":
            bars = self._price_bars(order, interval, int(order.params["hv_window"]))
            values["hv_value"] = self.indicators.hv_log_return(
                bars,
                float(order.params["hv_annualization"]),
            )
        elif order.method == "std_trailing":
            bars = self._price_bars(order, interval, int(order.params["std_window"]))
            values["std_value"] = self.indicators.price_std(bars)
        return values

    def _price_bars(
        self,
        order: ConditionOrder,
        interval: str,
        window: int,
    ) -> list[PriceBar]:
        try:
            bars = self.market_data.get_price_bars(order.symbol, interval, window)
        except (RuntimeError, NotImplementedError, OSError, TimeoutError) as exc:
            raise ValueError(str(exc)) from exc
        if len(bars) < window:
            raise ValueError(
                f"condition {order.condition_id} requires {window} bars, got {len(bars)}"
            )
        return bars

    def _trigger_price(
        self,
        order: ConditionOrder,
        high_water_price: float | None,
        indicator_values: dict[str, float | None] | None = None,
    ) -> float:
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
        if order.method in BAR_CONDITION_METHODS:
            if high_water_price is None:
                raise ValueError(f"condition {order.condition_id} missing high_water_price")
            values = indicator_values or self._indicator_values(order)
            if order.method == "atr_trailing":
                atr_value = values["atr_value"]
                if atr_value is None:
                    raise ValueError(f"condition {order.condition_id} missing atr_value")
                return high_water_price - atr_value * float(order.params["atr_multiple"])
            if order.method == "hv_log_trailing":
                hv_value = values["hv_value"]
                if hv_value is None:
                    raise ValueError(f"condition {order.condition_id} missing hv_value")
                return high_water_price * math.exp(
                    -float(order.params["lambda"]) * hv_value
                )
            std_value = values["std_value"]
            if std_value is None:
                raise ValueError(f"condition {order.condition_id} missing std_value")
            return high_water_price - std_value * float(order.params["std_multiple"])
        raise ValueError(
            f"condition {order.condition_id} method {order.method} "
            "trigger calculation is not implemented"
        )

    def _record_condition_market_state(
        self,
        order: ConditionOrder,
        latest_price: float,
        market_state: dict[str, Any],
        now: datetime,
    ) -> None:
        self.storage.record_condition_market_state(
            condition_id=order.condition_id,
            symbol=order.symbol,
            latest_price=latest_price,
            high_water_price=order.high_water_price,
            trigger_price=order.trigger_price,
            activated=bool(market_state["activated"]),
            activated_at=market_state["activated_at"],
            atr_value=market_state["atr_value"],
            hv_value=market_state["hv_value"],
            std_value=market_state["std_value"],
            computed_at=now.isoformat(),
            market_data_source=self._market_data_source(order),
            state={
                "method": order.method,
                "purpose": order.purpose,
                "params": order.params,
                "activation_price": market_state["activation_price"],
            },
        )

    def _record_error_market_state(
        self,
        order: ConditionOrder,
        latest_price: float | None,
        reason: str,
        now: datetime,
    ) -> None:
        stored = self.storage.get_condition_market_state(order.condition_id) or {}
        activated, activated_at, activation_price = self._error_activation_state(
            order,
            latest_price,
            stored,
            now,
        )
        high_water_price = self._safe_stored_market_number(stored, "high_water_price")
        if high_water_price is None:
            high_water_price = order.high_water_price
        trigger_price = self._safe_stored_market_number(stored, "trigger_price")
        if trigger_price is None:
            trigger_price = order.trigger_price
        if latest_price is not None and order.method in TRAILING_CONDITION_METHODS:
            high_water_price = self._safe_high_water_price(
                order,
                latest_price,
                high_water_price,
            )
            self.storage.update_condition_order_market_state(
                order.condition_id,
                high_water_price=high_water_price,
                trigger_price=trigger_price,
            )
        self.storage.record_condition_market_state(
            condition_id=order.condition_id,
            symbol=order.symbol,
            latest_price=latest_price,
            high_water_price=high_water_price,
            trigger_price=trigger_price,
            activated=activated,
            activated_at=activated_at,
            atr_value=None,
            hv_value=None,
            std_value=None,
            computed_at=now.isoformat(),
            market_data_source=self._market_data_source(order),
            state={
                "method": order.method,
                "purpose": order.purpose,
                "params": order.params,
                "activation_price": activation_price,
                "evaluation_error": reason,
            },
        )

    def _error_activation_state(
        self,
        order: ConditionOrder,
        latest_price: float | None,
        stored: dict[str, Any],
        now: datetime,
    ) -> tuple[bool, str | None, float | None]:
        if order.purpose != "take_profit" or order.method not in TRAILING_CONDITION_METHODS:
            return bool(stored.get("activated", False)), stored.get("activated_at"), None
        try:
            activation_price = self._activation_price(order)
        except (TypeError, ValueError):
            return bool(stored.get("activated", False)), stored.get("activated_at"), None
        if stored.get("activated"):
            return True, stored.get("activated_at"), activation_price
        if latest_price is not None and activation_price is not None and latest_price >= activation_price:
            return True, now.isoformat(), activation_price
        return False, None, activation_price

    def _safe_stored_market_number(
        self,
        stored: dict[str, Any],
        key: str,
    ) -> float | None:
        value = stored.get(key)
        if value is None or not _finite_float_gt(value, 0):
            return None
        return float(value)

    def _safe_high_water_price(
        self,
        order: ConditionOrder,
        latest_price: float,
        stored_high_water_price: float | None,
    ) -> float | None:
        candidates = [
            stored_high_water_price,
            order.high_water_price,
            order.reference_price,
            latest_price,
        ]
        safe_candidates = [
            float(value)
            for value in candidates
            if value is not None and _finite_float_gt(value, 0)
        ]
        return max(safe_candidates) if safe_candidates else None

    def _market_data_source(self, order: ConditionOrder) -> str:
        if order.method in BAR_CONDITION_METHODS and self.market_data is not None:
            return type(self.market_data).__name__
        return "prices"

    def _record_evaluation_error(self, order: ConditionOrder, reason: str) -> None:
        self.storage.record_condition_event(
            order.condition_id,
            "evaluation_error",
            {
                "method": order.method,
                "reason": reason,
            },
        )

    def _is_triggered(
        self,
        order: ConditionOrder,
        latest_price: float,
        activated: bool = True,
    ) -> bool:
        if not activated:
            return False
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
        requested_quantity = self._floor_lot(position.sellable_quantity * pct)
        quantity = min(requested_quantity, self._floor_lot(position.sellable_quantity))
        if quantity <= 0:
            return None
        condition_task_id = _condition_task_id(order)
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
        if _condition_spec_disabled(spec):
            continue
        for expanded_spec in _expand_condition_spec(task, spec):
            try:
                order = ConditionOrder.model_validate(
                    {
                        **expanded_spec,
                        "task_id": task.task_id,
                        "portfolio_id": task.portfolio_id,
                        "account_id": task.account_id,
                        "mode": task.mode,
                        "raw": expanded_spec,
                        "status": expanded_spec.get("status", "armed"),
                    }
                )
            except ValidationError as exc:
                condition_id = expanded_spec.get("condition_id", "<unknown>")
                raise ValueError(
                    f"condition {condition_id} invalid condition order: {exc}"
                ) from exc
            if not order.enabled:
                continue
            invalid = validate_condition_hyperparameters(order)
            if invalid:
                invalid_keys = ", ".join(invalid)
                raise ValueError(
                    f"condition {order.condition_id} missing/invalid "
                    f"condition params: {invalid_keys}"
                )
            orders.append(order)
    return orders


def _expand_condition_spec(task: RebalanceTask, spec: dict[str, Any]) -> list[dict[str, Any]]:
    if spec.get("symbol") is not None:
        return [spec]
    template_id = spec.get("id") or spec.get("condition_id")
    if not isinstance(template_id, str) or not template_id.strip():
        return [spec]
    return [
        {
            **spec,
            "template_id": template_id,
            "condition_id": f"cond-{task.portfolio_id}-{target.symbol}-{template_id}",
            "symbol": target.symbol,
        }
        for target in task.targets
    ]


def _condition_task_id(order: ConditionOrder) -> str:
    candidate = f"{CONDITION_TASK_ID_PREFIX}{order.task_id}:{order.condition_id}"
    if len(candidate) <= CONDITION_TASK_ID_MAX_LENGTH:
        return candidate
    source_hash = sha1(order.task_id.encode("utf-8")).hexdigest()[:12]
    source_capped = f"{CONDITION_TASK_ID_PREFIX}{source_hash}:{order.condition_id}"
    if len(source_capped) <= CONDITION_TASK_ID_MAX_LENGTH:
        return source_capped

    condition_hash = sha1(order.condition_id.encode("utf-8")).hexdigest()[:12]
    prefix = f"{CONDITION_TASK_ID_PREFIX}{source_hash}:{condition_hash}:"
    tail_length = CONDITION_TASK_ID_MAX_LENGTH - len(prefix)
    if tail_length <= 0:
        return prefix.rstrip(":")[:CONDITION_TASK_ID_MAX_LENGTH]
    return f"{prefix}{order.condition_id[-tail_length:]}"


def _condition_spec_disabled(spec: dict[str, Any]) -> bool:
    value = spec.get("enabled", True)
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in {"0", "false", "no", "off"}
    return False


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
    if order.purpose == "take_profit" and order.method in TRAILING_CONDITION_METHODS:
        required.add("activation_profit_pct|activation_price")
    return required


def validate_condition_hyperparameters(order: ConditionOrder) -> list[str]:
    invalid: list[str] = []
    keys_to_validate: set[str] = set()

    def add(key: str) -> None:
        if key not in invalid:
            invalid.append(key)

    for key in sorted(required_condition_params(order)):
        if "|" in key:
            alternatives = key.split("|")
            present = [name for name in alternatives if order.params.get(name) is not None]
            if not present:
                add(key)
            keys_to_validate.update(present)
        elif not _has_condition_param(order, key):
            add(key)
        else:
            keys_to_validate.add(key)
    for key in sorted(keys_to_validate):
        if not _valid_condition_param(order, key):
            add(key)
    if order.reference_price is not None and not _finite_float_gt(order.reference_price, 0):
        add("reference_price")
    if order.high_water_price is not None and not _finite_float_gt(order.high_water_price, 0):
        add("high_water_price")
    if order.trigger_price is not None and not _finite_float_gt(order.trigger_price, 0):
        add("trigger_price")
    if order.scope != "instrument":
        add("scope:instrument")
    if _requires_reference_price(order) and order.reference_price is None:
        add("reference_price")
    return invalid


def _requires_reference_price(order: ConditionOrder) -> bool:
    if _uses_position_cost_reference(order):
        return False
    if order.method == "static_pct":
        return True
    if (
        order.purpose == "take_profit"
        and order.method in TRAILING_CONDITION_METHODS
        and order.params.get("activation_profit_pct") is not None
        and order.params.get("activation_price") is None
    ):
        return True
    return False


def _uses_position_cost_reference(order: ConditionOrder) -> bool:
    reference = order.raw.get("reference") if isinstance(order.raw, dict) else None
    if not isinstance(reference, dict):
        return False
    return reference.get("source") == "position_cost_price"


def _has_condition_param(order: ConditionOrder, key: str) -> bool:
    if order.params.get(key) is not None:
        return True
    return any(
        order.params.get(alias) is not None
        for alias in CONDITION_PARAM_ALIASES.get(key, ())
    )


def _condition_param_value(order: ConditionOrder, key: str) -> Any:
    value = order.params.get(key)
    if value is not None:
        return value
    for alias in CONDITION_PARAM_ALIASES.get(key, ()):
        value = order.params.get(alias)
        if value is not None:
            return value
    return None


def _valid_condition_param(order: ConditionOrder, key: str) -> bool:
    value = _condition_param_value(order, key)
    if key in {"stop_loss_pct", "trail_pct"}:
        return _finite_float_in_range(value, lower=0, upper=1)
    if key in {
        "take_profit_pct",
        "activation_profit_pct",
        "activation_price",
        "atr_multiple",
        "std_multiple",
        "lambda",
        "hv_annualization",
    }:
        return _finite_float_gt(value, 0)
    if key == "hv_window":
        return _positive_int_at_least(value, 2)
    if key in {"atr_window", "std_window"}:
        return _positive_int(value)
    if key == "bar_interval":
        return isinstance(value, str) and bool(value.strip())
    return True


def _finite_float_gt(value: Any, lower: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > lower


def _finite_float_in_range(value: Any, lower: float, upper: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and lower < number < upper


def _positive_int(value: Any) -> bool:
    return _positive_int_at_least(value, 1)


def _positive_int_at_least(value: Any, minimum: int) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= minimum
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return False
        try:
            parsed = int(stripped)
        except ValueError:
            return False
        return stripped == str(parsed) and parsed >= minimum
    return False


def _param(order: ConditionOrder, primary: str, fallback: str) -> float:
    value = order.params.get(primary)
    if value is None:
        value = order.params.get(fallback)
    if value is None:
        raise ValueError(f"condition {order.condition_id} missing {primary}")
    return float(value)


def now_tz(order: ConditionOrder):
    if order.valid_from and order.valid_from.tzinfo:
        return order.valid_from.tzinfo
    if order.expires_at and order.expires_at.tzinfo:
        return order.expires_at.tzinfo
    return None
