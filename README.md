# trade-xquant

`trade-xquant` 是 Xquant 的 Windows QMT / MiniQMT 交易网关。

它运行在安装并登录了国金 QMT / MiniQMT 的 Windows 机器上，负责：

- 登录 Xquant。
- 从 Xquant 拉取当前账号的程序交易任务。
- 根据目标权重、本地账户资产、持仓和价格生成订单计划。
- 执行本地风控检查。
- 在满足真实下单安全门时，通过 `xtquant` 向 QMT 下单。
- 将订单计划、委托、回调事件、成交和最终结果回传 Xquant。
- 在本地 SQLite 中保留审计记录，方便排查和交叉验证。

默认配置是安全的：

- `runtime.allow_real_order` 默认为 `false`。
- `runtime.dry_run_default` 默认为 `true`。
- 真实下单还必须设置环境变量 `TRADE_XQUANT_ENABLE_REAL_ORDER=1`。
- `config.yaml`、token、SQLite 数据库和日志都不会提交到 Git。

## 适用对象

你适合使用这个交易端，如果你已经具备：

- 一个 Xquant 账号。
- 已订阅对应组合。
- 已在 Xquant 绑定证券账户。
- 一台 Windows 机器。
- Windows 上已安装并登录国金 QMT / MiniQMT。
- QMT 登录的证券账户与 Xquant 绑定账户一致。
- QMT 已勾选 `独立交易`。

如果你只想在 Mac / Linux 上测试 Xquant API 联通，不连接 QMT，
请使用 `mock-run`。它会使用本地模拟交易客户端，覆盖任务拉取、
计划回传、模拟委托、事件回传和成交回传。

## 安装

推荐 Python 3.11，最低需要 Python 3.10。

Windows QMT 机器上安装：

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pip install xtquant
```

如果你的环境无法从 PyPI 安装 `xtquant`，请使用 QMT 自带的 Python SDK。
安装后运行 `doctor`，确认当前 Python 环境可以导入 `xtquant`。

QMT 安装和 hello 验证记录见：

```text
docs/qmt-miniqmt-setup-and-hello-validation.md
```

Mac / Linux 仅做 Xquant API 测试时安装：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Mac / Linux 不连接真实 QMT，不做真实下单。

## 第一次配置

复制配置模板：

```bash
copy config.example.yaml config.yaml
```

PowerShell 中编辑配置：

```bash
notepad config.yaml
```

至少确认这些字段：

```yaml
xquant:
  base_url: "https://xquant.shop/api/v1"
  api_token: null

qmt:
  userdata_mini_path: "C:\\Apps\\QMT\\国金证券QMT交易端\\userdata_mini"
  account_id: "replace-with-qmt-account-id"

runtime:
  allow_real_order: false
  dry_run_default: true
  broker_adapter: "qmt"
  db_path: "data/trade_xquant.db"
  log_path: "logs/trade_xquant.jsonl"
```

关键配置说明：

- `xquant.base_url`
  Xquant API 地址。生产默认值是 `https://xquant.shop/api/v1`。

- `xquant.api_token`
  首次使用保持 `null`。通过 `login` 登录后，token 会写入
  `config.yaml` 同目录下的 `xquant-token.json`。

- `qmt.userdata_mini_path`
  Windows 上 QMT 的 `userdata_mini` 目录。

- `qmt.account_id`
  证券资金账号。必须与 Xquant 绑定账户、QMT 登录账户一致。

- `runtime.allow_real_order`
  是否允许真实下单。首次使用必须保持 `false`。

- `runtime.broker_adapter`
  Windows + QMT 使用 `qmt`。本地 API 联调使用 `mock`。

不要提交这些本地文件：

- `config.yaml`
- `xquant-token.json`
- `.env`
- `data/`
- `logs/`

这些文件已经在 `.gitignore` 中。

## 登录 Xquant

使用 Xquant 账号绑定的手机号或邮箱登录。

手机号登录：

```bash
trade-xquant login --config config.yaml --phone replace-with-phone --send-otp
```

邮箱登录：

```bash
trade-xquant login --config config.yaml --email user@example.com --send-otp
```

命令会发送验证码，然后提示你输入验证码。登录成功后，token 会写入：

```text
<config directory>/xquant-token.json
```

