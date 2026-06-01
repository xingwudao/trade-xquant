# QMT / MiniQMT 申请、安装与 Python 下单验证记录

记录日期：2026-05-26

本文件用于作为后续正式自动化交易程序的知识库。
内容来自三部分：

- 公开互联网资料
- 本项目本地 QMT 文档和日志
- 实际在 Windows 云服务器上跑通的验证过程

注意：

- 券商 QMT 门槛会变化，最终以券商最新审核口径为准。
- 本文件不是投资建议，也不是券商官方说明。
- 涉及真实交易前，必须再次人工确认权限、账户和风控参数。

## 1. 为什么选择国金 QMT

公开资料显示，不同券商的 QMT / PTrade 门槛差异较大。
常见说法包括：

- 部分券商要求 `30万` 到 `50万` 资产。
- 部分头部券商、专业版或全功能权限可能要求更高资产。
- 有资料提到部分券商可能要求 `100万` 或 `300万`。
- 国金证券相关公开资料多次提到 `10万` 级别可申请。

本次实际选择国金证券的原因：

- 申请门槛相对低。
- 可以线上通过国金佣金宝申请。
- 能同时看到 `QMT` 和 `PTrade` 选项。
- 能申请 `基础交易权限` 和 `策略交易权限`。
- 实测 `10万` 入金后可以完成申请路径。

需要特别强调：

`策略交易权限` 是程序化下单的必要权限。
只开通 QMT 客户端但没有策略交易权限，
可能只能登录、看行情、查资金或手工交易，
无法通过 Python 程序化下单。

公开资料参考：

- `https://www.miniqmt.com/pages/qa/knowledge-qa.html`
- `https://www.quant666.com/294.html`
- `https://k.sina.com.cn/article_7857201856_1d45362c001902979g.html`
- `https://k.sina.com.cn/article_7857201856_1d45362c0019028828.html`
- `https://k.sina.com.cn/article_7857201856_1d45362c00190288bo.html`
- `https://zhuanlan.zhihu.com/p/1925906187414667342`

这些资料多为第三方或开户渠道文章。
它们适合了解市场大致门槛，
但不应替代券商客户经理或 App 内审核结果。

## 2. 国金 QMT 申请过程

实际走通的申请过程如下。

1. 下载并安装 `国金佣金宝`。

2. 注册并开立国金证券账户。

3. 入金 `10万`。

4. 完成风险评估。

   风险评估需要达到适合量化/策略交易的风险等级。
   实操上，应把自己评测为：

   - 风险偏好型
   - 资深投资人

   如果风险测评等级过低，可能无法开通策略交易权限。

5. 在 App 首页进入：

   `首页 -> 全部功能 -> 更多业务办理`

6. 搜索：

   `QMT`

7. 进入 QMT 业务办理入口。

8. 申请时选择：

   `QMT`

   另一个可选项是：

   `PTrade`

9. 权限选择：

   - `基础交易权限`
   - `策略交易权限`

   必须确认勾选 `策略交易权限`。

10. 等待全流程审核通过。

11. 审核通过后，申请时留下的邮箱会收到专属下载链接。

12. 下载得到：

   `gjzqqmt.rar`

## 3. 安装包内容

本项目中已解压：

`gjzqqmt.rar`

解压后目录：

`gjzqqmt/`

主要内容：

- `XtItClient_x64_国金证券QMT实盘_2.0.8.300.exe`
- `QMT操作说明文档/`

文档包括：

- `国金QMT极速策略交易系统-操作说明.pdf`
- `国金QMT极速策略交易系统_模型资料_Python_API_说明文档_Python3.pdf`
- `国金QMT极速策略交易系统-普通算法交易参数说明.pdf`
- `国金QMT极速策略交易系统-智能算法交易介绍202008.pdf`
- `国金QMT极速策略交易系统-网格策略使用手册202002.pdf`
- `国金QMT极速策略交易系统-VBA模型编辑使用手册202002.pdf`

实际建议：

- PDF 文档可以交给 AI 阅读和检索。
- 人不用一开始全部仔细读完。
- 先跑通安装、登录和最小下单链路更重要。

## 4. Windows 安装

将安装程序复制到计划用于下单的 Windows 机器。

可选环境：

- 个人 Windows 电脑
- Windows 云服务器

本次实际跑通环境：

- Windows 云服务器
- QMT 安装路径：

  `C:\Apps\QMT\国金证券QMT交易端`

安装步骤：

1. 右键安装程序。

2. 选择：

   `以管理员身份运行`

3. 安装到：

   `C:\Apps\QMT\国金证券QMT交易端`

4. 安装完成后，启动 QMT。

