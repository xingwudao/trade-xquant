# 详细设计

## 1. 项目目标和边界

`trade-xquant` 是 Xquant 的 Windows QMT / MiniQMT 交易网关。它运行在
安装并登录 MiniQMT 的 Windows 机器上，从 Xquant 拉取交易任务，
使用本地 QMT 账户数据生成订单计划，执行风控和下单，并把计划、委托、
成交、任务结果和 heartbeat 回传给 Xquant。

系统边界：

- Xquant 是任务、计划、结果和页面展示的主系统。
- trade-xquant 是本地执行网关和审计系统。
- MiniQMT / QMT 是证券账户、行情、委托和成交来源。
- SQLite 是本地幂等、审计和恢复依据。

更完整的组件和数据流说明见 `docs/01-system-architecture.md`。

## 2. 运行环境和部署拓扑

正式运行环境是 Windows + 国金 QMT / MiniQMT + Python 虚拟环境。
Mac / Linux 只用于 Xquant API 和 mock broker 联调，不连接真实 QMT，
也不做真实下单。

部署关系：

```text
Xquant <-> trade-xquant daemon <-> MiniQMT / QMT <-> 券商账户
                         |
                         +-> local SQLite audit database
```

QMT / MiniQMT 申请、安装、hello 验证和设计启发见
`docs/02-qmt-miniqmt-setup-and-validation.md`。

## 3. 配置设计

配置从 `config.yaml` 加载，主要分为：

- `xquant`: API 地址、token、HTTP 设置。
- `qmt`: `userdata_mini`、账户 ID、session 和策略名。
- `runtime`: daemon 频率、broker 类型、数据库、日志、mock 参数。
- `risk`: 单笔金额、最小金额、最大换手率、现金 buffer 和时区。

真实下单需要同时打开：

- `runtime.allow_real_order: true`
- 环境变量 `TRADE_XQUANT_ENABLE_REAL_ORDER=1`

配置项详解见 `docs/03-configuration.md`。

## 4. Xquant 网关接口契约

trade-xquant 通过 Xquant gateway API 完成：

- 登录和 token 管理。
- 注册或更新账户。
- 心跳。
- 拉取待处理任务。
- 回传订单计划。
- 回传任务结果。
- 回传条件单结果。
- 手动任务预览和确认。

接口路径、请求体、响应体和状态含义见
`docs/04-xquant-api-contract.md`。

## 5. QMT 集成设计

`QmtAdapter` 封装 `xtquant.xttrader.XtQuantTrader`，负责：

- 连接 `userdata_mini`。
- 注册回调。
- 订阅证券账户。
- 查询资金、持仓、委托、成交和价格。
- 调用 `order_stock` 下单。
- 调用 `cancel_order_stock` 撤单。

QMT 连接和订阅必须返回 `0` 才算成功。下单返回 `-1` 是失败。
撤单返回码必须为 `0` 才算成功，非 `0` 必须按失败处理。

运行状态、回调、连接故障和常见排查见
`docs/05-qmt-runtime-notes.md`。

## 6. 任务生命周期

任务生命周期从 Xquant 待处理任务开始：

1. daemon 或 `poll-once` 拉取待处理任务。
2. 本地 SQLite 记录 task claim，保证幂等。
3. 如果是普通真实任务且当前不在有效交易 session，先检查真实下单
   双安全门。安全门缺失时终态失败；安全门已打开时进入
   `pending_execution`，等待 session 打开后重新执行。
4. 查询 QMT 账户、持仓和价格。
5. `PortfolioEngine` 生成订单计划。
6. `RiskControl` 校验账户、过期时间、权重、价格、金额、换手率和交易时段。
7. 试运行只记录计划和结果。
8. 真实下单模式在双安全门打开后下单。
9. 本地记录计划、委托、事件、成交和结果。
10. Xquant 接收计划和结果。

`pending_execution` 是本地等待状态，不代表已经向 QMT 提交委托。
普通真实任务只有在双安全门已经打开、但当前不在有效交易 session 时
才进入该状态。进入有效交易 session 后，daemon 会重新取账户、持仓、
价格并重新生成计划；任务过期或其他不可恢复风控错误会转为 `failed`。

终态任务不能被重复执行，除非显式重置本地和 Xquant 状态。

## 7. 订单计划和风控设计

订单计划以目标权重为输入，以 QMT 当前账户快照、持仓和价格为基础。

核心规则：

- 先卖出不需要或超配仓位。
- 保留现金 buffer。
- 买入目标缺口。
- 买卖都按 100 股整数手处理。
- 过滤低于最小买入金额的订单。

风控规则：

- 账户 ID 必须一致。
- 任务不能过期。
- 目标权重不能超过 100%。
- 标的必须有价格。
- 单笔金额不能超过阈值。
- 换手率不能超过阈值。
- 真实下单模式必须在 A 股交易时段和双安全门下运行。
- 非交易 session 的延后逻辑不能绕过双安全门；安全门缺失必须终态失败。

目标权重、持仓和实时市价如何转换成订单，详见
`docs/06-order-generation-algorithm.md`。

## 8. 守护进程运行设计

