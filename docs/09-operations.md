# Operations

## Doctor

```bash
trade-xquant doctor --config config.yaml
```

Checks Python, current directory, config presence, and whether `xtquant`
can be imported.

## Login To Xquant

```bash
trade-xquant login --config config.yaml --phone replace-with-phone --send-otp
```

The command calls:

- `POST /api/v1/auth/otp/send`
- `POST /api/v1/auth/login`

It writes the returned JWT to:

```text
<config directory>/xquant-token.json
```

Subsequent commands read this file automatically when `xquant.api_token`
is empty. `XQUANT_API_TOKEN` still has highest priority.

## Register Account

```bash
trade-xquant register-account --config config.yaml
```

Registers or updates the configured gateway account in Xquant. The command
is idempotent and can be run again after changing account metadata.

## Heartbeat

```bash
trade-xquant heartbeat --config config.yaml
```

Sends account liveness and runtime metadata to Xquant. Before each heartbeat,
`trade-xquant` checks QMT with the same connection path as `check-qmt` and
uploads the real `qmt_connected` result. When QMT is connected, the heartbeat
payload includes current `cash`, `total_asset`, and `holdings`.

Xquant should render heartbeat state as:

- fresh heartbeat and `qmt_connected=true`: green.
- fresh heartbeat and `qmt_connected=false`: yellow.
- stale or missing heartbeat: red.

## Check QMT

```bash
trade-xquant check-qmt --config config.yaml
```

This connects to MiniQMT, subscribes the configured account, then queries
account and positions. `connect()` and `subscribe()` must both return `0`.

Before running:

- QMT is installed and logged in.
- The correct account is selected.
- `独立交易` is checked.
- `qmt.userdata_mini_path` points to `userdata_mini`.

## Dry Run

```bash
trade-xquant dry-run --config config.yaml --task-id rebalance_20260527_001
```

Dry-run fetches the task and builds the same order plan, but records only
the plan and result. It does not call `order_stock`.

## Mock Run

```bash
trade-xquant mock-run --config config.yaml --task-id rebalance_20260527_001
```

Mock-run is for Mac/local Xquant integration tests. It uses the mock broker,
does not connect to QMT, and returns simulated `submitted_orders` in the
task result payload.

For Mac/local Xquant API tests without QMT, set:

```yaml
runtime:
  broker_adapter: "mock"
  simulate_real_orders: true
  mock_submit_dry_run_orders: true
  mock_order_behavior: "filled"
  mock_partial_fill_ratio: 0.5
  mock_total_asset: 100000
  mock_cash: 100000
  mock_prices:
    513100.SH: 1.0
    510300.SH: 4.0
```

This still pulls tasks and reports plans/results to Xquant, but account,
positions, prices, and order submission are simulated locally.

## Poll Once

```bash
trade-xquant poll-once --config config.yaml
```

Processes currently pending tasks once.

## Daemon

```bash
trade-xquant daemon --config config.yaml
```

Runs the same poll loop every `runtime.poll_interval_seconds`. Each loop
also sends a heartbeat to Xquant after checking QMT. If QMT can be queried,
the heartbeat refreshes the account's current holdings in Xquant even when
there are no pending tasks. If QMT cannot be queried, the heartbeat still
arrives with `qmt_connected=false` and the related error.

## Show Local Status

```bash
trade-xquant show-status --config config.yaml
```

Shows task counts by status and the 10 most recent local tasks.

## SQLite Audit

Use `sqlite3` or any SQLite viewer:

```bash
sqlite3 data/trade_xquant.db
```

Useful queries:

```sql
SELECT task_id, status, received_at, updated_at FROM tasks ORDER BY updated_at DESC;
SELECT task_id, symbol, side, quantity, price, amount FROM planned_orders;
SELECT task_id, status, payload_json, created_at FROM task_results;
SELECT event_type, order_id, symbol, payload_json, created_at FROM order_events;
```

## Real Orders

Real orders are high risk. Enable only during supervised trading:

```yaml
runtime:
  allow_real_order: true
```

```bash
set TRADE_XQUANT_ENABLE_REAL_ORDER=1
trade-xquant poll-once --config config.yaml
```

The gateway still blocks real orders outside the A-share trading session,
for expired tasks, duplicate terminal tasks, unknown prices, account mismatch,
invalid weights, oversized orders, and excessive turnover.