5. 登录时选择账户：

   `replace-with-qmt-account-id`

6. 关键选项：

   必须勾选 `独立交易`。

本次一开始没有勾选 `独立交易`，
导致 Python `xtquant` 连接失败：

`connect_result = -1`

勾选后连接成功：

`connect_result = 0`

结论：

在国金 QMT 这个版本中，
`独立交易` 实际对应 MiniQMT / 极简交易通道。
这是外部 Python 程序通过 `xtquant` 连接 QMT 的必要条件。

## 5. QMT、MiniQMT、xtquant 的关系

正式理解如下：

`Python 程序`

-> `xtquant Python SDK`

-> `本机 MiniQMT / 独立交易进程`

-> `QMT 客户端交易服务`

-> `国金证券柜台`

-> `交易所`

回报再沿原路返回。

关键点：

- Python 不直接登录券商柜台。
- Python 不保存交易密码。
- Python 依赖已经登录的 QMT / MiniQMT。
- QMT 是本机交易网关。
- `xtquant` 是 Python 到 MiniQMT 的本地接口。

迅投官方文档说明：

- `XtQuant` 基于 `MiniQMT`。
- `Xttrader` 可以和 `MiniQMT` 客户端交互。
- 交互内容包括：
  - 报单
  - 撤单
  - 查询资产
  - 查询委托
  - 查询成交
  - 查询持仓
  - 接收资金、委托、成交、持仓等主推消息

官方参考：

- `https://dict.thinktrader.net/nativeApi/start_now.html`
- `https://dict.thinktrader.net/nativeApi/xttrader.html`
- `https://dict.thinktrader.net/nativeApi/question_function.html?id=TB5IbM`

## 6. userdata_mini 目录

Python 创建交易连接时使用：

`C:\Apps\QMT\国金证券QMT交易端\userdata_mini`

代码中对应：

```python
trader = XtQuantTrader(
    r"C:\Apps\QMT\国金证券QMT交易端\userdata_mini",
    session_id,
)
```

`userdata_mini` 不是普通配置目录。
它是 MiniQMT 与 `xtquant` 对接的本地工作目录。

目录中观察到的关键文件：

- `up_queue_xtquant`
- `up_queue_win_xtquant`
- `down_queue_win_20260526`
- `down_queue_win_20260526__mutex`
- `lock_down_queue_win_20260526`
- `lock_up_queue_xtquant`
- `lock_up_queue_win_xtquant`
- `miniqmtShmQuoteCache`
- `miniqmtShmStockListCacheSH`
- `miniqmtShmStockListCacheSZ`
- `miniqmtShmTradeDateListCache`

推断：

- `up_queue_*` 用于上行请求。
- `down_queue_*` 用于下行回报。
- `lock_*` 和 `*_mutex` 用于锁和互斥。
- `miniqmtShm*` 是共享内存或文件映射缓存。

日志证据显示：

`lock_down_queue_win_20260526`

和 `session_id=20260526` 直接相关。

重要原则：

不要直接读写这些文件。
这些是迅投私有 IPC 协议的一部分。
正式程序只能通过 `xtquant` SDK 操作。

## 7. hello.py 的验证目标

本项目中当前验证程序：

`hello.py`

它不是最终自动交易程序。
它的目标是验证 QMT / MiniQMT / xtquant 通路。

目前支持模式：

- `doctor`
- `scan`
- `check`
- `dry-run`
- `order`

### doctor

用途：

- 查看当前 Python 路径。
- 查看 Python 版本。
- 检查 `xtquant` 是否能导入。
- 输出 `sys.path`。

运行：

```powershell
python hello.py doctor
```

实际成功输出：

```text
sys.executable : C:\Python311\python.exe
sys.version    : 3.11.8
xtquant 导入成功
xtquant path   : C:\Python311\Lib\site-packages\xtquant\__init__.py
```

说明：

`xtquant` 已经安装到当前运行脚本的 Python 环境。

### check

用途：

- 创建 `XtQuantTrader`
- 启动 trader
- 连接 MiniQMT
- 订阅资金账号
- 查询账号状态
- 查询资金
- 查询持仓
- 查询当日委托
- 查询当日成交

运行：

```powershell
python hello.py --path "C:\Apps\QMT\国金证券QMT交易端\userdata_mini" check
```

实际成功输出：

```text
连接结果: 0
订阅结果: 0
总资产    : 100,000.00
可用资金  : 100,000.00
持仓市值  : 0.00
冻结资金  : 0.00
当前持仓: 0
当日委托: 0
当日成交: 0
```

说明：

- Python 能连接 MiniQMT。
- Python 能订阅资金账号。
- Python 能查询交易数据。