daemon 是正式日常使用方式。它持续执行：

- 待处理任务轮询。
- 活跃条件单轮询。
- submitted / partial 订单同步。
- 心跳。
- QMT 账户状态同步。

即使 Xquant 没有新交易任务，daemon 仍需要同步条件单、
已提交订单、成交结果和心跳。订单生命周期详见
`docs/07-daemon-order-lifecycle.md`。

## 9. 已提交订单生命周期

daemon 会周期性同步 QMT 当前委托和成交。

状态规则：

- 全部成交: `success`。
- 部分成交或仍有待成交委托: `partial`。
- 未成交且未超时: `submitted`。
- 失败或拒单: 记录失败信息。

超时后只把 QMT 当前仍待成交的订单作为撤单候选。撤单成功后，
daemon 刷新账户、持仓和价格，并在重试预算内按原目标权重重试。

安全要求：

- 历史 `submitted_orders` 不能授权再次撤单。
- 撤单失败不重试，且实时委托继续可同步。
- 预检查阻止不消耗重试预算。
- 已接受的实时委托不能被终态失败隐藏。
- 终态失败和终态无操作上报失败必须可补报。

详见 `docs/07-daemon-order-lifecycle.md`。

## 10. 条件单设计

条件单用于止损、止盈和追踪类规则。

规则范围：

- 单标的止损。
- 单标的止盈。
- 组合级止损。
- 组合级止盈。
- 静态百分比、固定比例追踪、ATR、HV、标准差等参数化规则。

条件单任务也可能产生真实委托。其 submitted / partial 生命周期遵守
普通任务同样的安全规则，但重试结果必须走条件单结果接口。

真实 QMT 条件单在非有效交易 session 内不会轮询实时价格，也不会触发
下单链路。`pending_reference` 的真实 QMT 条件单也不会刷新
position-cost reference。dry-run 和 mock simulated-real 条件单不受
真实交易 session 限制；如果同一轮混合存在 dry-run 和真实 QMT 条件单，
daemon 必须把 session 过滤后的条件单列表贯穿 reference refresh、
价格查询和触发评估。

规则体系见 `docs/08-conditional-stop-take-profit-rules.md`。

## 11. 本地存储和审计

SQLite 用于幂等、审计、排查和恢复。

核心数据：

- `tasks`
- `target_positions`
- `planned_orders`
- `submitted_orders`
- `order_events`
- `trades`
- `task_results`

任务结果请求体中会保留账户快照、已提交订单、成交、事件、错误
和生命周期元数据。常用查询见
`docs/09-operations.md`。

## 12. 用户操作流程

首次部署顺序：

1. 安装并激活 Python 虚拟环境。
2. 复制并填写 `config.yaml`。
3. 登录 Xquant。
4. 注册交易网关账户。
5. 运行 `doctor`。
6. 运行 `check-qmt`。
7. 先 `dry-run`。
8. 人工确认任务和风控。
9. 只在有人值守时打开真实下单安全门。
10. 使用 `daemon` 长期运行。

CLI 操作说明见 `docs/09-operations.md`。

## 13. 运维和故障处理

重点故障类型：

- QMT `connect_result=-1`。
- `WaitingFreeWriter instances exceed maximum limit`。
- `userdata_mini` 队列文件占满磁盘。
- heartbeat 黄灯或红灯。
- Xquant 上报失败。
- 终态结果补报失败。
- 本地任务终态和 Xquant 状态不一致。

排查入口：

- `trade-xquant doctor`
- `trade-xquant check-qmt`
- `trade-xquant heartbeat`
- `trade-xquant show-status`
- SQLite 审计数据库
- `logs/`

操作命令见 `docs/09-operations.md`，QMT 运行细节见
`docs/05-qmt-runtime-notes.md`。

## 14. 测试和验收

验收需要覆盖：

- 配置加载。
- Xquant API 合约。
- QMT adapter 返回码和事件标准化。
- mock broker 下单、拒单、部分成交。
- daemon 轮询和心跳。
- submitted / partial sync。
- 超时撤单和重试。
- 重试预算。
- 撤单失败。
- 终态结果补报。
- 条件单任务结果接口。

当前全量测试命令：

```bash
PYTHONPATH=. pytest -q
```

## 15. 附录文档索引

`docs/detailed-design.md` 是主文档，不参与编号。其余专题文档使用
`NN-topic.md` 命名，编号顺序对应本主文档中的首次引用顺序。

后续新增正式生产设计专题文档必须继续纳入该体系，例如：

- `docs/10-storage-audit-design.md`
- `docs/11-testing-and-verification.md`

`docs/qmt/` 和 `docs/superpowers/` 是外部资料和 agent 工作记录目录，
不参与编号重命名。

- `docs/01-system-architecture.md`
- `docs/02-qmt-miniqmt-setup-and-validation.md`
- `docs/03-configuration.md`
- `docs/04-xquant-api-contract.md`
- `docs/05-qmt-runtime-notes.md`
- `docs/06-order-generation-algorithm.md`
- `docs/07-daemon-order-lifecycle.md`
- `docs/08-conditional-stop-take-profit-rules.md`
- `docs/09-operations.md`
