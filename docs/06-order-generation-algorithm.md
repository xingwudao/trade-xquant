# 订单生成算法

## 目标

订单生成算法把 Xquant 下发的目标权重任务转换成 QMT 可提交的订单计划。

输入：

- 目标权重 `targets`
- QMT 当前账户快照 `AccountSnapshot`
- QMT 当前持仓 `Position`
- 当前市价 `prices`
- 任务级约束 `constraints`
- 现金 buffer `cash_buffer_ratio`

输出：

- `OrderPlan`
- 多条 `PlannedOrder`
- 计划换手金额 `turnover_amount`
- 计划换手率 `turnover_ratio`

当前实现位于 `trade_xquant/portfolio_engine.py` 的 `PortfolioEngine.build_plan()`。

## 输入数据

### 目标权重

每个目标由 `symbol` 和 `target_weight` 组成。

示例：

```json
[
  {"symbol": "513100.SH", "target_weight": 0.5},
  {"symbol": "510300.SH", "target_weight": 0.5}
]
```

目标权重含义是：

```text
目标市值 = account.total_asset * target_weight
```

`target_weight` 未出现的当前持仓，目标权重按 `0` 处理。

### 当前持仓

持仓使用 QMT 当前查询结果：

- `quantity`: 当前持仓数量。
- `sellable_quantity`: 当前可卖数量。
- `market_value`: QMT 返回的持仓市值。

订单算法实际计算当前市值时使用：

```text
当前市值 = quantity * 当前市价
```

因此订单计划对输入价格敏感。

### 当前市价

`prices` 必须覆盖目标标的和当前持仓标的。

如果某个参与计算的标的没有价格，或价格小于等于 `0`，
算法会直接失败，不生成计划。

### 账户快照

账户快照至少使用：

- `account_id`
- `total_asset`
- `cash`

`total_asset` 必须大于 `0`。

## 前置校验

生成订单前先做基础校验：

- 目标权重总和不能超过 `1`。
- `account.total_asset` 必须大于 `0`。
- 所有目标标的和当前持仓标的都必须有正价格。
- 如果任务指定 `max_turnover_ratio`，当前实现要求它不小于 `0.2`。

`max_turnover_ratio < 0.2` 会直接报错：

```text
turnover ratio required by task exceeds max_turnover_ratio ...
```

这是当前实现的保守限制，用于避免目标任务在过低换手预算下生成
不完整但看似可执行的订单计划。

## 符号集合

算法使用以下标的集合：

```text
symbols = 目标权重标的 ∪ 当前持仓标的
```

这保证两类标的都会被处理：

- 目标中存在但当前未持有的标的，可能需要买入。
- 当前持有但目标中不存在的标的，目标权重视为 `0`，可能需要卖出。

## 卖出算法

卖出先于买入执行。

处理顺序为按 `symbol` 排序后的标的顺序。

对每个标的计算：

```text
当前市值 = 当前持仓数量 * 当前市价
目标市值 = account.total_asset * target_weight
差额 = 目标市值 - 当前市值
```

当 `差额 < 0` 时，说明当前持仓高于目标，需要卖出。

卖出数量计算：

```text
理论卖出股数 = abs(差额) / 当前市价
整手卖出股数 = 向下取整到 100 股整数倍
实际卖出股数 = min(sellable_quantity, 整手卖出股数)
```

如果设置了换手预算，还会进一步限制：

```text
剩余换手预算 = account.total_asset * max_turnover_ratio
实际卖出股数 = min(实际卖出股数, 向下取整到预算可卖股数)
```

卖出订单金额：

```text
amount = quantity * price
```

卖出所得会加入后续买入可用资金。

卖出订单不会超过 `sellable_quantity`。

## 买入可用资金

卖出阶段完成后，买入可用资金为：

```text
available_cash = account.cash + sell_cash - account.total_asset * cash_buffer_ratio
```

如果结果小于 `0`，按 `0` 处理。

如果设置了换手预算，买入可用资金还会受剩余换手预算限制：

```text
available_cash = min(available_cash, remaining_turnover_budget)
```