通常不需要把 token 写进 `config.yaml`。

token 读取优先级：

1. `XQUANT_API_TOKEN`
2. `<config directory>/xquant-token.json`
3. `config.yaml` 中的 `xquant.api_token`

## 注册交易网关账户

登录后，把本地交易网关账户注册到 Xquant：

```bash
trade-xquant register-account --config config.yaml
```

这个命令是幂等的，可以重复运行。修改账户信息或风控参数后，也可以再次运行。

发送一次心跳：

```bash
trade-xquant heartbeat --config config.yaml
```

心跳会让 Xquant 看到：

- 网关账号。
- 客户端版本。
- 主机名。
- QMT 是否连接。
- `xtquant` 是否可导入。

## 基础诊断

先运行：

```bash
trade-xquant doctor --config config.yaml
```

`doctor` 会检查：

- Python 版本。
- Python 可执行文件路径。
- 当前工作目录。
- 配置文件是否存在。
- 是否能导入 `xtquant`。

Mac / Linux 上 `xtquant_importable=false` 是正常的，除非你安装了兼容 SDK。
Windows QMT 机器上应当为 `true`。

## 不连接 QMT，先测试 Xquant API

如果你在 Mac、本地开发环境或没有 QMT 的机器上测试，先使用 mock 模式。

在 `config.yaml` 中设置：

```yaml
runtime:
  broker_adapter: "mock"
  simulate_real_orders: true
  mock_submit_dry_run_orders: true
  mock_order_behavior: "filled"
  mock_total_asset: 100000
  mock_cash: 100000
  mock_prices:
    300308.SZ: 20
    300394.SZ: 18
    300502.SZ: 22
    688256.SH: 60
    688981.SH: 50
```

先在 Xquant 生成一条 pending 任务。当前手动触发流程是两步：

```text
POST /api/v1/trading-gateway/products/{product_code}/manual-tasks/preview
POST /api/v1/trading-gateway/products/{product_code}/manual-tasks
```

得到 `task_id` 后运行：

```bash
trade-xquant mock-run --config config.yaml --task-id replace-with-task-id
```

`mock-run` 不连接 QMT，但会完整测试 Xquant 上下行链路：

1. 从 Xquant 拉取 pending task。
2. 生成本地订单计划。
3. 向 Xquant 回传订单计划。
4. 使用本地 mock broker 模拟委托。
5. 模拟 QMT 订单回调和成交回调。
6. 向 Xquant 回传执行结果。
7. 在本地 SQLite 记录审计数据。

成功时输出类似：

```json
[
  {
    "task_id": "replace-with-task-id",
    "status": "dry_run_success"
  }
]
```

建议先通过这一步，再去 Windows 端联调真实 QMT。

## 准备 Windows QMT

运行 `check-qmt` 前，先人工确认：

- 国金 QMT / MiniQMT 已安装。
- QMT 已登录。
- QMT 登录账户与 `qmt.account_id` 一致。
- QMT 已勾选 `独立交易`。
- `qmt.userdata_mini_path` 指向真实 `userdata_mini` 目录。
- 当前 Windows Python 环境能导入 `xtquant`。
- 没有其他网关或测试脚本占用相同 `session_id`。

如果没有设置 `qmt.session_id`，网关会根据 `session_id_strategy`
自动生成，避免固定 session 冲突。

## 检查 QMT 连接

在 Windows QMT 机器上运行：

```bash
trade-xquant check-qmt --config config.yaml
```

这个命令会：

1. 使用 `userdata_mini` 创建 `XtQuantTrader`。
2. 注册回调。
3. 调用 `connect()`。
4. 订阅配置中的证券账户。
5. 查询资金和持仓。

成功条件：

- `connect()` 返回 `0`。
- `subscribe()` 返回 `0`。

如果出现 `connect_result=-1` 或订阅失败：

- 确认 QMT 已打开并登录。
- 确认 QMT 勾选了 `独立交易`。
- 确认 `userdata_mini_path` 正确。
- 确认 `account_id` 与 QMT 登录账户完全一致。
- 换一个 `session_id`，或保持 `session_id: null` 让系统自动生成。
- 关闭可能占用同一 session 的其他脚本或网关进程。

## Windows 上先 dry-run

QMT 连接检查通过后，先做 dry-run：

