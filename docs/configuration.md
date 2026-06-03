# Configuration

Copy `config.example.yaml` to `config.yaml`.

## `xquant`

`xquant.base_url`
- Xquant API prefix, default `https://xquant.shop/api/v1`.

`xquant.api_token`
- Bearer token for gateway calls.
- Prefer `XQUANT_API_TOKEN` in the runtime environment.
- Logs mask this field.
- If unset, `load_settings` reads `xquant-token.json` from the same
  directory as `config.yaml`.

`xquant-token.json`
- Created by `trade-xquant login`.
- Stored beside `config.yaml`.
- Ignored by git.
- Contains the Xquant JWT returned by `/api/v1/auth/login`.

`xquant.product_code`
- Legacy key. `poll-once` ignores it and always uses the formal gateway
  task API: `GET /api/v1/trading-gateway/tasks`.
- Do not set this in new production configs.

`xquant.timeout_seconds`
- HTTP request timeout.

`xquant.trust_env`
- Whether HTTP client reads proxy settings from environment variables.
- Default `false`, so localhost and gateway calls are not accidentally routed
  through a developer machine proxy.

## `qmt`

`qmt.userdata_mini_path`
- Must point to QMT `userdata_mini`, for example:
  `C:\Apps\QMT\国金证券QMT交易端\userdata_mini`.

`qmt.account_id`
- QMT stock account ID.

`qmt.session_id`
- Optional fixed `xtquant` session ID.
- Leave `null` for automatic generation.

`qmt.session_id_strategy`
- MVP supports `auto` as the recommended mode.

`qmt.strategy_name`
- QMT strategy name sent with orders.

`qmt.cancel_after_order`
- Reserved for manual smoke tests.
- Keep `false` in normal operation.

## `runtime`

`runtime.poll_interval_seconds`
- Daemon polling interval.

`runtime.condition_poll_interval_seconds`
- Daemon condition-order price polling interval.
- Default `3` seconds.
- This is intentionally faster than task polling because active stop/take-profit
  rules should not wait for the next Xquant task poll.

`runtime.allow_real_order`
- Must remain `false` unless intentionally enabling real orders.

`runtime.dry_run_default`
- Default dry-run preference.

`runtime.broker_adapter`
- `qmt` for the real Windows MiniQMT adapter.
- `mock` for Mac/local Xquant API tests without connecting to QMT.
- Real `qmt` mode does not yet provide historical price bars to the condition
  engine. Bar-based `atr_trailing`, `hv_log_trailing`, and `std_trailing`
  conditions will record per-order evaluation errors in real QMT mode until
  QMT bar history is wired.

`runtime.local_task_file`
- Optional local JSON task file.
- When set, `poll-once`, `dry-run`, `mock-run`, and `daemon` read tasks from
  this file instead of calling Xquant task APIs.
- This is for developing the trade-xquant side of the contract before Xquant
  emits matching gateway tasks.
- The schema is documented in `docs/local-condition-orders.md`.

`runtime.simulate_real_orders`
- Used only with `runtime.broker_adapter: "mock"`.
- When `true`, real-mode tasks are executed by the mock adapter and reported
  as simulated submissions.
- Keep `false` outside controlled Xquant API integration tests.

`runtime.mock_submit_dry_run_orders`
- Used only with `runtime.broker_adapter: "mock"`.
- When `true`, dry-run tasks also call the mock broker and include
  `submitted_orders` in the `dry_run_success` result.
- This is for Mac integration tests of the Xquant result payload only.

`runtime.mock_order_behavior`
- Used only with `runtime.broker_adapter: "mock"`.
- `filled`: every mock order emits accepted order and full trade events.
- `partial_fill`: every mock order emits accepted order and partial trade
  events using `runtime.mock_partial_fill_ratio`.
- `reject`: every mock order emits an order error and the task fails.

`runtime.mock_partial_fill_ratio`
- Fill ratio used by `mock_order_behavior: "partial_fill"`.
- Default `0.5`.

`runtime.mock_total_asset`, `runtime.mock_cash`, `runtime.mock_prices`
- Used only when `runtime.broker_adapter` is `mock`.
- Every task target symbol must have a mock price.

`runtime.db_path`
- SQLite audit database path.

`runtime.log_path`
- JSONL structured log path.

## `risk`

`risk.max_single_order_amount`
- Maximum amount for one order.

`risk.min_order_amount`
- Minimum buy order amount after lot rounding.

`risk.max_turnover_ratio`
- Maximum task turnover ratio.

`risk.cash_buffer_ratio`
- Cash reserve ratio.

`risk.timezone`
- Default `Asia/Shanghai`.

## Real Order Gates

Real orders require both:

```yaml
runtime:
  allow_real_order: true
```

and:

```bash
set TRADE_XQUANT_ENABLE_REAL_ORDER=1
```

Without both gates, real orders are rejected before reaching QMT.

## Login

Use the existing Xquant login contract:

```bash
trade-xquant login --config config.yaml --phone replace-with-phone --send-otp
```

or:

```bash
trade-xquant login --config config.yaml --email user@example.com --send-otp
```

When you already have the OTP:

```bash
trade-xquant login --config config.yaml --phone replace-with-phone --otp replace-with-otp
```

The token file path is:

```text
<config directory>/xquant-token.json
```
