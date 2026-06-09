# 本地条件单 JSON

本文档说明 Xquant 服务端任务生成器仍在开发期间使用的临时本地 JSON 契约。

完整止损止盈规则分类、参数化要求、适用范围、风险和校验要求见
`docs/07-conditional-stop-take-profit-rules.md`。

本地 fixture 中的所有数值条件都只是本地开发占位值。生产数值必须由
Xquant 侧回测决定，并通过交易任务请求体下发。trade-xquant 不能从示例中
硬编码交易阈值。

目标是让 Xquant 后续通过正式 `/trading-gateway/tasks` API 发出同样结构。
本地文件只是开发数据源。

## 配置

设置：

```yaml
runtime:
  local_task_file: "fixtures/local-condition-task.json"
  broker_adapter: "mock"
  mock_submit_dry_run_orders: true
```

然后运行已有命令，例如：

```bash
trade-xquant mock-run --config config.yaml
```

不需要额外 CLI 参数。

## 文件结构

文件可以包含以下对象：

```json
{
  "tasks": []
}
```

也可以是顶层任务列表：

```json
[]
```

每个任务都使用现有 `RebalanceTask` 结构。止损止盈条件单位于
`constraints.condition_orders`。

## 示例

```json
{
  "tasks": [
    {
      "task_id": "task-20260603-001",
      "portfolio_id": "prod_etf_steady",
      "account_id": "acct",
      "mode": "dry_run",
      "created_at": "2026-06-03T09:35:00+08:00",
      "expires_at": null,
      "targets": [
        {
          "symbol": "513100.SH",
          "target_weight": 0.5
        }
      ],
      "constraints": {
        "max_turnover_ratio": 0.8,
        "condition_orders": [
          {
            "condition_id": "cond-513100-stop-001",
            "symbol": "513100.SH",
            "purpose": "stop_loss",
            "method": "static_pct",
            "reference_price": 1.0,
            "params": {
              "stop_loss_pct": 0.05
            },
            "action": {
              "type": "sell_pct",
              "pct": 1.0
            }
          },
          {
            "condition_id": "cond-513100-take-001",
            "symbol": "513100.SH",
            "purpose": "take_profit",
            "method": "static_pct",
            "reference_price": 1.0,
            "params": {
              "take_profit_pct": 0.1
            },
            "action": {
              "type": "sell_pct",
              "pct": 0.5
            }
          },
          {
            "condition_id": "cond-513100-trail-001",
            "symbol": "513100.SH",
            "purpose": "stop_loss",
            "method": "trailing_pct",
            "reference_price": 1.0,
            "params": {
              "trail_pct": 0.08
            },
            "action": {
              "type": "sell_pct",
              "pct": 1.0
            }
          }
        ]
      }
    }
  ]
}
```

## 支持的单标的规则

本地 JSON 可以模拟每一种 Xquant 单标的卖出侧条件规则：

- `static_pct`，搭配 `purpose: "stop_loss"`
- `static_pct`，搭配 `purpose: "take_profit"`
- `trailing_pct`，搭配 `purpose: "stop_loss"`
- `trailing_pct`，搭配 `purpose: "take_profit"`
- `atr_trailing`，搭配 `purpose: "stop_loss"`
- `atr_trailing`，搭配 `purpose: "take_profit"`
- `hv_log_trailing`，搭配 `purpose: "stop_loss"`
- `hv_log_trailing`，搭配 `purpose: "take_profit"`
- `std_trailing`，搭配 `purpose: "stop_loss"`
- `std_trailing`，搭配 `purpose: "take_profit"`

JSON 文件只模拟 Xquant 任务请求体。它不保存高水位状态、激活状态、
指标快照、触发证据或执行审计。这些内容保存在 SQLite 中。

任务请求体可以携带基准价格和规则超参数。市场推导出的状态由
trade-xquant 在运行时创建和更新。

当规则基准依赖执行后的持仓成本时，本地 JSON 应使用以下结构模拟
Xquant 请求体：

```json
{
  "reference": {"source": "position_cost_price"}
}
```

这种情况下，Xquant 请求体会省略 `reference_price`。执行后，
trade-xquant 读取最新 QMT 聚合持仓的 `cost_price`，并将其保存为条件单实例
的基准价格。

## 执行

当条件触发时，trade-xquant 会创建普通卖出 `PlannedOrder`。

订单使用：

```text
task_id = condition:{source_task_id}:{condition_id}
remark = cond:{condition_id}
price_type = latest
```

如果生成的 `task_id` 会超过 Xquant 长度限制，trade-xquant 会用短哈希替换
`source_task_id`。审计请求体仍包含完整 `source_task_id`。

订单随后进入现有 `RiskControl` 和 `ExecutionEngine`。

条件单状态保存在 SQLite：

- `condition_orders`
- `condition_order_events`

活跃状态：

- `received`
- `armed`

终态或非活跃状态：

- `triggered`
- `submitting`
- `submitted`
- `completed`
- `expired`
- `canceled`
- `failed`
- `needs_reconcile`
