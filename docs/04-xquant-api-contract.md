# Xquant 交易网关 API 契约

## 背景

当订阅组合在计算日产生新信号时，Xquant 会生成账户维度的交易任务。
任务在被新信号取代前，或被 Xquant 标记为终态前保持有效。

本地网关使用本契约完成鉴权、注册账户、拉取可执行任务，并回传订单计划
和执行结果。

## 鉴权

使用以下方式之一：

- `Authorization: Bearer <token>`
- 如果 Xquant 后续选择服务令牌鉴权，也可以使用内部 token header。

MVP 在配置了 `xquant.api_token` 时发送 `Authorization`。

## 注册账户

`POST /api/v1/trading-gateway/accounts`

请求体：

```json
{
  "account_id": "replace-with-qmt-account-id",
  "broker": "gjzq_qmt",
  "client_type": "qmt_mini",
  "enabled": true,
  "meta": {
    "gateway_version": "0.1.0"
  }
}
```

该接口应按 `account_id` 幂等。

## 心跳

`POST /api/v1/trading-gateway/accounts/{account_id}/heartbeat`

请求体：

```json
{
  "status": "online",
  "meta": {
    "version": "0.1.0"
  }
}
```

## 拉取待处理任务

`GET /api/v1/trading-gateway/tasks?account_id={account_id}&limit=10`

响应体：

```json
{
  "tasks": [
    {
      "task_id": "rebalance_20260527_001",
      "portfolio_id": "demo_etf_rotation",
      "account_id": "replace-with-qmt-account-id",
      "mode": "dry_run",
      "signal_as_of_date": "2026-05-20",
      "signal_effective_date": "2026-05-21",
      "created_at": "2026-05-27T09:35:00+08:00",
      "expires_at": null,
      "cash_buffer_ratio": 0.002,
      "targets": [
        {"symbol": "513100.SH", "target_weight": 0.5},
        {"symbol": "510300.SH", "target_weight": 0.5}
      ],
      "constraints": {
        "max_turnover_ratio": 0.8,
        "max_single_order_amount": 50000,
        "min_order_amount": 1000
      }
    }
  ]
}
```

规则：

- `task_id` 全局唯一，并作为幂等键。
- `account_id` 必须匹配本地 QMT 账户。
- `signal_as_of_date` 是信号计算日期。
- `signal_effective_date` 是写入调仓任务 ID 的生效日期。
- 目标权重总和必须 `<= 1`，剩余部分视为现金。
- `expires_at` 可以为 `null`；为空时，任务有效性持续到被新任务取代。
- Xquant 不应继续把已取代或终态任务作为待处理任务返回。
- MVP 不支持的资产类别不应下发。

## 手动任务预览

`POST /api/v1/trading-gateway/products/{product_code}/manual-tasks/preview`

请求体：

```json
{
  "account_id": "replace-with-qmt-account-id",
  "mode": "dry_run"
}
```

响应体：

```json
{
  "product_code": "prod_leading_stocks_rotation",
  "account_id": "replace-with-qmt-account-id",
  "mode": "dry_run",
  "trigger_type": "manual",
  "signal_as_of_date": "2026-05-20",
  "signal_effective_date": "2026-05-21",
  "cash_buffer_ratio": "0.002",
  "targets": [
    {"symbol": "300308.SZ", "target_weight": "0.1867535594777999"}
  ],
  "constraints": {
    "max_turnover_ratio": 0.8,
    "max_single_order_amount": 50000,
    "min_order_amount": 1000
  },
  "preview_token": "opaque-confirmation-token"
}
```

规则：

- 预览不能创建任务。
- 确认接口必须携带 `preview_token`。
- Xquant 应从可执行的增量信号生成预览数据，
  不应从空的最新 target-only 信号生成。

## 手动任务确认

`POST /api/v1/trading-gateway/products/{product_code}/manual-tasks`

请求体：

```json
{
  "account_id": "replace-with-qmt-account-id",
  "mode": "dry_run",
  "preview_token": "opaque-confirmation-token"
}
```

响应体：

```json
{
  "ok": true,
  "task_id": "manual_rebalance_prod_leading_stocks_rotation_20260521_xxx_dry_run_abc",
  "status": "pending",
  "trigger_type": "manual"
}
```

创建出的任务随后应由 `GET /trading-gateway/tasks` 返回，直到网关认领
并完成它。

## 上报计划

`POST /api/v1/trading-gateway/tasks/{task_id}/plan`

请求体是本地 `OrderPlan` JSON：

```json
{
  "task_id": "rebalance_20260527_001",
  "account_id": "replace-with-qmt-account-id",
  "total_asset": 100000,
  "turnover_amount": 50000,
  "turnover_ratio": 0.5,
  "orders": [
    {
      "symbol": "513100.SH",
      "side": "buy",
      "quantity": 10000,
      "price": 5,
      "amount": 50000
    }
  ]
}
```

## 上报结果

`POST /api/v1/trading-gateway/tasks/{task_id}/result`

请求体：

```json
{
  "status": "success",
  "mode": "dry_run",
  "planned_orders": [],
  "submitted_orders": [],
  "trades": [],
  "events": [],
  "errors": [],
  "meta": {}
}
```

