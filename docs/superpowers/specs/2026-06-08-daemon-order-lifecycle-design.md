# Daemon Order Lifecycle Automation Design

## Goal

Make `trade-xquant daemon` the primary unattended runtime for real trading.
The daemon should not stop at fetching Xquant tasks and submitting QMT orders.
It must also keep account state fresh, reconcile submitted orders, cancel stale
unfilled orders, and retry toward the original target weights within explicit
safety limits.

## Current Behavior

`daemon` currently runs two periodic loops:

- `poll_once()` every `runtime.poll_interval_seconds`.
- `condition_poll_once()` every `runtime.condition_poll_interval_seconds`.

`poll_once()` submits real orders and records a task result with status
`submitted`. It also reports that status to Xquant. It does not automatically
call `sync_results()`.

`sync_results()` already performs one reconciliation pass:

- Load local tasks with result status `submitted`, `success`, `failed`, or
  `partial` that have `submitted_orders`.
- Query QMT orders and trades.
- Match QMT payloads to local submitted orders.
- Summarize status as `success`, `partial`, `failed`, or `submitted`.
- Persist the refreshed execution result.
- Report the result to Xquant.

The missing daemon behavior is automatic scheduling and lifecycle action after
the reconciliation result is still incomplete.

## Configuration

Add runtime settings:

```yaml
runtime:
  order_sync_interval_seconds: 30
  submitted_order_timeout_seconds: 180
  max_rebalance_retries: 3
```

Defaults:

- `order_sync_interval_seconds`: `30`.
- `submitted_order_timeout_seconds`: `180`.
- `max_rebalance_retries`: `3`.

These settings belong under `runtime` because they govern gateway execution
behavior, not Xquant strategy generation or account-level risk limits.

## Daemon Loops

`GatewayService.run_forever()` should maintain three independent periodic
timers:

- Task polling and heartbeat.
- Condition order polling.
- Submitted order synchronization.

The submitted order loop should call a new method:

```python
GatewayService.sync_submitted_orders_once()
```

This method should:

- Initialize storage.
- Find tasks with local `task_results.status` in `submitted` or `partial`.
- Query QMT orders and trades once per pass.
- Reconcile each task.
- Report the latest result to Xquant.
- Decide whether incomplete tasks need cancellation and retry.

## Retry State

Retries must be persisted so daemon restarts do not reset retry count.

Use the existing `ExecutionResult.meta` object for the first implementation:

```json
{
  "order_lifecycle": {
    "retry_count": 1,
    "last_retry_at": "2026-06-08T09:35:00+08:00",
    "cancelled_order_ids": ["1082169287"],
    "reason": "submitted_order_timeout"
  }
}
```

The retry count is task-scoped. It counts retry submissions after the original
task submission. If `max_rebalance_retries` is `3`, a task can have the
original submission plus at most three retry submissions.

## Reconciliation Outcomes

`success`

- All submitted orders are fully filled.
- Persist status `success`.
- Report `success` to Xquant.
- Do not retry.

`submitted`

- No matched order is failed.
- No matched trade has filled any quantity.
- At least one order is still pending.
- If elapsed time since the current submission is below
  `submitted_order_timeout_seconds`, keep status `submitted`.
- If elapsed time reaches the timeout, attempt cancellation and retry.

`partial`

- At least one order has filled quantity, but the task has not reached the
  final target.
- Persist and report `partial`.
- If there are still pending unfilled quantities past the timeout, cancel the
  pending orders and retry from the fresh account and position snapshot.

`failed`

- QMT reports failed or rejected orders and no quantity filled.
- Persist and report `failed` only after retry budget is exhausted.
- Before retry budget is exhausted, refresh account and positions, rebuild the
  order plan from the original target weights, and submit a retry.

## Cancellation

Cancellation should only target orders that:

- Belong to the local task being reconciled.
- Are matched to local `submitted_orders`.
- Are not known to be fully filled.
- Have a usable local or broker order id.

