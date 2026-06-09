# 配置

先复制 `config.example.yaml` 为 `config.yaml`。

## `xquant`

`xquant.base_url`
- Xquant API 前缀，默认 `https://xquant.shop/api/v1`。

`xquant.api_token`
- 网关请求使用的 Bearer token。
- 运行环境中优先使用 `XQUANT_API_TOKEN`。
- 日志会遮蔽该字段。
- 如果未设置，`load_settings` 会读取与 `config.yaml` 同目录的
  `xquant-token.json`。

`xquant-token.json`
- 由 `trade-xquant login` 创建。
- 存放在 `config.yaml` 旁边。
- 被 git 忽略。
- 包含 `/api/v1/auth/login` 返回的 Xquant JWT。

`xquant.product_code`
- 旧配置项。`poll-once` 会忽略它，并始终使用正式 gateway task API：
  `GET /api/v1/trading-gateway/tasks`。
- 新的生产配置不要设置该项。

`xquant.timeout_seconds`
- HTTP 请求超时时间。

`xquant.trust_env`
- HTTP client 是否读取环境变量中的代理设置。
- 默认 `false`，避免 localhost 和网关请求意外走开发机器代理。

## `qmt`

`qmt.userdata_mini_path`
- 必须指向 QMT `userdata_mini`，例如：
  `C:\Apps\QMT\国金证券QMT交易端\userdata_mini`。

`qmt.account_id`
- QMT 证券账户 ID。

`qmt.session_id`
- 可选固定 `xtquant` session ID。
- 推荐保持 `null`，让系统自动生成。

`qmt.session_id_strategy`
- MVP 推荐并支持 `auto`。

`qmt.strategy_name`
- 下单时发送给 QMT 的策略名。

`qmt.cancel_after_order`
- 预留给手工 smoke test。
- 正常运行保持 `false`。

## `runtime`

`runtime.poll_interval_seconds`
- daemon 任务轮询间隔。

`runtime.condition_poll_interval_seconds`
- daemon 条件单价格轮询间隔。
- 默认 `3` 秒。
- 该间隔故意快于任务轮询，因为 active 止损止盈规则不应等待下一次
  Xquant task poll。

`runtime.order_sync_interval_seconds`
- daemon 同步 `submitted` / `partial` 订单生命周期的间隔。
- 默认 `30` 秒。

`runtime.submitted_order_timeout_seconds`
- 已提交但未终态订单的超时时间。
- 默认 `180` 秒。

`runtime.max_rebalance_retries`
- 同一任务超时撤单后最多自动重试次数。
- 默认 `3` 次。

`runtime.allow_real_order`
- 除非明确要启用真实下单，否则必须保持 `false`。

`runtime.dry_run_default`
- 默认 dry-run 偏好。

`runtime.broker_adapter`
- `qmt`: Windows MiniQMT 真实 adapter。
- `mock`: 不连接 QMT 的 Mac / 本地 Xquant API 测试。
- 真实 `qmt` mode 目前尚未向 condition engine 提供历史 K 线。
  在 QMT bar history 接入前，`atr_trailing`、`hv_log_trailing`、
  `std_trailing` 等基于 bar 的条件会在 real QMT mode 记录逐条评估错误。

`runtime.local_task_file`
- 可选本地 JSON 任务文件。
- 设置后，`poll-once`、`dry-run`、`mock-run` 和 `daemon` 会从该文件读取任务，
  不再调用 Xquant task API。
- 仅用于本地开发、联调和回归测试，不属于正式生产契约。
- 生产环境应使用 Xquant 通过 `/trading-gateway/tasks` 下发的正式任务。

`runtime.simulate_real_orders`
- 只用于 `runtime.broker_adapter: "mock"`。
- 为 `true` 时，real-mode 任务由 mock adapter 执行，并报告为模拟提交。
- 只应在受控 Xquant API 集成测试中使用。

`runtime.mock_submit_dry_run_orders`
- 只用于 `runtime.broker_adapter: "mock"`。
- 为 `true` 时，dry-run 任务也会调用 mock broker，并在 `dry_run_success`
  结果中包含 `submitted_orders`。
- 只用于 Xquant 结果请求体的 Mac 集成测试。

`runtime.mock_order_behavior`
- 只用于 `runtime.broker_adapter: "mock"`。
- `filled`: 每个模拟订单都生成已接受订单和完整成交事件。
- `partial_fill`: 每个模拟订单都按 `runtime.mock_partial_fill_ratio`
  生成已接受订单和部分成交事件。
- `reject`: 每个模拟订单都生成订单错误，并使任务失败。

`runtime.mock_partial_fill_ratio`
- `mock_order_behavior: "partial_fill"` 使用的成交比例。
- 默认 `0.5`。

`runtime.mock_total_asset`、`runtime.mock_cash`、`runtime.mock_prices`
- 仅用于 mock broker。
- 每个任务目标标的都必须有 mock price。

`runtime.db_path`
- SQLite 审计数据库路径。

`runtime.log_path`
- JSONL 结构化日志路径。

## `risk`

`risk.max_single_order_amount`
- 单笔订单最大金额。

`risk.min_order_amount`
- 100 股取整后的最小买入订单金额。

`risk.max_turnover_ratio`
- 任务最大换手率。

`risk.cash_buffer_ratio`
- 现金保留比例。

`risk.timezone`
- 默认 `Asia/Shanghai`。

## 真实下单安全门

真实下单需要同时满足：

```yaml
runtime:
  allow_real_order: true
```

以及：

```bash
set TRADE_XQUANT_ENABLE_REAL_ORDER=1
```

缺少任意一个安全门，真实下单都会在到达 QMT 前被拒绝。

## 登录

使用现有 Xquant 登录契约：

```bash
trade-xquant login --config config.yaml --phone replace-with-phone --send-otp
```

或：

```bash
trade-xquant login --config config.yaml --email user@example.com --send-otp
```

已经拿到 OTP 时：

```bash
trade-xquant login --config config.yaml --phone replace-with-phone --otp replace-with-otp
```

token 文件路径为：

```text
<config directory>/xquant-token.json
```
