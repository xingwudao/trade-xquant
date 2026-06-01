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
