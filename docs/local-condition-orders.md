# Local Condition Order JSON

This file documents the temporary local JSON contract used while the Xquant
server-side task emitter is still being developed.

The goal is for Xquant to later emit the same shape through the formal
`/trading-gateway/tasks` API. The local file is only a development source.

## Config

Set:

```yaml
runtime:
  local_task_file: "fixtures/local-condition-task.json"
  broker_adapter: "mock"
  mock_submit_dry_run_orders: true
```

Then run an existing command, for example:

```bash
trade-xquant mock-run --config config.yaml
```

No CLI flag is required.

## File Shape

The file may contain either:

```json
{
  "tasks": []
}
```

or a top-level task list:

```json
[]
```

Each task is the existing `RebalanceTask` shape. Stop-loss and take-profit
condition orders live under `constraints.condition_orders`.

## Example

```json
{
  "tasks": [
    {
      "task_id": "task-20260603-001",
      "portfolio_id": "prod_etf_steady",
      "account_id": "acct",
      "mode": "dry_run",
      "created_at": "2026-06-03T09:35:00+08:00",
      "expires_at": null,
      "targets": [
        {
          "symbol": "513100.SH",
          "target_weight": 0.5
        }
      ],
      "constraints": {
        "max_turnover_ratio": 0.8,
        "condition_orders": [
          {
            "condition_id": "cond-513100-stop-001",
            "symbol": "513100.SH",
            "purpose": "stop_loss",
            "method": "static_pct",
            "reference_price": 1.0,
            "params": {
              "stop_loss_pct": 0.05
            },
            "action": {
              "type": "sell_pct",
              "pct": 1.0
            }
          },
          {
            "condition_id": "cond-513100-take-001",
            "symbol": "513100.SH",
            "purpose": "take_profit",
            "method": "static_pct",
            "reference_price": 1.0,
            "params": {
              "take_profit_pct": 0.1
            },
            "action": {
              "type": "sell_pct",
              "pct": 0.5
            }
          },
          {
            "condition_id": "cond-513100-trail-001",
            "symbol": "513100.SH",
            "purpose": "stop_loss",
            "method": "trailing_pct",
            "reference_price": 1.0,
            "high_water_price": 1.0,
            "params": {
              "trail_pct": 0.08
            },
            "action": {
              "type": "sell_pct",
              "pct": 1.0
            }
          }
        ]
      }
    }
  ]
}
```

## Supported MVP Rules

`static_pct` with `purpose: "stop_loss"`
- Trigger price:
  `reference_price * (1 - params.stop_loss_pct)`
- Trigger condition:
  latest price is less than or equal to trigger price.

`static_pct` with `purpose: "take_profit"`
- Trigger price:
  `reference_price * (1 + params.take_profit_pct)`
- Trigger condition:
  latest price is greater than or equal to trigger price.

`trailing_pct`
- High-water price is updated locally when latest price makes a new high.
- Trigger price:
  `high_water_price * (1 - params.trail_pct)`
- Trigger condition:
  latest price is less than or equal to trigger price.

## Execution

When a condition triggers, trade-xquant creates a normal sell `PlannedOrder`.

The order uses:

```text
task_id = condition:{condition_id}
remark = cond:{condition_id}
price_type = latest
```

The order then goes through the existing `RiskControl` and `ExecutionEngine`.

Condition state is stored in SQLite:

- `condition_orders`
- `condition_order_events`

Active states are:

- `received`
- `armed`

Terminal or non-active states are:

- `triggered`
- `submitting`
- `submitted`
- `completed`
- `expired`
- `canceled`
- `failed`
- `needs_reconcile`