## 买入算法

买入只处理目标权重中存在的标的。

处理顺序为按 `symbol` 排序后的目标标的顺序。

对每个目标标的计算：

```text
当前市值 = 当前持仓数量 * 当前市价
目标市值 = account.total_asset * target_weight
差额 = 目标市值 - 当前市值
```

当 `差额 > 0` 时，说明当前持仓低于目标，可能需要买入。

买入金额上限依次受这些条件限制：

```text
capped_value = min(差额, available_cash)
capped_value = min(capped_value, max_single_order_amount)
capped_value = min(capped_value, remaining_turnover_budget)
```

其中 `max_single_order_amount` 和 `remaining_turnover_budget` 只有在任务约束
中存在时才生效。

买入数量计算：

```text
理论买入股数 = capped_value / 当前市价
实际买入股数 = 向下取整到 100 股整数倍
```

如果实际买入股数小于等于 `0`，跳过该订单。

如果订单金额小于 `min_order_amount`，也跳过该订单。

买入订单生成后，会扣减：

- `available_cash`
- `remaining_turnover_budget`

## 100 股整数手

所有买入和卖出数量都通过同一个规则处理：

```text
floor_lot(shares) = int(shares // 100) * 100
```

因此计划中的订单数量一定是 `100` 的整数倍。

如果理论数量不足 `100` 股，订单数量为 `0`，不会生成订单。

## 最小订单金额

`min_order_amount` 只用于过滤买入订单。

如果买入订单金额：

```text
quantity * price < min_order_amount
```

该买入订单会被跳过。

当前卖出订单不使用 `min_order_amount` 过滤。

## 换手限制

如果任务约束指定 `max_turnover_ratio`，算法使用：

```text
turnover_budget = account.total_asset * max_turnover_ratio
```

卖出和买入都会消耗同一换手预算。

计划生成完成后，再计算：

```text
turnover_amount = sum(order.amount for order in orders)
turnover_ratio = turnover_amount / account.total_asset
```

如果最终 `turnover_ratio` 超过 `max_turnover_ratio`，算法报错。

## 订单字段

每条订单包含：

- `task_id`
- `symbol`
- `side`
- `quantity`
- `price`
- `amount`
- `remark`

`remark` 当前使用 `task.task_id`。这使后续 QMT 委托、成交和任务能够通过
remark 关联。

## 空计划

以下情况可能生成空计划：

- 当前持仓已经接近目标。
- 理论交易数量不足 `100` 股。
- 买入金额低于 `min_order_amount`。
- 现金不足。
- 现金 buffer 后没有可用资金。
- 换手预算不足。
- 目标权重很小，无法生成整手订单。

空计划不是错误。执行层会把空计划作为 no-op 结果处理。

## 当前算法限制

当前实现是确定性、保守、顺序式算法，不是全局优化器。

限制包括：

- 买入按 `symbol` 排序顺序依次消耗现金。
- 不做跨标的全局最优分配。
- 不考虑手续费、印花税、滑点和盘口深度。
- 不考虑未成交在途订单，调用方需要先同步 QMT 当前状态。
- `current_value` 使用当前市价计算，不直接使用 QMT 持仓 `market_value`。
- `max_single_order_amount` 是单个买入订单上限，不会自动拆单。

这些限制使算法行为可预测，适合当前 daemon 自动化和审计优先的设计。

## 示例

账户：

```text
total_asset = 100000
cash = 100000
cash_buffer_ratio = 0.002
```

目标：

```text
513100.SH: 50%
510300.SH: 50%
```

价格：

```text
513100.SH: 2.31
510300.SH: 4.02
```

没有当前持仓时：

```text
可用现金 = 100000 - 100000 * 0.002 = 99800
```

算法会生成两个买入订单：

- 两个订单数量都向下取整到 `100` 股整数倍。
- 总买入金额不超过 `99800`。
- 总换手率不超过任务约束。

具体数量由 `价格`、`max_single_order_amount`、`max_turnover_ratio` 和
剩余现金共同决定。
