# 系统架构

## 范围

当前 MVP 是一个 CLI 和后台轮询服务。它不提供 GUI，也不接收 Xquant
主动回调。Windows 网关从 Xquant 拉取任务，并通过本地 MiniQMT 执行。

Xquant 是网关任务、订单计划、已提交委托、事件、成交和任务结果的主系统。
本地 SQLite 数据库用于幂等、交叉校验和问题排查。

## 组件

`XquantAdapter`
- 通过 Xquant auth API 登录，并保存本地 JWT token 文件。
- 注册交易账户，并定期发送 heartbeat。
- 从 `/trading-gateway/tasks` 拉取待处理的目标权重任务。
- 回传订单计划、已提交委托、事件、成交和最终结果。

`QmtAdapter`
- 封装 `xtquant.xttrader.XtQuantTrader`。
- 连接 `userdata_mini`，注册回调，订阅账户。
- 查询资产、持仓、委托、成交和价格，并调用 `order_stock` 下单。
- 将 QMT 回调对象标准化为 `QmtGatewayEvent`。

`PortfolioEngine`
- 将目标权重、账户资产、持仓和价格转换为订单。
- 使用保守的规则算法：
  先卖出不需要的敞口，保留现金 buffer，再按目标缺口买入。
- 买入和卖出都强制使用 100 股整数手。

`RiskControl`
- 在执行前校验交易安全门。
- 阻止过期、重复、账户不匹配、权重非法、价格未知、金额过大、
  换手过高和不安全的真实下单任务。

`ExecutionEngine`
- dry-run 只记录计划，不提交委托。
- real mode 会检查双真实下单安全门，并通过 broker adapter 逐笔提交订单。
- MVP 不实现追单或拆单算法。

`Storage`
- SQLite 审计数据库。
- 表包括：`tasks`、`target_positions`、`planned_orders`、
  `submitted_orders`、`order_events`、`trades`、`task_results`。
- 终态任务不会重复处理，除非显式重置。

## 数据流

1. `daemon` 或 `poll-once` 启动并加载 `config.yaml`。
2. 网关从 Xquant 拉取待处理的正式任务。
3. 每个任务在 SQLite 中 claim。
4. 普通真实任务如果不在有效交易 session，先检查双安全门。
   安全门缺失时终态失败；安全门已打开时进入本地 `pending_execution`，
   等 session 打开后重试。
5. 对需要立即执行的任务，查询 QMT 账户、持仓和价格。
6. `PortfolioEngine` 生成订单计划。
7. `RiskControl` 校验任务和计划。
8. dry-run 只记录计划；real-run 通过 QMT 提交订单。
9. SQLite 存储计划、已提交委托、回调事件和任务结果。
10. Xquant 接收计划和结果上报。
11. daemon mode 下，每轮还会发送 heartbeat。如果 QMT 可查询，
    heartbeat 会包含当前 `cash`、`total_asset` 和 `holdings`。

## Xquant 任务模式

默认模式假设 Xquant 实现这些接口：

```text
GET /api/v1/trading-gateway/tasks
POST /api/v1/trading-gateway/tasks/{task_id}/plan
POST /api/v1/trading-gateway/tasks/{task_id}/result
```

当订阅组合产生新信号时，Xquant 为账户创建任务。任务在被新信号取代前，
或被 Xquant 标记为终态前保持有效。正式合约中 `expires_at`
可以为 `null`。

`poll-once` 总是使用正式 gateway task API。旧的 `xquant.product_code`
配置会被忽略，这样每个执行任务都能绑定到明确的 Xquant `task_id`。

## 来源参考

- `docs/02-qmt-miniqmt-setup-and-validation.md`。
- 本地 `hello.py` 验证脚本。
- QMT PDF 手册位于 `docs/qmt/`。
