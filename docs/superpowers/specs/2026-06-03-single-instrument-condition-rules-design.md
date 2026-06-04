# Single-Instrument Conditional Rule Completion Design

## Purpose

Complete all single-instrument sell-side stop-loss and take-profit rules for
trade-xquant.

The local JSON task file remains a mock of the Xquant task API. It does not
replace SQLite. SQLite remains the gateway's runtime state store and audit
store.

## Scope

In scope:

- `scope: "instrument"`.
- `side: "sell"`.
- `purpose: "stop_loss"`.
- `purpose: "take_profit"`.
- `method: "static_pct"`.
- `method: "trailing_pct"`.
- `method: "atr_trailing"`.
- `method: "hv_log_trailing"`.
- `method: "std_trailing"`.
- Local JSON fixtures that mock Xquant task payloads.
- Local market-derived state and trigger evidence stored in SQLite.
- Audit result payloads sent back to Xquant after condition-triggered trades.

Out of scope:

- `scope: "portfolio"` account-level rules.
- Conditional buy rules.
- Xquant-side research or backtest parameter generation.
- Xquant API implementation in the Xquant repository.
- QMT order chasing, slicing, or complex execution algorithms.

## Parameter Ownership

There are two parameter classes.

Xquant-owned hyperparameters:

- `params.stop_loss_pct`.
- `params.take_profit_pct`.
- `params.trail_pct`.
- `params.activation_profit_pct`.
- `params.activation_price`.
- `params.atr_window`.
- `params.atr_multiple`.
- `params.hv_window`.
- `params.hv_annualization`.
- `params.lambda`.
- `params.std_window`.
- `params.std_multiple`.
- `params.bar_interval`.
- `action.pct`.

Gateway-owned market-derived state:

- `latest_price`.
- `high_water_price`.
- `trigger_price`.
- `activated`.
- `activated_at`.
- `atr_value`.
- `hv_value`.
- `std_value`.
- `computed_at`.
- `triggered_at`.
- `trigger_reason`.

The gateway must not hardcode trading thresholds. If a rule requires a
hyperparameter and Xquant did not send it, the gateway fails closed and records
an audit event.

## Task Schema

Condition rules are carried under:

```text
constraints.condition_orders[]
```

Example:

```json
{
  "condition_id": "cond-513100-atr-tp",
  "scope": "instrument",
  "symbol": "513100.SH",
  "purpose": "take_profit",
  "method": "atr_trailing",
  "reference_price": 1.0,
  "params": {
    "activation_profit_pct": 0.12,
    "atr_window": 14,
    "atr_multiple": 2.0,
    "bar_interval": "1d"
  },
  "action": {
    "type": "sell_pct",
    "pct": 1.0
  }
}
```

Schema rules:

- `condition_id` is idempotency key for one condition rule.
- `reference_price` is required for single-instrument rules.
- `scope` defaults to `instrument` only for backwards compatibility.
- Unsupported `scope`, `purpose`, or `method` must fail closed.
- Unsupported or missing parameters must fail closed.
- Local JSON uses the same payload shape as the Xquant task API.

## Local State Model

Keep existing tables:

- `condition_orders`.
- `condition_order_events`.

Add `condition_market_states`:

- `condition_id`.
- `symbol`.
- `latest_price`.
- `high_water_price`.
- `trigger_price`.
- `activated`.
- `activated_at`.
- `atr_value`.
- `hv_value`.
- `std_value`.
- `computed_at`.
- `market_data_source`.
- `state_json`.

Add `condition_trigger_audits`:

- `condition_id`.
- `source_task_id`.
- `condition_task_id`.
- `symbol`.
- `purpose`.
- `method`.
- `rule_json`.
- `market_state_json`.
- `trigger_json`.
- `execution_result_json`.
- `xquant_report_status`.
- `xquant_report_error`.
- `created_at`.
- `updated_at`.

SQLite is authoritative for:

- Active or terminal condition status.
- High-water state maintained by the gateway.
- Take-profit activation state.
- Indicator snapshots.
- Trigger evidence.
- Xquant audit report status.

## Rule Semantics

### Static Percentage Stop-Loss

Inputs:

- `reference_price`.
- `params.stop_loss_pct`.

Calculation:

```text
trigger_price = reference_price * (1 - params.stop_loss_pct)
```

Trigger:

```text
latest_price <= trigger_price
```

### Static Percentage Take-Profit

Inputs:

- `reference_price`.
- `params.take_profit_pct`.

Calculation:

```text
trigger_price = reference_price * (1 + params.take_profit_pct)
```

Trigger:

```text
latest_price >= trigger_price
```

### Fixed-Ratio Trailing Stop-Loss

Inputs:

- `reference_price`.
- `params.trail_pct`.

State:

- `high_water_price`.

Calculation:

```text
high_water_price = max(existing_high_water_price, latest_price)
trigger_price = high_water_price * (1 - params.trail_pct)
```

Trigger:

```text
latest_price <= trigger_price
```

