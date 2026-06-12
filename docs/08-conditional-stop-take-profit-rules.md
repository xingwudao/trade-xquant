# 条件止损止盈规则

本文档是 trade-xquant 条件止损止盈规则的开发参考。

来源文档：

- 飞书：`量化交易止盈止损完整体系手册`
- URL: `https://icnzri2zfn9e.feishu.cn/docx/SCz6dluyqoReycxTcVocdK2Ingb`
- 获取方式：`lark-cli docs +fetch --api-version v2`
- 起草时读取的来源版本：`351`

## 不可协商的参数规则

飞书文档中的所有数值都必须被视为参数。

trade-xquant 不能硬编码：

- 止损百分比。
- 止盈百分比。
- 追踪回撤百分比。
- ATR 窗口。
- ATR 倍数。
- HV 窗口。
- HV 年化因子。
- HV lambda 值。
- 标准差窗口。
- 标准差倍数。
- 最小盈亏比。
- 激活阈值。
- 执行百分比。
- 持有周期分桶。
- 账户级回撤或盈利阈值。

具体参数值必须由 Xquant 侧研究、回测、验证和组合配置决定，
再通过 Xquant 交易任务下发给网关。

网关可以校验参数是否存在、类型是否正确、范围是否安全、组合是否一致。
网关不能为交易阈值发明默认值。

## 开发范围

本文档描述飞书文档中的完整规则分类，并把它归一化为可实现契约。

当前 MVP 已实现：

- 仅支持 `instrument` scope。
- 仅支持卖出侧条件退出。
- `static_pct`。
- `trailing_pct`。
- `atr_trailing`。
- `hv_log_trailing`。
- `std_trailing`。
- 追踪止盈的激活门槛。
- `constraints.condition_orders` 下发条件值。
- 本地 SQLite 条件单状态和审计存储。
- Xquant 条件单结果审计上报。

基于 K 线的 `atr_trailing`、`hv_log_trailing` 和 `std_trailing` 已在引擎
和模拟 adapter 中实现并可测试。真实 `qmt` adapter 目前尚未提供历史 K 线。
生产 QMT 模式下，在接入 QMT 历史 K 线前，这些基于 K 线的规则会记录逐条
条件评估错误。

未来可加入：

- 组合范围规则。
- 组合级清仓或部分降风险动作。

## 核心概念

`stop_loss`
- 目标是控制亏损或下行风险。
- 固定止损通常在低于入场价时触发。
- 追踪止损可以在价格或账户财富创新高后上移。
- 它可能在亏损、回本或盈利状态下退出，取决于市场路径。

`take_profit`
- 目标是兑现或锁定收益。
- 固定止盈通常在高于入场价时触发。
- 追踪止盈通常应在达到配置的盈利门槛后才激活。
- 激活后，它在高水位回撤时退出。

重要区别：

- 追踪止损和追踪止盈可以使用相同数学线。
- 它们是不同业务规则，因为目的、激活语义、上报和校验要求不同。

## 归一化符号

单标的价格变量：

- `P_t`: 最新标的价格。
- `P_in`: Xquant 提供的入场价或基准价格。
- `P_high_t`: 条件单激活以来的高水位价格。
- `H_t`: 当前 K 线最高价，用于 ATR。
- `L_t`: 当前 K 线最低价，用于 ATR。
- `C_t`: 当前 K 线收盘价。
- `ATR_t`: 参数化窗口计算出的平均真实波幅。
- `HV_t`: 对数收益的年化历史波动率。
- `sigma_P_t`: 最近价格序列的标准差。

组合财富变量：

- `W_t`: 最新账户或组合财富。
- `W_0`: Xquant 提供的 reference wealth。
- `W_high_t`: 条件单激活以来的高水位财富。
- `ATR_W_t`: 账户或组合财富 true range。
- `HV_W_t`: 财富对数收益的年化历史波动率。
- `sigma_W_t`: 最近财富序列的标准差。

参数命名：

- 任务请求体中使用明确参数名。
- API 契约避免使用 `N` 这种含糊名称。
- 飞书文档中的 `N` 映射为方法特定参数，例如 `atr_multiple`
  或 `std_multiple`。

## 条件单结构

条件单位于：

```text
constraints.condition_orders[]
```

推荐归一化字段：

```json
{
  "id": "稳定规则模板 ID",
  "scope": "instrument | portfolio",
  "purpose": "stop_loss | take_profit",
  "method": "static_pct | trailing_pct | atr_trailing | hv_log_trailing | std_trailing",
  "params": {"hyperparameters": "该方法要求的数字或字符串"},
  "action": {"type": "sell_pct", "pct": 1.0},
  "reference": {"source": "position_cost_price"},
  "enabled": true
}
```

