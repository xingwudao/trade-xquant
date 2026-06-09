# Daemon 订单生命周期

## 背景

`trade-xquant daemon` 是未来主要使用方式。它不只负责从 Xquant
拉取新的交易任务，也要在没有新任务时持续维护已提交订单的生命周期。

daemon 需要自动完成三类同步：

- 从 Xquant 获取新的交易任务并执行。
- 同步止盈止损等条件单规则。
- 同步 QMT 账户、委托、成交和任务结果。

这样 Windows 端网关可以长期运行，并把 Xquant 页面上的账户状态、
持仓状态、任务状态和订单状态保持为最新。

## 配置项

订单生命周期由 `runtime` 下列配置控制：

```yaml
runtime:
  order_sync_interval_seconds: 30
  submitted_order_timeout_seconds: 180
  max_rebalance_retries: 3
```

含义：

- `runtime.order_sync_interval_seconds`
  控制 daemon 同步已提交订单的频率，默认 `30` 秒。
- `runtime.submitted_order_timeout_seconds`
  控制已提交但未终态订单的超时时间，默认 `180` 秒。
- `runtime.max_rebalance_retries`
  控制同一任务最多自动重试次数，默认 `3` 次。

## 主循环职责

daemon 循环中会周期性执行：

- 拉取并处理 Xquant 待处理任务。
- 同步活跃条件单。
- 同步 `submitted` 和 `partial` 任务的 QMT 委托与成交。
- 发送 heartbeat，并上传 QMT 连接状态、现金、总资产和持仓。

如果 Xquant 没有新交易任务，daemon 仍会继续同步条件单、
已提交订单、成交结果和 heartbeat。

## 订单状态同步

daemon 通过 QMT 查询当前委托和成交，并回写本地 SQLite 与 Xquant。

状态规则：

- 全部成交后，任务结果为 `success`。
- 部分成交或部分订单仍待成交，任务结果为 `partial`。
- 未成交且未超时，任务结果保持 `submitted`。
- QMT 已拒单或失败，按失败信息记录到任务结果。

同步必须保留实时委托可见性。只要 QMT 仍可能存在活跃委托，
本地任务就不能被错误写成不可同步的终态。

## 超时撤单和重试

当订单超过 `runtime.submitted_order_timeout_seconds` 仍未终态时，
daemon 会进入超时处理。

处理顺序：

1. 先同步 QMT 当前委托和成交。
2. 只把 QMT 当前仍待成交的订单作为撤单候选。
3. 撤单前执行重试预检查和风控检查。
4. 撤单成功后，刷新账户、持仓和价格。
5. 按原任务目标权重重新生成调仓计划。
6. 在 `runtime.max_rebalance_retries` 预算内重新下单。
7. 将新的委托继续记录为 `submitted` 或 `partial` 并持续同步。

不会使用历史 `submitted_orders` 作为撤单依据。历史订单只作为审计记录，
不能授权 daemon 再次撤同一个旧订单。

## 安全边界

daemon 必须避免重复活跃委托和隐藏实时委托。

安全规则：

- 撤单失败时不重试。
- QMT 撤单返回码必须为 `0` 才算成功。
- 撤单返回非 `0` 或抛异常时，任务保持可同步状态。
- 风控或预检查阻止重试时，不撤单、不重试。
- 预检查阻止重试不消耗 `retry_count`。
- 有实时待成交委托时，不能把任务写成不可同步终态。
- Xquant 上报失败不能影响本地实时委托继续同步。
- 重试部分下单成功、部分失败时，已接受的实时委托必须继续同步。
- 已撤单尝试不应阻止后续重试成交后进入 `success`。

`retry_count` 只在实际进入撤单后重试流程时增加。单纯的预检查
失败或风控阻止不应消耗重试预算。

## 上报和补报

daemon 会把计划和结果上报给 Xquant：

- 普通任务使用任务结果接口。
- 条件单任务使用条件单结果接口。
- 重试生成的新计划也需要上报。
- 终态失败和终态无操作的上报失败必须持久化。
- 后续同步周期需要补报失败的终态生命周期结果。

本地 SQLite 是 daemon 的审计和恢复依据。即使 Xquant 临时上报失败，
本地也要保留足够的任务结果、错误信息、委托、成交和生命周期元数据，
以便后续补报或继续同步。

## 条件单任务

条件单任务也可能产生真实委托。

要求：

- 条件单任务的 submitted/partial 状态也参与订单生命周期同步。
- 超时后遵守同样的撤单、风控和重试安全规则。
- 重试结果必须走条件单结果接口。
- 条件单结果上报失败时，需要保持可补报状态。

## 运维预期

推荐把 daemon 作为长期运行进程部署在 Windows QMT 机器上。

运行前确认：

- MiniQMT 已登录。
- `userdata_mini` 路径正确。
- QMT 已勾选 `独立交易`。
- `runtime.allow_real_order` 和 `TRADE_XQUANT_ENABLE_REAL_ORDER`
  只在有人值守的实盘交易时段开启。
- 磁盘空间充足，尤其是 QMT `userdata_mini` 目录。

出现 QMT 连接失败时，daemon 仍会发送心跳，并把错误写入
`last_error`。错误文本需要去重和截断，避免超过 Xquant API 限制。

## 验收口径

本需求完成后应满足：

- daemon 无新任务时仍会同步条件单和已提交订单。
- 已成交订单能回写 `success`。
- 部分成交订单能保持 `partial` 并继续同步。
- 超时待成交订单只撤 QMT 当前仍待成交的订单。
- 撤单失败不会下新单，且原实时委托不会被隐藏。
- 重试不超过 `runtime.max_rebalance_retries`。
- 重试成功下出的实时委托会继续同步直到终态。
- 条件单任务重试使用条件单结果接口。
- 终态生命周期结果上报失败后可以补报。
- 全量测试通过。
