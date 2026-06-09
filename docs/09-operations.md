# 运维操作

## Doctor 诊断

```bash
trade-xquant doctor --config config.yaml
```

检查 Python、当前目录、配置文件是否存在，以及是否能导入 `xtquant`。

## 登录 Xquant

```bash
trade-xquant login --config config.yaml --phone replace-with-phone --send-otp
```

该命令调用：

- `POST /api/v1/auth/otp/send`
- `POST /api/v1/auth/login`

返回的 JWT 会写入：

```text
<config directory>/xquant-token.json
```

后续命令在 `xquant.api_token` 为空时自动读取该文件。
`XQUANT_API_TOKEN` 仍有最高优先级。

## 注册账户

```bash
trade-xquant register-account --config config.yaml
```

在 Xquant 注册或更新当前配置的 gateway account。该命令是幂等的，
修改账户元数据后可以重复运行。

## Heartbeat 心跳

```bash
trade-xquant heartbeat --config config.yaml
```

向 Xquant 发送账户在线状态和 runtime metadata。每次 heartbeat 前，
`trade-xquant` 会用与 `check-qmt` 相同的连接路径检查 QMT，并上传真实
`qmt_connected` 结果。QMT 连接正常时，心跳请求体包含当前
`cash`、`total_asset` 和 `holdings`。

Xquant 应按以下方式展示 heartbeat 状态：

- heartbeat 新鲜且 `qmt_connected=true`: 绿灯。
- heartbeat 新鲜且 `qmt_connected=false`: 黄灯。
- heartbeat 过期或缺失: 红灯。

## 检查 QMT

```bash
trade-xquant check-qmt --config config.yaml
```

该命令连接 MiniQMT，订阅配置中的账户，然后查询账户和持仓。
`connect()` 和 `subscribe()` 都必须返回 `0`。

运行前确认：

- QMT 已安装并登录。
- 已选择正确账户。
- 已勾选 `独立交易`。
- `qmt.userdata_mini_path` 指向 `userdata_mini`。

## Dry Run 试运行

```bash
trade-xquant dry-run --config config.yaml --task-id rebalance_20260527_001
```

dry-run 会拉取任务并生成同样的订单计划，但只记录计划和结果。
它不会调用 `order_stock`。

## Mock Run 模拟运行

```bash
trade-xquant mock-run --config config.yaml --task-id rebalance_20260527_001
```

mock-run 用于 Mac / 本地 Xquant 集成测试。它使用 mock broker，
不连接 QMT，并在任务结果请求体中返回模拟 `submitted_orders`。

Mac / 本地无 QMT 的 Xquant API 测试配置：

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

该模式仍会拉取任务并向 Xquant 上报计划和结果，但账户、持仓、价格和下单
都由本地模拟。

## Poll Once 单次轮询

```bash
trade-xquant poll-once --config config.yaml
```

处理当前待处理任务一次。

## Daemon 守护进程

```bash
trade-xquant daemon --config config.yaml
```

按 `runtime.poll_interval_seconds` 运行同样的 poll loop。每轮也会检查
QMT 后向 Xquant 发送 heartbeat。QMT 可查询时，heartbeat 会刷新 Xquant
里的账户当前持仓；QMT 不可查询时，heartbeat 仍会发送
`qmt_connected=false` 和相关错误。

daemon 也是正式运行推荐入口。它会持续同步条件单、已提交订单、
成交结果和终态结果补报。

## 查看本地状态

```bash
trade-xquant show-status --config config.yaml
```

显示任务状态统计和最近 10 条本地任务。

## SQLite 审计

使用 `sqlite3` 或任意 SQLite viewer：

```bash
sqlite3 data/trade_xquant.db
```

常用查询：

```sql
SELECT task_id, status, received_at, updated_at FROM tasks ORDER BY updated_at DESC;
SELECT task_id, symbol, side, quantity, price, amount FROM planned_orders;
SELECT task_id, status, payload_json, created_at FROM task_results;
SELECT event_type, order_id, symbol, payload_json, created_at FROM order_events;
```

## 真实下单

真实下单风险高，只能在有人值守的交易时段启用：

```yaml
runtime:
  allow_real_order: true
```

```bash
set TRADE_XQUANT_ENABLE_REAL_ORDER=1
trade-xquant poll-once --config config.yaml
```

网关仍会阻止以下真实下单：

- 非交易时段。
- 任务过期。
- 终态任务重复执行。
- 价格未知。
- 账户不匹配。
- 权重非法。
- 订单金额过大。
- 换手过高。