实现说明：

- 上面的 JSON 示例只用于说明 shape。
- 真实任务请求体必须使用类型化字段值。
- Xquant 任务请求体包含超参数、明确动作，以及基准依赖最终持仓成本时的
  基准来源元数据。
- Xquant 发送组合或账户级规则模板。如果规则有 `id` 但没有 `symbol`，
  trade-xquant 会把它展开为每个任务目标标的的本地条件单实例。
- 对依赖成交成本的规则，Xquant 发送 `reference.source: "position_cost_price"`，
  不发送 `reference_price`。
- 本地条件单实例 ID 为：
  `cond-{portfolio_id}-{symbol}-{rule_template_id}`。
- 当前 MVP 中，网关拥有运行时状态：QMT 持仓成本基准、高水位值、
  激活状态、指标快照、触发证据和执行审计请求体都存储在 SQLite
  和条件单审计报告中。
- gateway 必须拒绝缺失、格式错误或超出安全范围的必要参数。
- `action.type` 必填。
- `sell_pct` 需要 `action.pct`，且 `0 < pct <= 1`。
- `clear` 可以省略 `action.pct`，因为动作类型已表示清空可卖目标仓位。

## 条件单状态

本地条件单的主要运行状态：

`armed`
- 条件单已经布防，等待价格触发。
- 真实 QMT 条件单在非有效交易 session 内不会轮询实时价格，也不会触发下单链路。
- dry-run 和 mock simulated-real 条件单不受真实交易 session 限制。

`triggered`
- 本轮价格已经满足触发条件，网关正在准备执行。

`pending_execution`
- 条件已经触发，但真实下单风控发现当前不在有效交易 session。
- 网关不向 QMT 提交委托，不写终态 `task_results`，也不上报执行失败。
- 进入有效交易 session 后，daemon 会先把它恢复为 `armed`，
  再重新取最新价格并完整重算条件。
- 如果重验仍满足条件，才重新生成计划并下单。
- 如果重验不再满足条件，保持 `armed`，继续等待下一次触发。

`submitted`
- 已经提交到 QMT，后续由订单同步流程跟踪成交和终态。

`failed`
- 终态失败。账户不匹配、任务过期、单笔金额超限、换手超限、
  未知标的、无可卖整手等不可自动恢复错误仍应进入 `failed`。

## 动作

支持的动作概念：

`sell_pct`
- 卖出可卖仓位的参数化百分比。
- `action.pct` 由 Xquant 下发。

`clear`
- 清空目标标的或组合 scope 下的可卖仓位。
- 具体清仓行为必须遵守 QMT 整手、可卖数量、风控和市场限制。

未来动作概念：

- 组合级部分降风险。
- 组合级全部清仓。
- 组合动作使用的 symbol 白名单或黑名单。

## 单标的止损规则

### 静态百分比止损

目的：
- 针对单标的基准价格限制亏损。

必需任务数据：
- `scope: "instrument"`。
- `purpose: "stop_loss"`。
- `method: "static_pct"`。
- `reference_price`。
- `params.stop_loss_pct`。

触发线：

```text
trigger_price = reference_price * (1 - params.stop_loss_pct)
```

触发条件：

```text
P_t <= trigger_price
```

适用场景：

- 短周期策略。
- 均值回归或区间策略，且有预定义 invalidation level。
- 最适合作为第一版实现，因为简单且可审计。

风险：

- 可能被短时价格尖刺触发。
- 不适应当前波动率。
- gap 或流动性差时，执行价可能劣于触发价。

### 固定比例追踪止损

目的：
- 当价格创新高后上移止损线，限制回撤。

必需任务数据：
- `scope: "instrument"`。
- `purpose: "stop_loss"`。
- `method: "trailing_pct"`。
- `reference_price`。
- `params.trailing_drawdown_pct`。

状态：

```text
P_high_t = max(reference_price, max(P_i since condition start))
```

触发线：

```text
trigger_price = P_high_t * (1 - params.trailing_drawdown_pct)
```

触发条件：

```text
P_t <= trigger_price
```

适用场景：

- 趋势跟随。
- 动量持仓。
- 希望保留上行空间但限制回撤的仓位。

风险：

- 在震荡行情中可能频繁触发。
- 回撤百分比过小会过早退出。
- 回撤百分比过大可能放弃大量浮盈。

### ATR 追踪止损

目的：
- 使用波动率自适应的追踪止损。

必需任务数据：
- `scope: "instrument"`。
- `purpose: "stop_loss"`。
- `method: "atr_trailing"`。
- `reference_price`。
- `params.atr_window`。
- `params.atr_multiple`。
- `params.bar_interval`。

True range：

