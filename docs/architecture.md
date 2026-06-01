# Architecture

## Scope

This MVP is a CLI and background polling service. It does not expose a GUI
and does not accept inbound callbacks from Xquant. The Windows gateway pulls
tasks from Xquant and executes through the local MiniQMT process.

Xquant is the primary system of record for gateway tasks, plans, submitted
orders, events, trades, and task results. The local SQLite database is kept
for idempotency, cross-checking, and debugging.

## Components

`XquantAdapter`
- Logs in through the Xquant auth API and stores a local JWT token file.
- Registers the account and sends periodic heartbeat data.
- Pulls pending target-weight tasks from `/trading-gateway/tasks`.
- Reports order plans, submitted orders, events, trades, and final results.
- Supports the temporary latest-signal endpoint only when
  `xquant.product_code` is set.

`QmtAdapter`
- Wraps `xtquant.xttrader.XtQuantTrader`.
- Connects to `userdata_mini`, registers callbacks, subscribes the account,
  queries assets, holdings, orders, trades, prices, and sends `order_stock`.
- Normalizes QMT callback objects into `QmtGatewayEvent`.

`PortfolioEngine`
- Converts target weights, account assets, holdings, and prices into orders.
- Uses a conservative rule algorithm:
  sell unwanted exposure first, reserve cash buffer, then buy by target gap.
- Enforces 100-share lots for buys and sells.

`RiskControl`
- Validates pre-trade gates before execution.
- Blocks expired, duplicate, account-mismatched, invalid-weight, unknown-price,
  oversized, high-turnover, and unsafe real-order tasks.

`ExecutionEngine`
- Records dry-run plans without submitting.
- In real mode, checks the double real-order gate and submits one order at a
  time through the broker adapter.
- MVP does not implement chasing or slicing.

`Storage`
- SQLite audit database.
- Tables: `tasks`, `target_positions`, `planned_orders`,
  `submitted_orders`, `order_events`, `trades`, `task_results`.
- Terminal tasks are not reprocessed unless explicitly reset.

## Data Flow

1. `daemon` or `poll-once` starts and loads `config.yaml`.
2. The gateway fetches pending formal tasks from Xquant.
3. Each task is claimed in SQLite.
4. QMT account, positions, and prices are queried.
5. `PortfolioEngine` builds an order plan.
6. `RiskControl` validates the task and plan.
7. Dry-run records the plan only; real-run submits orders through QMT.
8. SQLite stores plans, submitted orders, callback events, and task result.
9. Xquant receives plan and result reports.

## Xquant Task Mode

The default mode assumes Xquant implements:

```text
GET /api/v1/trading-gateway/tasks
POST /api/v1/trading-gateway/tasks/{task_id}/plan
POST /api/v1/trading-gateway/tasks/{task_id}/result
```

Xquant creates account-scoped tasks when a subscribed portfolio generates a
new signal. The task remains valid until Xquant supersedes it with the next
signal or returns it in a terminal state. `expires_at` may be `null` in this
formal contract.

For the current transitional environment, setting `xquant.product_code`
switches the adapter to:

```text
GET /api/v1/internal/products/{product_code}/signal/latest
```

That fallback constructs a local dry-run task and does not report plan/result
to Xquant.

## Source References

Read during design:

- Local `hello.py`, already validated against MiniQMT.
- `docs/qmt-miniqmt-setup-and-hello-validation.md`.
- QMT Python API PDF sections on `passorder`, callbacks, order objects,
  deal objects, and price/order type constants.
- GitHub `xingwudao/xquant`, especially API contract and signal/run models.
- GitHub `xingwudao/open-xquant/src/oxq/contrib`, especially the Alpaca
  adapter shape that keeps broker-specific REST/WebSocket code isolated.