### Fixed-Ratio Trailing Take-Profit

Inputs:

- `reference_price`.
- `params.trail_pct`.
- `params.activation_profit_pct` or `params.activation_price`.

State:

- `activated`.
- `activated_at`.
- `high_water_price`.

Activation:

```text
latest_price >= params.activation_price
```

or:

```text
latest_price >= reference_price * (1 + params.activation_profit_pct)
```

Calculation after activation:

```text
high_water_price = max(existing_high_water_price, latest_price)
trigger_price = high_water_price * (1 - params.trail_pct)
```

Trigger after activation:

```text
latest_price <= trigger_price
```

Trailing take-profit must require activation. Without activation it is too
close to trailing stop-loss semantics.

### ATR Trailing Stop-Loss

Inputs:

- `reference_price`.
- `params.atr_window`.
- `params.atr_multiple`.
- `params.bar_interval`.

Derived state:

- `high_water_price`.
- `atr_value`.

Calculation:

```text
trigger_price =
  high_water_price - params.atr_multiple * atr_value
```

Trigger:

```text
latest_price <= trigger_price
```

### ATR Trailing Take-Profit

Inputs:

- `reference_price`.
- `params.activation_profit_pct` or `params.activation_price`.
- `params.atr_window`.
- `params.atr_multiple`.
- `params.bar_interval`.

Derived state:

- `activated`.
- `high_water_price`.
- `atr_value`.

Calculation after activation:

```text
trigger_price =
  high_water_price - params.atr_multiple * atr_value
```

Trigger after activation:

```text
latest_price <= trigger_price
```

### HV Log-Return Trailing Stop-Loss

Inputs:

- `reference_price`.
- `params.hv_window`.
- `params.hv_annualization`.
- `params.lambda`.
- `params.bar_interval`.

Derived state:

- `high_water_price`.
- `hv_value`.

Calculation:

```text
trigger_price =
  high_water_price * exp(-params.lambda * hv_value)
```

Trigger:

```text
latest_price <= trigger_price
```

### HV Log-Return Trailing Take-Profit

Inputs:

- `reference_price`.
- `params.activation_profit_pct` or `params.activation_price`.
- `params.hv_window`.
- `params.hv_annualization`.
- `params.lambda`.
- `params.bar_interval`.

Derived state:

- `activated`.
- `high_water_price`.
- `hv_value`.

Calculation after activation:

```text
trigger_price =
  high_water_price * exp(-params.lambda * hv_value)
```

Trigger after activation:

```text
latest_price <= trigger_price
```

### Standard-Deviation Trailing Stop-Loss

Inputs:

- `reference_price`.
- `params.std_window`.
- `params.std_multiple`.
- `params.bar_interval`.

Derived state:

- `high_water_price`.
- `std_value`.

Calculation:

```text
trigger_price =
  high_water_price - params.std_multiple * std_value
```

Trigger:

```text
latest_price <= trigger_price
```

### Standard-Deviation Trailing Take-Profit

Inputs:

- `reference_price`.
- `params.activation_profit_pct` or `params.activation_price`.
- `params.std_window`.
- `params.std_multiple`.
- `params.bar_interval`.

Derived state:

- `activated`.
- `high_water_price`.
- `std_value`.

Calculation after activation:

```text
trigger_price =
  high_water_price - params.std_multiple * std_value
```

Trigger after activation:

```text
latest_price <= trigger_price
```

## Market Data Boundary

Add a market-data boundary that can be implemented by QMT and mocked in tests:

```text
get_latest_price(symbol)
get_price_bars(symbol, interval, window)
```

The current broker adapter already exposes latest price through `get_prices`.
Historical bars should be added behind the same broker abstraction. If QMT
history is unavailable for a method, that method must record `needs_data` or
an indicator error and must not trigger.

## Indicator Engine

Add `ConditionIndicatorEngine`:

- Computes ATR from price bars.
- Computes HV from log returns.
- Computes price standard deviation.
- Returns an indicator snapshot.

The indicator engine must be deterministic and unit-tested. It should not own
condition status or order execution.

## Condition Engine Responsibilities

`ConditionEngine` should:

- Load active condition orders.
- Validate required hyperparameters.
- Request latest price and historical bars through the market-data boundary.
- Update high-water state.
- Update take-profit activation state.
- Compute trigger price.
- Persist market state snapshots.
- Emit triggered condition plans.
- Record condition events for state changes and errors.

`ConditionEngine` should not:

- Submit orders directly.
- Report to Xquant directly.
- Invent missing trading parameters.

## Gateway Flow

Arming conditions:

```text
local JSON or Xquant API
-> RebalanceTask
-> extract_condition_orders
-> Storage.upsert_condition_orders
-> condition_order_events: armed
```

Polling conditions:

```text
GatewayService.condition_poll_once
-> Storage.list_active_condition_orders
-> ConditionEngine.evaluate
-> update market state and trigger evidence
-> build condition task and OrderPlan
-> RiskControl.validate
-> ExecutionEngine.execute
-> Storage.record_execution_result
-> Storage.record_condition_trigger_audit
-> XquantAdapter.report_condition_result
```