### dry-run

用途：

- 只打印拟下单参数。
- 不连接 QMT。
- 不发送真实委托。

示例：

```powershell
python hello.py dry-run `
  --stock-code 513100.SH `
  --side buy `
  --volume 100 `
  --price-type fix `
  --price 2.185
```

输出说明：

```text
side buy (23)
price_type fix (11)
```

其中：

- `23` 表示股票买入。
- `24` 表示股票卖出。
- `11` 表示指定价 / 限价。

### order

用途：

- 发送真实委托。

安全设计：

必须同时满足两个条件：

1. 设置环境变量：

   ```powershell
   $env:QMT_ENABLE_REAL_ORDER="1"
   ```

2. 命令中显式传入：

   `--real-order`

真实下单示例：

```powershell
$env:QMT_ENABLE_REAL_ORDER="1"

python hello.py order --real-order `
  --stock-code 513100.SH `
  --side buy `
  --volume 100 `
  --price-type fix `
  --price 2.185 `
  --cancel-after-order
```

本次实际运行结果：

```text
order_id=<order_id>
order_error:
error_id=-59
error_msg=当前时间不允许委托
order_status=57
cancel_result=0
```

解释：

- 程序化下单请求已经发出。
- 已获得真实 `order_id`。
- 柜台返回错误：

  `当前时间不允许委托`

- 原因是运行时间在非交易时段。
- 这不是程序化权限问题。
- 撤单请求也成功发出。

结论：

程序化下单链路已跑通。
失败原因是非交易时间。

## 8. 日志观察到的内部链路

复制到本项目的日志：

- `XtMiniQmt_20260526.log`
- `XtMiniQmt_perform_20260526.log`
- `XtMiniQuote_20260526.log`

注意：

日志中包含资金账号、姓名和疑似密码字段。
不得公开发布，不得提交到公开仓库。

### 交易主日志

核心日志文件：

`XtMiniQmt_20260526.log`

其中最关键组件：

`COrderServiceQuantAdaptor`

该组件负责量化会话和交易服务之间的适配。

连接日志：

```text
TC::COrderServiceQuantAdaptor::onConnected
quant session 20260526 connected
```

这对应 Python：

```python
trader.connect()
```

订阅日志：

```text
[orderservice] [quant] account ... subscribe [16202]
[orderservice] [quant] account ... subscribe [16203]
onSubscribe account <account_id> 2 subscribed datas
```

这对应 Python：

```python
trader.subscribe(account)
```

查询日志：

```text
onQueryAccountInfosReq
onQueryAccountStatusReq
onQueryStockAsset
onQueryStockPositions
onQueryStockOrders
onQueryStockTrades
```

这对应 `hello.py check`。

下单日志：

```text
onOrder param:
[<account_id> 2] [SH 513100] [23 11] [100 2.185]
[qmt_permission_test]
seq:9, tag:20260526
```

这对应 Python：

```python
trader.order_stock(
    account,
    "513100.SH",
    23,
    100,
    11,
    2.185,
    "qmt_permission_test",
    "qmt_permission_test",
)
```

MiniQMT 转发交易服务：

```text
[orderservice] [quant] try order
[ordercenter] order success [<order_id>]
onOrderResponse seq:9, tag:20260526, orderid:<order_id>
```

柜台错误回报：

```text
ordererror [-59]
当前时间不允许委托
onOrderError seq:9, tag:20260526, orderid:<order_id>
```

委托状态推送：

```text
quant adaptor: push order to session 20260526
xt<order_id> 57
```

撤单日志：

```text
onCancel param ... <order_id>
try cancel
cancel orderid [<order_id>] success
onCancelResponse
```

断开日志：

```text
heartbeat timeout
lock_down_queue_win_20260526 file lock not held, offline
```

解释：

- `session_id` 会参与生成队列/锁文件。
- MiniQMT 通过锁文件判断 Python 会话是否在线。

### 性能日志

文件：

`XtMiniQmt_perform_20260526.log`

关键片段：

```text
[order][req|0] ... 17:50:28.822
[order][resp|0] ... 17:50:28.843
```

解释：

本地请求到本地响应约 `21 ms`。

随后主日志中柜台业务错误在：

`17:50:28.854`

到达，约再过 `11 ms`。

该数据只反映本次非交易时段测试，
不能代表盘中真实成交时延。

### 行情日志

文件：

`XtMiniQuote_20260526.log`

主要看到：

- 行情服务器配置
- 行情连接
- `SHMHashTable`
- `miniqmtShmQuoteCache`
- `miniqmtShmStockListCache*`
- `miniqmtShmTradeDateListCache`

解释：

行情和基础资料大量使用共享内存或文件映射缓存。
这和交易队列不是同一层。

## 9. 对正式自动交易程序的设计启发

正式程序不应直接扩展 `hello.py` 成大脚本。
应拆成清晰模块。

建议模块：

### QMT 网关模块

职责：

- 启动 `XtQuantTrader`
- 注册回调
- 连接 MiniQMT
- 订阅账号
- 查询资金/持仓/委托/成交
- 下单
- 撤单
- 维护连接状态

关键要求：

- `connect()` 必须返回 `0`。
- `subscribe()` 必须返回 `0`。
- `session_id` 必须唯一。
- 不要无限循环尝试 `session_id`。
- 重连要有限次数并告警。

### 信号接入模块

职责：

- 从 Linux 服务器上的 Xquant 接收目标组合。
- 校验信号合法性。
- 做去重和过期判断。

信号必须包含：

- `signal_id`
- `portfolio_id`
- `created_at`
- `expires_at`
- `target_weights`
- `cash_target` 或现金保留比例
- `source`
- `signature` 或鉴权字段

### 组合求解模块

职责：

- 输入目标权重、当前持仓、现金和价格。
- 输出订单列表。

目标：

- 目标权重误差越小越好。
- 交易成本越小越好。

约束：

- A 股/ETF 最小交易单位通常为 `100` 股。
- 买入需要满足可用资金。
- 卖出不能超过可卖数量。
- 需要考虑停牌、涨跌停、价格异常。
- 需要设置最小交易金额，避免碎单。
- 需要设置最大换手、最大单票交易金额等风控。

### 执行引擎模块

职责：

- 接收订单计划。
- 按顺序下单。
- 跟踪订单状态。
- 处理拒单、撤单、部分成交。
- 必要时二次调整。

关键原则：

- 不能只看 `order_id`。
- 必须综合：
  - `on_order_error`
  - `on_stock_order`
  - `on_stock_trade`
  - 主动查询结果

### 风控模块

职责：

- 交易前校验。
- 交易中限速。
- 交易后核对。

建议风控：

- 单次调仓最大成交金额。
- 单票最大买入金额。
- 单票最大卖出比例。
- 最大订单数。
- 最大换手率。
- 非交易时段禁止下单。
- 信号过期禁止下单。
- 目标权重和不合理时禁止下单。
- 未知证券代码禁止下单。
- 当前持仓中非目标资产是否允许卖出，需要显式配置。

### 审计日志模块

职责：

- 持久化每次信号。
- 持久化组合求解结果。
- 持久化每笔委托请求。
- 持久化柜台回报。
- 持久化成交和最终持仓。

建议保存：

- JSONL 原始事件流
- CSV 人工复核报表
- 每日运行摘要

## 10. 后续设计待确认问题

正式自动交易程序开始设计前，需要确认：

1. Xquant 信号传输方式：

   - Windows 主动 HTTP 拉取
   - Linux 主动 HTTP 推送
   - 文件 / SFTP / 对象存储同步

2. 调仓频率：

   - 每日一次
   - 每小时
   - 分钟级
   - 事件触发

3. 交易品种：

   - 只交易 ETF
   - ETF + 股票
   - 是否包含可转债、港股通、两融

4. 价格策略：

   - 最新价
   - 买一/卖一
   - 对手价
   - 限价偏移
   - 分批追单

5. 成本模型：

   - 手续费
   - 印花税
   - 过户费
   - 滑点估计
   - 最小佣金

6. 是否允许自动撤单和重挂。

7. 是否需要人工确认模式。

8. 盘中失败后的处理策略。

9. 是否需要守护进程常驻运行。

10. 是否需要 Web 管理界面或只用日志/命令行。

## 11. 当前阶段结论

已经完成：

- 国金 QMT 申请流程跑通。
- 安装包解压。
- Windows 云服务器安装 QMT。
- 勾选 `独立交易` 登录。
- 安装 `xtquant` 到 `C:\Python311`。
- Python 成功连接 MiniQMT。
- Python 成功订阅资金账号。
- Python 成功查询资金、持仓、委托、成交。
- Python 成功发送真实委托请求。
- Python 成功收到柜台错误回报。
- Python 成功发送撤单请求。
- 日志确认 MiniQMT 通过 `COrderServiceQuantAdaptor`
  处理量化会话。

尚未完成：

- 交易时段内真实成交验证。
- 正式组合目标权重求解。
- Xquant 信号接入。
- 风控系统。
- 审计和运维系统。

下一步建议：

先设计正式程序架构，
不要直接在 `hello.py` 上堆功能。

`hello.py` 保留为验收和排障工具。