状态值：

- `success`
- `failed`
- `dry_run_success`
- `submitted`
- `superseded`

Xquant 应把同一个 `task_id` 的重复结果上报视为幂等更新。

## 上报条件单结果

`POST /api/v1/trading-gateway/tasks/{source_task_id}/condition-results`

当条件规则触发且本地条件单执行产生 `ExecutionResult` 后，
网关调用该接口。

请求体包含：

- `source_task_id`
- `condition_id`
- `condition_task_id`
- `account_id`
- `portfolio_id`
- `symbol`
- `status`
- `trigger`，包含 `triggered_at`、`latest_price`、`trigger_price` 和 `reason`
- `rule`，包含 `scope`、`purpose`、`method`、`params` 和 `action`
- `market_state`，包含最新价、高水位价格、触发价格、激活状态、
  指标值、计算时间、行情数据来源，以及嵌套的本地状态字段
- `execution_result`，即本地 `ExecutionResult` JSON

示例数值只用于说明结构。数值型规则参数由 Xquant 侧研究、回测和配置决定，
并通过任务下发。`market_state` 是网关运行时快照，不是硬编码阈值或默认值。

```json
{
  "source_task_id": "task-20260603-001",
  "condition_id": "cond-513100-atr-tp",
  "condition_task_id": "condition:cond-513100-atr-tp",
  "account_id": "acct",
  "portfolio_id": "prod_etf_steady",
  "symbol": "513100.SH",
  "status": "dry_run_success",
  "trigger": {
    "triggered_at": "2026-06-03T10:30:00+08:00",
    "latest_price": 1.23,
    "trigger_price": 1.18,
    "reason": "latest_price <= trigger_price"
  },
  "rule": {
    "scope": "instrument",
    "purpose": "take_profit",
    "method": "atr_trailing",
    "params": {
      "activation_profit_pct": 0.12,
      "atr_window": 14,
      "atr_multiple": 2.0,
      "bar_interval": "1d"
    },
    "action": {
      "type": "sell_pct",
      "pct": 1.0
    }
  },
  "market_state": {
    "latest_price": 1.23,
    "high_water_price": 1.4,
    "trigger_price": 1.18,
    "activated": true,
    "activated_at": "2026-06-03T10:20:00+08:00",
    "activation_price": 1.12,
    "atr_value": 0.03,
    "hv_value": null,
    "std_value": null,
    "computed_at": "2026-06-03T10:30:00+08:00",
    "market_data_source": "qmt",
    "state": {
      "method": "atr_trailing",
      "purpose": "take_profit",
      "params": {
        "activation_profit_pct": 0.12,
        "atr_window": 14,
        "atr_multiple": 2.0,
        "bar_interval": "1d"
      },
      "activation_price": 1.12
    }
  },
  "execution_result": {
    "task_id": "condition:cond-513100-atr-tp",
    "status": "dry_run_success",
    "mode": "dry_run",
    "planned_orders": [
      {
        "task_id": "condition:cond-513100-atr-tp",
        "symbol": "513100.SH",
        "side": "sell",
        "quantity": 1000,
        "price": 1.23,
        "amount": 1230.0
      }
    ],
    "submitted_orders": [],
    "trades": [],
    "events": [],
    "holdings": [],
    "cash": null,
    "total_asset": null,
    "errors": [],
    "meta": {}
  }
}
```

规则：

- `condition_task_id` 是幂等键。
- 对条件单触发的交易，trade-xquant 会在长度允许时使用
  `condition:{source_task_id}:{condition_id}`。如果超过 160 字符，
  trade-xquant 使用 `condition:{source_task_hash}:{condition_id}`，
  并仍在审计请求体中发送 `source_task_id`。
- 条件单结果上报失败不能导致重复交易。
- Xquant 应按 `condition_task_id` 幂等接受重复审计上报。

## 条件规则模板职责

Xquant 应把条件规则模板作为组合或账户级配置发送。网关在本地执行并刷新
QMT 持仓后，把这些模板实例化到每个标的的聚合持仓上。

Xquant 发送：

- `id`: 稳定的规则模板 ID，例如 `stop_loss-trailing_pct-1`
- `scope: "instrument"`
- `purpose`
- `method`
- `params`: 仅包含回测得出的超参数
- `action`
- `reference.source: "position_cost_price"`

对于依赖成交后持仓成本作为基准的规则，Xquant 不应发送
`reference_price`。
交易完成后，trade-xquant 读取最新 QMT 持仓 `cost_price`，将其写为本地
条件单实例的 `reference_price`，并在本地推导激活价格、高水位价格、
触发价格等运行时数值。

当前 MVP 中，规则实例绑定到聚合持仓：

```text
account_id + portfolio_id + symbol + rule_template_id
```

trade-xquant 将每个模板展开为本地条件单实例：

```text
condition_id = cond-{portfolio_id}-{symbol}-{rule_template_id}
```

当同一组合后续加仓同一 `symbol` 时，Xquant 应保持同一个规则模板 ID。
trade-xquant 从 QMT 刷新聚合持仓基准价格，并更新已有条件单实例，
不要求 Xquant 创建批次级规则。