```bash
trade-xquant dry-run --config config.yaml --task-id replace-with-task-id
```

dry-run 会：

- 拉取 Xquant 任务。
- 查询本地资金、持仓和价格。
- 生成与真实下单一致的订单计划。
- 回传订单计划和 dry-run 结果。
- 不调用 QMT `order_stock`。

用 dry-run 检查：

- symbol 是否正确。
- 价格是否能获取。
- 目标权重是否合理。
- 现金 buffer 是否保留。
- 数量是否满足 100 股整数手。
- 单笔金额和换手率风控是否符合预期。

## 单次处理任务

处理当前 Xquant 返回的 pending tasks：

```bash
trade-xquant poll-once --config config.yaml
```

在默认安全配置下，即使任务是 real mode，只要真实下单安全门没打开，
真实委托也会在到达 QMT 前被拒绝。

## 常驻轮询

持续轮询：

```bash
trade-xquant daemon --config config.yaml
```

轮询间隔来自：

```yaml
runtime:
  poll_interval_seconds: 30
```

建议使用受控终端、Windows 服务包装器或进程管理器运行，并保留 `logs/`。

## 查看本地状态

```bash
trade-xquant show-status --config config.yaml
```

这个命令读取本地 SQLite，显示任务状态统计和最近任务。

默认本地审计库：

```text
data/trade_xquant.db
```

可以用 SQLite 查看：

```bash
sqlite3 data/trade_xquant.db
```

常用查询：

```sql
SELECT task_id, status, received_at, updated_at FROM tasks ORDER BY updated_at DESC;
SELECT task_id, symbol, side, quantity, price, amount FROM planned_orders;
SELECT task_id, symbol, side, quantity, price, amount, status FROM submitted_orders;
SELECT event_type, order_id, symbol, payload_json, created_at FROM order_events;
SELECT task_id, status, payload_json, created_at FROM task_results;
```

## 开启真实下单

真实下单必须同时打开两个安全门。

第一，在 `config.yaml` 中设置：

```yaml
runtime:
  allow_real_order: true
```

第二，在运行终端设置环境变量：

```bash
set TRADE_XQUANT_ENABLE_REAL_ORDER=1
```

然后再运行：

```bash
trade-xquant poll-once --config config.yaml
```

即使两个安全门都打开，网关仍会阻止以下情况：

- 任务不是 `real` mode。
- 当前不在 A 股交易时段。
- 任务已过期。
- 本地已记录该任务为终态。
- 账户 ID 不匹配。
- 目标权重非法或总和超过 `1`。
- symbol 未知或无法取价。
- 单笔订单金额超过阈值。
- 总换手超过阈值。
- 无法保留现金 buffer。

真实下单必须在有人值守的交易时间执行。建议顺序是：

1. `doctor`
2. `check-qmt`
3. `dry-run`
4. 人工确认计划
5. 打开真实下单安全门
6. `poll-once`

## CLI 快查

```bash
trade-xquant doctor --config config.yaml
trade-xquant login --config config.yaml --phone replace-with-phone --send-otp
trade-xquant register-account --config config.yaml
trade-xquant heartbeat --config config.yaml
trade-xquant check-qmt --config config.yaml
trade-xquant mock-run --config config.yaml --task-id replace-with-task-id
trade-xquant dry-run --config config.yaml --task-id replace-with-task-id
trade-xquant poll-once --config config.yaml
trade-xquant daemon --config config.yaml
trade-xquant show-status --config config.yaml
```

## 开发测试

```bash
python -m pytest tests
python -m compileall trade_xquant hello.py
```

当前测试覆盖：

- Xquant 登录和 token 存储。
- Xquant 任务解析和手动任务 API。
- 目标权重转订单计划。
- 100 股整数手。
- 现金 buffer。
- 最小订单金额过滤。
- 最大换手限制。
- task_id 幂等。
- 真实下单安全门。
- mock QMT 委托、回调、拒单和部分成交。
- QMT adapter 事件标准化。

## 更多文档

- `docs/architecture.md`
- `docs/configuration.md`
- `docs/operations.md`
- `docs/xquant-api-contract.md`
- `docs/qmt-runtime-notes.md`
- `docs/qmt-miniqmt-setup-and-hello-validation.md`
