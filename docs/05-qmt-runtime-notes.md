# QMT 运行说明

## 必要运行状态

- QMT 已安装在 Windows 交易机器上。
- QMT 已用目标证券账户登录。
- 登录时已勾选 `独立交易`。
- 已配置 `userdata_mini` 路径。
- 账户已开通策略交易权限。

本地验证过的路径为：

```text
C:\Apps\QMT\国金证券QMT交易端\userdata_mini
```

## MiniQMT 连接

`QmtAdapter.connect()` 使用：

```python
trader = XtQuantTrader(userdata_mini_path, session_id)
trader.start()
connect_result = trader.connect()
subscribe_result = trader.subscribe(account)
```

`connect_result` 和 `subscribe_result` 都必须为 `0`。

session ID 默认按当前时间自动生成，以避免固定 session ID 冲突。
只有在操作者确认没有其他进程使用同一 session 时，才应配置固定值。

## 订单 API

MVP 使用 `xtquant.xttrader.order_stock`，不使用模型交易的 `passorder`。

QMT PDF 记录：

- `passorder` 操作 `23` 是股票买入。
- `passorder` 操作 `24` 是股票卖出。
- 但 `XtQuantTrader.order_stock` 已提供更直接的 Python API。

`order_stock` 返回 `-1` 表示下单失败。网关必须把它当作错误。

`cancel_order_stock` 返回 `0` 表示撤单成功。任何非 `0` 返回值都必须当作
撤单失败，不能触发重试。

## Callback 处理

`QmtAdapter` 注册 `XtQuantTraderCallback`，并将这些对象标准化为事件：

- `on_stock_order`
- `on_stock_trade`
- `on_order_error`

标准化事件写入本地审计存储，也会包含在任务结果请求体中。

即使回调缺失，daemon 仍会通过 QMT 当前委托和成交查询补充同步状态。
因此回调是审计增强，不是唯一状态来源。

## `connect_result=-1` 排查

常见原因：

- MiniQMT 未打开或未登录。
- 未勾选 `独立交易`。
- `userdata_mini_path` 指向错误目录。
- 当前 Python 进程没有权限访问 QMT runtime 文件。
- `session_id` 与其他进程冲突。
- QMT 内部 queue 或 writer 资源耗尽。
- 磁盘空间不足。

处理顺序：

1. 关闭其他 QMT 测试脚本或 daemon。
2. 确认 MiniQMT 已登录正确账户。
3. 确认 `userdata_mini_path`。
4. 保持 `qmt.session_id: null`，使用自动 session。
5. 清理磁盘空间，尤其是 `userdata_mini` 下的 queue 文件。
6. 重启 MiniQMT。
7. 再运行 `trade-xquant check-qmt --config config.yaml`。