Cancellation should use `broker.cancel_order(order_id)`.

If cancellation fails for any pending order:

- Do not submit retry orders in the same pass.
- Persist the latest reconciliation result.
- Record an error in `ExecutionResult.errors`.
- Keep the task in `submitted` or `partial` unless QMT clearly reports terminal
  failure.
- Report the latest state to Xquant.

This avoids duplicate live orders when the gateway cannot prove the old order
was cancelled.

## Retry Submission

Retry submission should rebuild from the original Xquant task:

1. Load the original `RebalanceTask` from storage.
2. Query fresh account snapshot.
3. Query fresh positions.
4. Query current prices for task targets and existing positions.
5. Build a new plan with `PortfolioEngine.build_plan()`.
6. Validate with `RiskControl.validate()`.
7. Submit with `ExecutionEngine.execute()`.
8. Persist the new plan, submitted orders, events, trades, and execution result.
9. Preserve and increment `meta.order_lifecycle.retry_count`.
10. Report the new status to Xquant.

Retry submission must preserve the original task id. This keeps the task
idempotency model simple and lets Xquant see a single task lifecycle.

## Trading Session Guard

Automatic retry orders should only be submitted inside the same A-share trading
session guard already enforced by `RiskControl`.

If an order times out outside the allowed trading window:

- Do not cancel and retry.
- Persist and report the latest `submitted` or `partial` state.
- Retry evaluation can happen again on the next daemon pass.

## Account State Sync

Heartbeat already reports account snapshot fields when QMT is connected:

- `cash`.
- `total_asset`.
- `holdings`.

The daemon should continue to use heartbeat as the account-state reporting path.
The order sync loop should also attach a current account snapshot to every
reconciled execution result before reporting to Xquant.

This keeps Xquant current even when no new trading tasks arrive.

## Condition Orders

Condition-triggered tasks use synthetic task ids beginning with `condition:`.
The same order lifecycle rules apply:

- Reconcile submitted condition orders.
- Cancel and retry stale incomplete condition orders within retry budget.
- Report condition results through the existing condition-result endpoint.

Condition retry should not re-arm the condition rule. It should continue the
execution lifecycle for the already-triggered condition task.

## Safety Rules

The daemon must never:

- Reprocess tasks with terminal local status `success` or `failed` unless an
  explicit sync command asks for that status.
- Submit retry orders if cancellation of old pending orders failed.
- Exceed `runtime.max_rebalance_retries`.
- Retry without running `RiskControl.validate()`.
- Retry when the original task cannot be loaded from storage.
- Retry when QMT connection or account snapshot query fails.

The daemon may keep reporting `submitted` or `partial` until it can safely
prove the next action.

## Observability

Add structured log messages for:

- Order sync pass start and result counts.
- Per-task reconciliation status.
- Timeout detection.
- Cancellation attempts and failures.
- Retry count and retry submission result.
- Retry budget exhausted.

Console logs already include timestamps after the logging formatter update.
File logs remain JSON and should include the same messages.

## Tests

Add tests for:

- `daemon` calls submitted-order sync on its own interval.
- Submitted unfilled orders below timeout remain `submitted`.
- Submitted unfilled orders past timeout are cancelled and retried.
- Partial fill past timeout cancels pending order and retries only remaining
  target drift through full plan rebuild.
- Cancellation failure prevents retry submission.
- Retry budget exhaustion leaves final `failed` or `partial` state and reports
  to Xquant.
- Retry count persists across service instances through stored result metadata.
- Condition task retries report through condition-result endpoint.
- Account snapshot is attached to reconciled reports.

## Out Of Scope

- Xquant-side task state machine changes.
- A separate order lifecycle table.
- Broker-specific smart order routing.
- Intraday price chasing beyond rebuilding the existing target-weight plan.
- Cross-day persistence policy for stale live orders.

Those are separate features and should get their own design if production use
shows they are required.
