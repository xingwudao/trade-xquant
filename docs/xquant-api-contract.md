# Xquant Trading Gateway API Contract

## Background

Xquant generates account-scoped trading tasks when a subscribed portfolio
produces a new signal on its calculation day. A task remains valid until a
new signal supersedes it or Xquant marks it terminal.

The local gateway uses this contract to authenticate, register its account,
pull executable tasks, and report plans and execution results.

## Authentication

Use one of:

- `Authorization: Bearer <token>`
- A future internal token header if Xquant chooses service-token auth.

The MVP sends `Authorization` when `xquant.api_token` is configured.

## Register Account

`POST /api/v1/trading-gateway/accounts`

Payload:

```json
{
  "account_id": "replace-with-qmt-account-id",
  "broker": "gjzq_qmt",
  "client_type": "qmt_mini",
  "enabled": true,
  "meta": {
    "gateway_version": "0.1.0"
  }
}
```

The endpoint should be idempotent by `account_id`.

## Heartbeat

`POST /api/v1/trading-gateway/accounts/{account_id}/heartbeat`

Payload:

```json
{
  "status": "online",
  "meta": {
    "version": "0.1.0"
  }
}
```

## Fetch Pending Tasks

`GET /api/v1/trading-gateway/tasks?account_id={account_id}&limit=10`

Response:

```json
{
  "tasks": [
    {
      "task_id": "rebalance_20260527_001",
      "portfolio_id": "demo_etf_rotation",
      "account_id": "replace-with-qmt-account-id",
      "mode": "dry_run",
      "signal_as_of_date": "2026-05-20",
      "signal_effective_date": "2026-05-21",
      "created_at": "2026-05-27T09:35:00+08:00",
      "expires_at": null,
      "cash_buffer_ratio": 0.002,
      "targets": [
        {"symbol": "513100.SH", "target_weight": 0.5},
        {"symbol": "510300.SH", "target_weight": 0.5}
      ],
      "constraints": {
        "max_turnover_ratio": 0.8,
        "max_single_order_amount": 50000,
        "min_order_amount": 1000
      }
    }
  ]
}
```

Rules:

- `task_id` is globally unique and idempotent.
- `account_id` must match the local QMT account.
- `signal_as_of_date` is the signal calculation date.
- `signal_effective_date` is the date encoded into rebalance task IDs.
- Target weights must sum to `<= 1`; remainder is cash.
- `expires_at` may be `null`; when null, validity lasts until superseded.
- Xquant should stop returning superseded or terminal tasks as pending.
- Unsupported asset classes must not be sent to this MVP.

## Manual Task Preview

`POST /api/v1/trading-gateway/products/{product_code}/manual-tasks/preview`

Payload:

```json
{
  "account_id": "replace-with-qmt-account-id",
  "mode": "dry_run"
}
```

Response:

```json
{
  "product_code": "prod_leading_stocks_rotation",
  "account_id": "replace-with-qmt-account-id",
  "mode": "dry_run",
  "trigger_type": "manual",
  "signal_as_of_date": "2026-05-20",
  "signal_effective_date": "2026-05-21",
  "cash_buffer_ratio": "0.002",
  "targets": [
    {"symbol": "300308.SZ", "target_weight": "0.1867535594777999"}
  ],
  "constraints": {
    "max_turnover_ratio": 0.8,
    "max_single_order_amount": 50000,
    "min_order_amount": 1000
  },
  "preview_token": "opaque-confirmation-token"
}
```

Rules:

- Preview must not create a task.
- `preview_token` is required by the confirm endpoint.
- Xquant should generate preview data from the actionable delta signal,
  not from an empty latest target-only signal.

## Manual Task Confirm

`POST /api/v1/trading-gateway/products/{product_code}/manual-tasks`

Payload:

```json
{
  "account_id": "replace-with-qmt-account-id",
  "mode": "dry_run",
  "preview_token": "opaque-confirmation-token"
}
```

Response:

```json
{
  "ok": true,
  "task_id": "manual_rebalance_prod_leading_stocks_rotation_20260521_xxx_dry_run_abc",
  "status": "pending",
  "trigger_type": "manual"
}
```

The created task should then be returned by `GET /trading-gateway/tasks`
until claimed and completed by the gateway.

## Report Plan

`POST /api/v1/trading-gateway/tasks/{task_id}/plan`

Payload is the local `OrderPlan` JSON:

```json
{
  "task_id": "rebalance_20260527_001",
  "account_id": "replace-with-qmt-account-id",
  "total_asset": 100000,
  "turnover_amount": 50000,
  "turnover_ratio": 0.5,
  "orders": [
    {
      "symbol": "513100.SH",
      "side": "buy",
      "quantity": 10000,
      "price": 5,
      "amount": 50000
    }
  ]
}
```

## Report Result

`POST /api/v1/trading-gateway/tasks/{task_id}/result`

Payload:

```json
{
  "status": "success",
  "mode": "dry_run",
  "planned_orders": [],
  "submitted_orders": [],
  "trades": [],
  "events": [],
  "errors": [],
  "meta": {}
}
```

Status values:

- `success`
- `failed`
- `dry_run_success`
- `submitted`
- `superseded`

Xquant should treat repeated result reports for the same `task_id` as
idempotent updates.

## Report Condition Result

`POST /api/v1/trading-gateway/tasks/{source_task_id}/condition-results`

The gateway calls this endpoint after a condition rule triggers and local
condition execution has produced an `ExecutionResult`.

Payload includes:

- `source_task_id`.
- `condition_id`.
- `condition_task_id`.
- `account_id`.
- `portfolio_id`.
- `symbol`.
- `status`.
- `trigger` with `triggered_at`, `latest_price`, `trigger_price`, and
  `reason`.
- `rule` with `scope`, `purpose`, `method`, `params`, and `action`.
- `market_state` with latest price, high-water price, trigger price,
  activation state, indicator values, computed timestamp, market data
  source, and nested local state fields.
- `execution_result`, the local `ExecutionResult` JSON.

Example values are illustrative. Numeric rule parameters are task-supplied
values determined by Xquant-side research, backtesting, and configuration.
Market-state values are gateway-derived runtime snapshots, not hardcoded
thresholds or defaults.

```json
{
  "source_task_id": "task-20260603-001",
  "condition_id": "cond-513100-atr-tp",
  "condition_task_id": "condition:cond-513100-atr-tp",
  "account_id": "acct",
  "portfolio_id": "prod_etf_steady",
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
  },
  "market_state": {
    "latest_price": 1.23,
    "high_water_price": 1.4,
    "trigger_price": 1.18,
    "activated": true,
    "activated_at": "2026-06-03T10:20:00+08:00",
    "activation_price": 1.12,
    "atr_value": 0.03,
    "hv_value": null,
    "std_value": null,
    "computed_at": "2026-06-03T10:30:00+08:00",
    "market_data_source": "qmt",
    "state": {
      "method": "atr_trailing",
      "purpose": "take_profit",
      "params": {
        "activation_profit_pct": 0.12,
        "atr_window": 14,
        "atr_multiple": 2.0,
        "bar_interval": "1d"
      },
      "activation_price": 1.12
    }
  },
  "execution_result": {
    "task_id": "condition:cond-513100-atr-tp",
    "status": "submitted",
    "mode": "dry_run",
    "planned_orders": [
      {
        "task_id": "condition:cond-513100-atr-tp",
        "symbol": "513100.SH",
        "side": "sell",
        "quantity": 1000,
        "price": 1.23,
        "amount": 1230.0
      }
    ],
    "submitted_orders": [],
    "trades": [],
    "events": [],
    "holdings": [],
    "cash": null,
    "total_asset": null,
    "errors": [],
    "meta": {}
  }
}
```

Rules:

- `condition_task_id` is idempotent.
- A failed condition-result report must not cause repeated trading.
- Xquant should accept repeated audit reports for the same
  `condition_task_id` idempotently.