## Xquant Audit Report

Add an adapter method:

```text
report_condition_result(source_task_id, condition_id, payload)
```

Target endpoint proposal:

```text
POST /api/v1/trading-gateway/tasks/{source_task_id}/condition-results
```

Payload:

```json
{
  "source_task_id": "task-1",
  "condition_id": "cond-513100-atr-tp",
  "condition_task_id": "condition:cond-513100-atr-tp",
  "account_id": "acct",
  "portfolio_id": "prod",
  "symbol": "513100.SH",
  "status": "submitted",
  "trigger": {
    "triggered_at": "2026-06-03T10:30:00+08:00",
    "latest_price": 1.23,
    "trigger_price": 1.18,
    "reason": "latest_price <= trigger_price"
  },
  "rule": {
    "scope": "instrument",
    "purpose": "take_profit",
    "method": "atr_trailing",
    "params": {},
    "action": {}
  },
  "market_state": {
    "latest_price": 1.23,
    "reference_price": 1.0,
    "high_water_price": 1.4,
    "trigger_price": 1.18,
    "activated": true,
    "atr_value": 0.03,
    "hv_value": null,
    "std_value": null,
    "computed_at": "2026-06-03T10:30:00+08:00",
    "market_data_source": "qmt",
    "state_source": "local_sqlite"
  },
  "execution_result": {}
}
```

The audit payload must include:

- Rule identity.
- Hyperparameter snapshot from Xquant.
- Market-derived state snapshot from the gateway.
- Trigger evidence.
- Execution result.

## Idempotency And Reconciliation

Use:

```text
condition_task_id = condition:{condition_id}
```

Rules:

- A terminal condition must not submit another order.
- Xquant report failure must not cause a second trade.
- Audit report retry is separate from execution retry.
- `needs_reconcile` marks local execution complete but Xquant audit report
  incomplete or inconsistent.

## Error Handling

Unsupported method:

- Mark condition failed.
- Record event.
- Do not submit order.

Missing hyperparameter:

- Mark condition failed.
- Record event with missing key.
- Do not submit order.

Missing latest price:

- Record `needs_data`.
- Keep condition active.
- Do not submit order.

Missing historical bars:

- Record `needs_data` or indicator error.
- Keep condition active.
- Do not submit order.

Indicator calculation error:

- Record `indicator_error`.
- Keep condition active unless the rule is invalid.
- Do not submit order.

Execution failure:

- Record execution result.
- Record trigger audit.
- Report failure audit to Xquant.

Xquant audit report failure:

- Store report failure.
- Do not retry execution.
- Allow retrying audit report only.

## Testing Plan

Unit tests:

- Extract every supported method from local JSON.
- Reject unsupported methods.
- Reject missing required hyperparameters.
- Static stop-loss trigger and non-trigger.
- Static take-profit trigger and non-trigger.
- Trailing stop-loss high-water update and trigger.
- Trailing take-profit inactive, activate, trigger, non-trigger.
- ATR calculation.
- ATR stop-loss trigger and non-trigger.
- ATR take-profit activation and trigger.
- HV log-return calculation.
- HV stop-loss trigger and non-trigger.
- HV take-profit activation and trigger.
- Standard deviation calculation.
- Standard deviation stop-loss trigger and non-trigger.
- Standard deviation take-profit activation and trigger.
- No position.
- No sellable quantity.
- Lot rounding.

Integration tests:

- Local JSON mocks Xquant task containing all single-instrument rules.
- `poll_once` arms all supported conditions.
- `condition_poll_once` updates market states.
- Triggered condition executes through `RiskControl` and `ExecutionEngine`.
- SQLite stores rule, market state, trigger audit, and execution result.
- Xquant condition audit report success is recorded.
- Xquant condition audit report failure does not repeat the trade.

Regression tests:

- Existing `static_pct` behavior remains valid.
- Existing `trailing_pct` stop-loss behavior remains valid.
- Existing gateway task, heartbeat, and result-sync tests still pass.

## Documentation Updates

Update:

- `docs/local-condition-orders.md`.
- `docs/conditional-stop-take-profit-rules.md`.
- `docs/xquant-api-contract.md`.

The docs must clearly state:

- Local JSON mocks Xquant task API only.
- SQLite stores local runtime and audit state.
- Xquant sends hyperparameters.
- The gateway computes and stores market-derived state.
- Condition-triggered trades report audit payloads to Xquant.

## Acceptance Criteria

- All single-instrument methods are represented in local JSON schema.
- All hyperparameters are task-supplied.
- No trading threshold is hardcoded in implementation.
- Market-derived state is persisted locally.
- Trigger audits include rule parameters, market state, and execution result.
- Xquant adapter has a condition audit report method.
- Audit report failure does not repeat a trade.
- Existing tests pass.
- New unit and integration tests cover all methods in scope.