```text
TR_t = max(
  H_t - L_t,
  abs(H_t - C_{t-1}),
  abs(L_t - C_{t-1})
)
```

ATR：

```text
ATR_t = average(TR over params.atr_window)
```

触发线：

```text
trigger_price = P_high_t - params.atr_multiple * ATR_t
```

触发条件：

```text
P_t <= trigger_price
```

适用场景：

- 波动率变化明显的趋势策略。
- 固定百分比不适合不同波动水平的标的。

风险：

- 需要可靠 bar 数据。
- ATR 窗口和倍数必须回测决定。
- gap、停牌、异常 bar 会影响触发线。

### HV 对数收益追踪止损

目的：
- 使用历史波动率调整追踪距离。

必需任务数据：
- `scope: "instrument"`。
- `purpose: "stop_loss"`。
- `method: "hv_log_trailing"`。
- `reference_price`。
- `params.hv_window`。
- `params.hv_annualization_factor`。
- `params.hv_multiple`。

对数收益：

```text
r_t = ln(P_t / P_{t-1})
```

历史波动率：

```text
HV_t = std(r over params.hv_window) * sqrt(params.hv_annualization_factor)
```

触发线：

```text
trigger_price = P_high_t * (1 - params.hv_multiple * HV_t)
```

触发条件：

```text
P_t <= trigger_price
```

适用场景：

- 波动率本身是重要风险尺度的组合。
- 希望不同标的按相对波动率调整回撤线。

风险：

- 对窗口选择敏感。
- 短窗口可能噪声大。
- 长窗口可能反应慢。

### 标准差追踪止损

目的：
- 用最近价格标准差设定追踪距离。

必需任务数据：
- `scope: "instrument"`。
- `purpose: "stop_loss"`。
- `method: "std_trailing"`。
- `reference_price`。
- `params.std_window`。
- `params.std_multiple`。

价格标准差：

```text
sigma_P_t = std(P over params.std_window)
```

触发线：

```text
trigger_price = P_high_t - params.std_multiple * sigma_P_t
```

触发条件：

```text
P_t <= trigger_price
```

适用场景：

- 简化版波动率追踪。
- 不需要完整 ATR 计算的场景。

风险：

- 对价格尺度敏感。
- 与百分比波动率相比，可比性较差。

## 组合止损规则

### 组合静态止损

目的：
- 限制账户或组合财富相对 reference wealth 的回撤。

必需任务数据：
- `scope: "portfolio"`。
- `purpose: "stop_loss"`。
- `method: "static_pct"`。
- `reference_wealth`。
- `params.stop_loss_pct`。
- `action`。

触发线：

```text
trigger_wealth = reference_wealth * (1 - params.stop_loss_pct)
```

触发条件：

```text
W_t <= trigger_wealth
```

风险：

- 组合级动作会影响多个标的，执行风险高于单标的动作。
- 需要明确定义可卖资产范围和执行顺序。

### 组合追踪止损

目的：
- 保护组合财富高点后的回撤。

状态：

```text
W_high_t = max(reference_wealth, max(W_i since condition start))
```

触发线：

```text
trigger_wealth = W_high_t * (1 - params.trailing_drawdown_pct)
```

触发条件：

```text
W_t <= trigger_wealth
```

风险：

- 需要可靠账户净值或组合财富快照。
- 触发后可能需要组合级批量执行。

### 组合 ATR、HV 和标准差止损

这些规则把单标的价格序列替换为组合财富序列。

示例：

```text
trigger_wealth = W_high_t - params.atr_multiple * ATR_W_t
trigger_wealth = W_high_t * (1 - params.hv_multiple * HV_W_t)
trigger_wealth = W_high_t - params.std_multiple * sigma_W_t
```

当前 MVP 不实现 portfolio scope。后续实现前需要先明确：

- 组合财富数据来源。
- 可卖范围。
- 多标的执行顺序。
- 部分降风险与全部清仓的动作契约。

## 单标的止盈规则

### 静态百分比止盈

目的：
- 当价格达到基准价格上方的目标收益时退出或部分退出。

必需任务数据：
- `scope: "instrument"`。
- `purpose: "take_profit"`。
- `method: "static_pct"`。
- `reference_price`。
- `params.take_profit_pct`。
- `action`。

触发线：

```text
trigger_price = reference_price * (1 + params.take_profit_pct)
```

触发条件：

```text
P_t >= trigger_price
```

风险：

- 可能过早止盈，错过趋势延续。
- 需要通过 Xquant 侧研究决定是否分批止盈。

### 固定比例追踪止盈

目的：
- 价格达到激活盈利门槛后，使用高水位回撤锁定收益。

必需任务数据：
- `scope: "instrument"`。
- `purpose: "take_profit"`。
- `method: "trailing_pct"`。
- `reference_price`。
- `params.activation_profit_pct`。
- `params.trailing_drawdown_pct`。
- `action`。

激活条件：

```text
P_t >= reference_price * (1 + params.activation_profit_pct)
```

激活后状态：

```text
P_high_t = max(P_i since activation)
```

触发线：

```text
trigger_price = P_high_t * (1 - params.trailing_drawdown_pct)
```

触发条件：

```text
activated == true and P_t <= trigger_price
```

风险：

- 激活门槛过低时，规则会接近追踪止损。
- 激活门槛过高时，可能永远不触发。

### ATR、HV 和标准差追踪止盈

这些方法与对应追踪止损使用相同指标，但必须带有止盈
激活门槛。

示例：

```text
activated = P_t >= reference_price * (1 + params.activation_profit_pct)
trigger_price = P_high_t - params.atr_multiple * ATR_t
trigger_price = P_high_t * (1 - params.hv_multiple * HV_t)
trigger_price = P_high_t - params.std_multiple * sigma_P_t
```

触发条件：

```text
activated == true and P_t <= trigger_price
```

## 组合止盈规则

### 组合静态止盈

目的：
- 当组合财富达到基准财富上方的目标收益时执行动作。

触发线：

```text
trigger_wealth = reference_wealth * (1 + params.take_profit_pct)
```

触发条件：

```text
W_t >= trigger_wealth
```

### 组合追踪止盈

目的：
- 组合财富达到激活收益后，通过高水位回撤锁定收益。

激活条件：

```text
W_t >= reference_wealth * (1 + params.activation_profit_pct)
```

触发线：

```text
trigger_wealth = W_high_t * (1 - params.trailing_drawdown_pct)
```

触发条件：

```text
activated == true and W_t <= trigger_wealth
```

当前 MVP 不实现 portfolio scope。

## 盈亏比参数化

如果 Xquant 需要把止损和止盈成对下发，应由 Xquant 侧保证盈亏比关系。

网关可以校验：

```text
take_profit_distance / stop_loss_distance >= params.min_reward_risk_ratio
```

但 `min_reward_risk_ratio` 必须由 Xquant 下发，不能在网关硬编码。

## 适用性摘要

`static_pct`
- 优点：简单、可审计、容易解释。
- 风险：不适应波动率。
- MVP 状态：已实现。

`trailing_pct`
- 优点：能保护高水位后的回撤。
- 风险：参数过紧会频繁触发。
- MVP 状态：已实现。

`atr_trailing`
- 优点：波动率自适应，适合趋势类规则。
- 风险：依赖历史 bar。
- MVP 状态：引擎和模拟 adapter 已实现，真实 QMT K 线未接入。

`hv_log_trailing`
- 优点：跨标的相对波动率更可比。
- 风险：对窗口和年化因子敏感。
- MVP 状态：引擎和模拟 adapter 已实现，真实 QMT K 线未接入。

`std_trailing`
- 优点：实现简单。
- 风险：价格尺度敏感。
- MVP 状态：引擎和模拟 adapter 已实现，真实 QMT K 线未接入。

## 执行风险

所有条件单动作都必须经过与普通任务一致的执行链路：

- 当前账户和持仓刷新。
- 价格刷新。
- 订单计划生成。
- 风控校验。
- real-order 双安全门。
- QMT 下单。
- 本地审计。
- Xquant 条件单结果上报。

条件单触发不等于立即无条件下单。任何风控或 QMT 失败都必须写入审计结果。

## 验证要求

测试应覆盖：

- 参数缺失。
- 参数类型错误。
- 参数范围错误。
- 基准价格来源。
- 高水位更新。
- 激活状态。
- 每种 method 的触发与未触发。
- `sell_pct` 与 `clear`。
- 试运行执行结果。
- 模拟已提交订单结果。
- 条件单结果审计请求体。
- bar 数据缺失或不足。
- 真实 QMT 模式下基于 bar 数据规则的评估错误。

## 已应用的来源修正

整理飞书来源时采用这些修正：

- 不把文档中的示例数值当作默认值。
- 不使用含糊的 `N` 作为 API 参数名。
- 追踪止损和追踪止盈按不同业务目的处理。
- 追踪止盈必须有激活门槛。
- 基准价格依赖成交持仓成本时，由本地 QMT 持仓刷新后确定。

## 实现指导

实现时遵循：

- Xquant 负责研究、回测和参数配置。
- trade-xquant 负责执行、状态维护、校验和审计。
- SQLite 保存运行时状态。
- 条件单结果上报必须幂等。
- 上报失败不能导致重复交易。
- 真实交易前必须先通过模拟运行和试运行验证。
