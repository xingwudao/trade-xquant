# Daemon Order Lifecycle Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `trade-xquant daemon` automatically reconcile submitted QMT orders, cancel stale unfilled orders, and retry toward the original target weights within explicit safety limits.

**Architecture:** Extend the existing `GatewayService` rather than adding a separate worker. Add runtime configuration, small storage helpers for submitted-order age and result metadata, then split order lifecycle behavior into focused daemon methods that reuse existing `sync_results()`, `PortfolioEngine`, `RiskControl`, and `ExecutionEngine` paths.

**Tech Stack:** Python 3.10, Pydantic, SQLite storage, pytest, existing QMT/Xquant adapter interfaces.

---

## File Structure

- Modify `trade_xquant/config.py`
  - Add runtime settings for sync interval, timeout, and retry budget.
- Modify `config.example.yaml`
  - Document the new runtime defaults.
- Modify `trade_xquant/storage.py`
  - Add submitted-order timestamp lookup.
  - Add task result payload lookup for lifecycle metadata.
- Modify `trade_xquant/daemon.py`
  - Add submitted-order sync loop.
  - Add lifecycle reconciliation helpers.
  - Add cancellation and retry helpers.
- Modify `tests/test_sync_results.py`
  - Add brokers and tests for lifecycle sync, timeout, cancel, retry, and retry budget.
- Modify `tests/test_gateway_mock_broker.py`
  - Add a daemon loop scheduling test.
- Modify `README.md`
  - Document daemon order lifecycle settings and behavior.

---

### Task 1: Runtime Configuration

**Files:**
- Modify: `trade_xquant/config.py`
- Modify: `config.example.yaml`
- Test: `tests/test_cli_account.py`

- [ ] **Step 1: Write the failing config test**

Append this test to `tests/test_cli_account.py`:

```python
def test_runtime_config_includes_order_lifecycle_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)

    from trade_xquant.config import load_settings

    settings = load_settings(config_path)

    assert settings.runtime.order_sync_interval_seconds == 30
    assert settings.runtime.submitted_order_timeout_seconds == 180
    assert settings.runtime.max_rebalance_retries == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
PYTHONPATH=. pytest tests/test_cli_account.py::test_runtime_config_includes_order_lifecycle_defaults -q
```

Expected: FAIL with `AttributeError` for `order_sync_interval_seconds`.

- [ ] **Step 3: Add config fields**

In `trade_xquant/config.py`, extend `RuntimeConfig`:

```python
class RuntimeConfig(BaseModel):
    poll_interval_seconds: int = 30
    condition_poll_interval_seconds: int = 3
    order_sync_interval_seconds: int = 30
    submitted_order_timeout_seconds: int = 180
    max_rebalance_retries: int = 3
    allow_real_order: bool = False
```

Keep the rest of the existing fields unchanged.

- [ ] **Step 4: Update example config**

In `config.example.yaml`, under `runtime:`, add:

```yaml
  order_sync_interval_seconds: 30
  submitted_order_timeout_seconds: 180
  max_rebalance_retries: 3
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=. pytest tests/test_cli_account.py::test_runtime_config_includes_order_lifecycle_defaults -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add trade_xquant/config.py config.example.yaml tests/test_cli_account.py
git commit -m "feat: add daemon order lifecycle config"
```

---

### Task 2: Storage Helpers For Lifecycle Decisions

**Files:**
- Modify: `trade_xquant/storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing storage tests**

Append these tests to `tests/test_storage.py`:

```python
def test_storage_loads_latest_submitted_order_created_at(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.record_execution_result(
        ExecutionResult(
            task_id="task-1",
            status="submitted",
            mode="real",
            planned_orders=[],
            submitted_orders=[
                SubmittedOrder(
                    task_id="task-1",
                    symbol="513100.SH",
                    side="buy",
                    quantity=100,
                    price=1.0,
                    amount=100.0,
                    local_order_id="1",
                )
            ],
        )
    )

    value = storage.latest_submitted_order_created_at("task-1")

    assert isinstance(value, str)
    assert value


def test_storage_loads_task_result_payload(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.mark_task_result(
        "task-1",
        "submitted",
        {"status": "submitted", "meta": {"order_lifecycle": {"retry_count": 2}}},
    )

    payload = storage.load_task_result_payload("task-1")

    assert payload["meta"]["order_lifecycle"]["retry_count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. pytest tests/test_storage.py::test_storage_loads_latest_submitted_order_created_at tests/test_storage.py::test_storage_loads_task_result_payload -q
```

Expected: FAIL with missing `Storage` methods.

- [ ] **Step 3: Implement storage helpers**

Add these methods to `Storage` in `trade_xquant/storage.py`, near the other load helpers:

```python
    def latest_submitted_order_created_at(self, task_id: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT MAX(created_at) AS created_at
                FROM submitted_orders
                WHERE task_id=?
                """,
                (task_id,),
            ).fetchone()
        return str(row["created_at"]) if row and row["created_at"] else None

    def load_task_result_payload(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM task_results WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])
```

`storage.py` already imports `Any` and `json`, so no new imports should be needed.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=. pytest tests/test_storage.py::test_storage_loads_latest_submitted_order_created_at tests/test_storage.py::test_storage_loads_task_result_payload -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trade_xquant/storage.py tests/test_storage.py
git commit -m "feat: add order lifecycle storage helpers"
```

---

### Task 3: Add Submitted-Only Sync Method

**Files:**
- Modify: `trade_xquant/daemon.py`
- Test: `tests/test_sync_results.py`

- [ ] **Step 1: Write failing sync method test**

Append this test to `tests/test_sync_results.py`:

```python
def test_sync_submitted_orders_once_only_reconciles_submitted_and_partial(tmp_path) -> None:
    service = make_service_with_submitted_task(tmp_path, result_status="submitted")

    result = service.sync_submitted_orders_once()

    assert result == [{"task_id": "task-1", "status": "success"}]
    assert service.xquant.results[0][1] == "success"  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_once_only_reconciles_submitted_and_partial -q
```

Expected: FAIL with missing `sync_submitted_orders_once`.

- [ ] **Step 3: Implement minimal method**

In `GatewayService`, add this method just before `sync_results()`:

```python
    def sync_submitted_orders_once(self) -> list[dict[str, object]]:
        results = self.sync_results(status="submitted")
        partial_results = self.sync_results(status="partial")
        return results + partial_results
```

This is deliberately minimal. Timeout, cancellation, and retry are added in later tasks.

- [ ] **Step 4: Run test**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_once_only_reconciles_submitted_and_partial -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trade_xquant/daemon.py tests/test_sync_results.py
git commit -m "feat: add submitted order sync entrypoint"
```

---

### Task 4: Schedule Submitted Sync In Daemon Loop

**Files:**
- Modify: `trade_xquant/daemon.py`
- Test: `tests/test_gateway_mock_broker.py`

- [ ] **Step 1: Write failing scheduling test**

Append this test to `tests/test_gateway_mock_broker.py`:

```python
def test_daemon_loop_runs_submitted_order_sync(tmp_path, monkeypatch) -> None:
    settings = Settings(
        xquant=XquantConfig(base_url="http://xquant/api/v1"),
        qmt=QmtConfig(userdata_mini_path="C:/QMT/userdata_mini", account_id="acct"),
        runtime=RuntimeConfig(
            broker_adapter="mock",
            poll_interval_seconds=60,
            condition_poll_interval_seconds=60,
            order_sync_interval_seconds=1,
            db_path=str(tmp_path / "audit.db"),
            log_path=str(tmp_path / "gateway.jsonl"),
        ),
        risk=RiskConfig(),
    )
    service = GatewayService(settings)
    calls = {"sync": 0, "sleep": 0}
    service.poll_once = lambda: []  # type: ignore[method-assign]
    service.condition_poll_once = lambda: []  # type: ignore[method-assign]
    service.heartbeat_once = lambda last_error=None: {"ok": True}  # type: ignore[method-assign]

    def sync_once():
        calls["sync"] += 1
        if calls["sync"] >= 2:
            raise KeyboardInterrupt
        return []

    service.sync_submitted_orders_once = sync_once  # type: ignore[method-assign]

    def fake_sleep(seconds):
        calls["sleep"] += 1

    monkeypatch.setattr("trade_xquant.daemon.time.sleep", fake_sleep)

    with pytest.raises(KeyboardInterrupt):
        service.run_forever()

    assert calls["sync"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=. pytest tests/test_gateway_mock_broker.py::test_daemon_loop_runs_submitted_order_sync -q
```

Expected: FAIL because `run_forever()` does not call `sync_submitted_orders_once()`.

- [ ] **Step 3: Update daemon loop**

In `GatewayService.run_forever()`, add a third timer:

```python
        next_order_sync = 0.0
```

Inside the loop, after condition polling and before heartbeat:

```python
            if current >= next_order_sync:
                try:
                    self.sync_submitted_orders_once()
                except Exception as exc:  # noqa: BLE001 - order sync must not stop daemon
                    logger.exception("submitted order sync loop failed")
                    last_error = _append_error(last_error, str(exc))
                next_order_sync = current + self.settings.runtime.order_sync_interval_seconds
```

Update the sleep calculation:

```python
            sleep_until = min(next_task_poll, next_condition_poll, next_order_sync)
```

- [ ] **Step 4: Run test**

Run:

```bash
PYTHONPATH=. pytest tests/test_gateway_mock_broker.py::test_daemon_loop_runs_submitted_order_sync -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trade_xquant/daemon.py tests/test_gateway_mock_broker.py
git commit -m "feat: schedule submitted order sync in daemon"
```

---

### Task 5: Keep Fresh Submitted Orders Pending

**Files:**
- Modify: `trade_xquant/daemon.py`
- Test: `tests/test_sync_results.py`

- [ ] **Step 1: Add pending broker and test**

Append this broker and test to `tests/test_sync_results.py`:

```python
class PendingBroker(SnapshotBroker):
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.placed: list[PlannedOrder] = []

    def get_orders(self):
        return [
            SimpleNamespace(
                order_id=1082169287,
                stock_code="513100.SH",
                order_status=50,
                traded_volume=0,
                price=1.0,
                m_strRemark="task-1",
            )
        ]

    def get_trades(self):
        return []

    def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(str(order_id))

    def place_order(self, order: PlannedOrder):
        self.placed.append(order)
        return {"order_id": f"retry-{len(self.placed)}"}


def test_sync_submitted_orders_keeps_fresh_pending_order(tmp_path) -> None:
    broker = PendingBroker()
    service = make_service_with_result(
        tmp_path,
        broker=broker,
        result=submitted_result(),
        result_status="submitted",
    )
    service.settings.runtime.submitted_order_timeout_seconds = 3600

    result = service.sync_submitted_orders_once()

    assert result == [{"task_id": "task-1", "status": "submitted"}]
    assert broker.cancelled == []
    assert broker.placed == []
```

- [ ] **Step 2: Run test**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_keeps_fresh_pending_order -q
```

Expected: PASS with the current minimal implementation. This test locks in no premature cancellation.

- [ ] **Step 3: Commit**

```bash
git add tests/test_sync_results.py
git commit -m "test: keep fresh submitted orders pending"
```

---

### Task 6: Cancel Timed-Out Pending Orders Without Retrying Yet

**Files:**
- Modify: `trade_xquant/daemon.py`
- Test: `tests/test_sync_results.py`

- [ ] **Step 1: Write failing timeout cancellation test**

Append this test to `tests/test_sync_results.py`:

```python
def test_sync_submitted_orders_cancels_timed_out_pending_order(tmp_path) -> None:
    broker = PendingBroker()
    service = make_service_with_result(
        tmp_path,
        broker=broker,
        result=submitted_result(),
        result_status="submitted",
    )
    service.settings.runtime.submitted_order_timeout_seconds = 0
    service.settings.runtime.max_rebalance_retries = 0

    result = service.sync_submitted_orders_once()

    assert result == [{"task_id": "task-1", "status": "submitted"}]
    assert broker.cancelled == ["1082169287"]
    assert broker.placed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_cancels_timed_out_pending_order -q
```

Expected: FAIL because no cancellation occurs.

- [ ] **Step 3: Implement lifecycle helper skeleton**

In `trade_xquant/daemon.py`, import `ZoneInfo` is already present and `datetime` is already present. Add these methods to `GatewayService` after `sync_submitted_orders_once()`:

```python
    def _submitted_order_timed_out(self, task_id: str, now: datetime | None = None) -> bool:
        created_at = self.storage.latest_submitted_order_created_at(task_id)
        if not created_at:
            return False
        submitted_at = datetime.fromisoformat(created_at)
        current = now or datetime.now(ZoneInfo(self.settings.risk.timezone))
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=ZoneInfo(self.settings.risk.timezone))
        return (
            current.astimezone(submitted_at.tzinfo) - submitted_at
        ).total_seconds() >= self.settings.runtime.submitted_order_timeout_seconds

    def _cancel_pending_submitted_orders(
        self,
        submitted_orders: list,
        synced_orders: list,
    ) -> tuple[list[str], list[str]]:
        synced_by_id = {
            str(order.local_order_id or order.broker_order_id): order
            for order in synced_orders
            if order.local_order_id or order.broker_order_id
        }
        cancelled: list[str] = []
        errors: list[str] = []
        for order in submitted_orders:
            order_id = str(order.local_order_id or order.broker_order_id or "")
            if not order_id:
                errors.append(f"{order.symbol} {order.side} missing order id for cancel")
                continue
            synced = synced_by_id.get(order_id)
            if synced is not None and synced.status == "filled":
                continue
            try:
                self.qmt.cancel_order(order_id)
                cancelled.append(order_id)
            except Exception as exc:  # noqa: BLE001 - cancellation failures must be audited
                errors.append(f"{order.symbol} {order.side} cancel failed: {exc}")
        return cancelled, errors
```

- [ ] **Step 4: Call cancellation from `sync_submitted_orders_once()`**

Replace the minimal method from Task 3 with:

```python
    def sync_submitted_orders_once(self) -> list[dict[str, object]]:
        results = self.sync_results(status="submitted") + self.sync_results(status="partial")
        for result_item in results:
            task_id = str(result_item.get("task_id") or "")
            status = str(result_item.get("status") or "")
            if status not in {"submitted", "partial"}:
                continue
            if not self._submitted_order_timed_out(task_id):
                continue
            submitted_orders = self.storage.load_submitted_orders(task_id)
            payload = self.storage.load_task_result_payload(task_id) or {}
            synced_orders = [
                SubmittedOrder.model_validate(order)
                for order in payload.get("submitted_orders", [])
                if isinstance(order, dict)
            ]
            cancelled, errors = self._cancel_pending_submitted_orders(
                submitted_orders,
                synced_orders,
            )
            logger.info(
                "submitted order timeout handled: task_id=%s cancelled=%s errors=%s",
                task_id,
                len(cancelled),
                len(errors),
            )
        return results
```

Add `SubmittedOrder` to the `trade_xquant.models` import list in `daemon.py`.

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_keeps_fresh_pending_order tests/test_sync_results.py::test_sync_submitted_orders_cancels_timed_out_pending_order -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add trade_xquant/daemon.py tests/test_sync_results.py
git commit -m "feat: cancel timed out submitted orders"
```

---

### Task 7: Retry Normal Tasks After Timeout Cancellation

**Files:**
- Modify: `trade_xquant/daemon.py`
- Test: `tests/test_sync_results.py`

- [ ] **Step 1: Write failing retry test**

Append this test to `tests/test_sync_results.py`:

```python
def test_sync_submitted_orders_retries_after_timeout_cancel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRADE_XQUANT_ENABLE_REAL_ORDER", "1")
    broker = PendingBroker()
    service = make_service_with_result(
        tmp_path,
        broker=broker,
        result=submitted_result(),
        result_status="submitted",
    )
    service.settings.runtime.submitted_order_timeout_seconds = 0
    service.settings.runtime.max_rebalance_retries = 1
    service.settings.runtime.simulate_real_orders = True
    service.settings.runtime.mock_prices = {"513100.SH": 1.0}

    result = service.sync_submitted_orders_once()

    assert result[-1]["task_id"] == "task-1"
    assert broker.cancelled == ["1082169287"]
    assert len(broker.placed) == 1
    payload = service.storage.load_task_result_payload("task-1")
    assert payload["meta"]["order_lifecycle"]["retry_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_retries_after_timeout_cancel -q
```

Expected: FAIL because cancellation does not submit a retry.

- [ ] **Step 3: Add lifecycle metadata helpers**

Add these methods to `GatewayService`:

```python
    def _order_lifecycle_meta(self, task_id: str) -> dict[str, object]:
        payload = self.storage.load_task_result_payload(task_id) or {}
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        if not isinstance(meta, dict):
            return {"retry_count": 0}
        lifecycle = meta.get("order_lifecycle")
        if not isinstance(lifecycle, dict):
            return {"retry_count": 0}
        return dict(lifecycle)

    def _retry_count(self, task_id: str) -> int:
        value = self._order_lifecycle_meta(task_id).get("retry_count", 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
```

- [ ] **Step 4: Add retry submission helper**

Add this method to `GatewayService`:

```python
    def _retry_rebalance_task(
        self,
        task_id: str,
        *,
        cancelled_order_ids: list[str],
        reason: str,
    ) -> dict[str, object]:
        retry_count = self._retry_count(task_id)
        if retry_count >= self.settings.runtime.max_rebalance_retries:
            logger.info("rebalance retry budget exhausted: task_id=%s", task_id)
            return {"task_id": task_id, "status": "retry_budget_exhausted"}

        task = self.storage.load_task(task_id)
        if task is None:
            logger.error("cannot retry missing task: task_id=%s", task_id)
            return {"task_id": task_id, "status": "missing_task"}

        self.qmt.connect()
        account = self.qmt.get_account_snapshot()
        positions = self.qmt.get_positions()
        symbols = [target.symbol for target in task.targets] + [position.symbol for position in positions]
        prices = self.qmt.get_prices(symbols)
        plan = self.portfolio.build_plan(task, account, positions, prices)
        self.risk.validate(task, account, plan, known_symbols=set(prices))
        self.storage.record_plan(plan)
        result = ExecutionEngine(self.qmt, self.settings.runtime).execute(plan, task.mode)
        result.meta["order_lifecycle"] = {
            "retry_count": retry_count + 1,
            "last_retry_at": datetime.now(ZoneInfo(self.settings.risk.timezone)).isoformat(),
            "cancelled_order_ids": cancelled_order_ids,
            "reason": reason,
        }
        self._attach_current_account_snapshot(
            result,
            task,
            fallback_account=account,
            fallback_positions=positions,
            fallback_prices=prices,
        )
        self.storage.record_execution_result(result)
        status = result.status if result.status in {"dry_run_success", "submitted"} else "failed"
        self.storage.mark_task_result(task_id, status, result.model_dump(mode="json"))
        self.xquant.report_result(task_id, status, result)
        return {"task_id": task_id, "status": status, "retry_count": retry_count + 1}
```

- [ ] **Step 5: Call retry after successful cancellation**

In `sync_submitted_orders_once()`, after cancellation:

```python
            if errors:
                continue
            if cancelled:
                results.append(
                    self._retry_rebalance_task(
                        task_id,
                        cancelled_order_ids=cancelled,
                        reason="submitted_order_timeout",
                    )
                )
```

- [ ] **Step 6: Run tests**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_retries_after_timeout_cancel -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add trade_xquant/daemon.py tests/test_sync_results.py
git commit -m "feat: retry timed out submitted tasks"
```

---

### Task 8: Prevent Retry When Cancellation Fails

**Files:**
- Modify: `trade_xquant/daemon.py`
- Test: `tests/test_sync_results.py`

- [ ] **Step 1: Add failing broker and test**

Append to `tests/test_sync_results.py`:

```python
class FailingCancelBroker(PendingBroker):
    def cancel_order(self, order_id: str) -> None:
        raise RuntimeError("cancel rejected")


def test_sync_submitted_orders_does_not_retry_when_cancel_fails(tmp_path) -> None:
    broker = FailingCancelBroker()
    service = make_service_with_result(
        tmp_path,
        broker=broker,
        result=submitted_result(),
        result_status="submitted",
    )
    service.settings.runtime.submitted_order_timeout_seconds = 0
    service.settings.runtime.max_rebalance_retries = 1

    result = service.sync_submitted_orders_once()

    assert result == [{"task_id": "task-1", "status": "submitted"}]
    assert broker.placed == []
    payload = service.storage.load_task_result_payload("task-1")
    assert payload["status"] == "submitted"
```

- [ ] **Step 2: Run test**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_does_not_retry_when_cancel_fails -q
```

Expected: PASS if Task 7 correctly skips retry on cancellation errors. If it fails, update `sync_submitted_orders_once()` so retry only happens when `errors` is empty.

- [ ] **Step 3: Commit if code changed**

If code changed:

```bash
git add trade_xquant/daemon.py tests/test_sync_results.py
git commit -m "test: prevent retry after cancellation failure"
```

If only the test was added:

```bash
git add tests/test_sync_results.py
git commit -m "test: prevent retry after cancellation failure"
```

---

### Task 9: Enforce Retry Budget Persistence

**Files:**
- Modify: `trade_xquant/daemon.py`
- Test: `tests/test_sync_results.py`

- [ ] **Step 1: Write failing retry budget test**

Append to `tests/test_sync_results.py`:

```python
def test_sync_submitted_orders_respects_persisted_retry_budget(tmp_path) -> None:
    broker = PendingBroker()
    result = submitted_result()
    result.meta["order_lifecycle"] = {"retry_count": 1}
    service = make_service_with_result(
        tmp_path,
        broker=broker,
        result=result,
        result_status="submitted",
    )
    service.settings.runtime.submitted_order_timeout_seconds = 0
    service.settings.runtime.max_rebalance_retries = 1

    sync_result = service.sync_submitted_orders_once()

    assert {"task_id": "task-1", "status": "retry_budget_exhausted"} in sync_result
    assert broker.cancelled == ["1082169287"]
    assert broker.placed == []
```

- [ ] **Step 2: Run test**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_orders_respects_persisted_retry_budget -q
```

Expected: PASS if Task 7 metadata handling is correct. If it fails, fix `_retry_count()` or `_retry_rebalance_task()`.

- [ ] **Step 3: Commit**

```bash
git add trade_xquant/daemon.py tests/test_sync_results.py
git commit -m "feat: enforce rebalance retry budget"
```

---

### Task 10: Condition Task Reporting On Retry

**Files:**
- Modify: `trade_xquant/daemon.py`
- Test: `tests/test_sync_results.py`

- [ ] **Step 1: Write failing condition retry test**

Append to `tests/test_sync_results.py`:

```python
def test_sync_submitted_condition_retry_reports_condition_endpoint(tmp_path) -> None:
    broker = PendingBroker()
    service = make_service_with_condition_result(tmp_path, result_status="submitted")
    service.qmt = broker  # type: ignore[assignment]
    service.settings.runtime.submitted_order_timeout_seconds = 0
    service.settings.runtime.max_rebalance_retries = 1
    service.settings.runtime.simulate_real_orders = True
    service.storage.record_task_received(
        RebalanceTask(
            task_id="condition:cond-sync",
            portfolio_id="prod",
            account_id="acct",
            mode="real",
            created_at=task().created_at,
            expires_at=None,
            targets=[{"symbol": "513100.SH", "target_weight": 0}],
            raw={"condition_id": "cond-sync", "source_task_id": "task-1"},
        ),
        status="submitted",
    )

    result = service.sync_submitted_orders_once()

    assert broker.cancelled == ["1082169287"]
    assert any(item["task_id"] == "condition:cond-sync" for item in result)
    assert service.xquant.condition_results  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_condition_retry_reports_condition_endpoint -q
```

Expected: FAIL until condition retry/report handling is implemented.

- [ ] **Step 3: Route condition retry reports**

Update `_retry_rebalance_task()`:

```python
        if _is_condition_task_id(task_id):
            report_error = self._record_and_report_synced_condition_result(task_id, result)
            if report_error is not None:
                return {
                    "task_id": task_id,
                    "status": status,
                    "xquant_synced": False,
                    "error": str(report_error),
                }
        else:
            self.xquant.report_result(task_id, status, result)
```

Remove the unconditional `self.xquant.report_result(task_id, status, result)` from Task 7.

- [ ] **Step 4: Run test**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py::test_sync_submitted_condition_retry_reports_condition_endpoint -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trade_xquant/daemon.py tests/test_sync_results.py
git commit -m "feat: retry submitted condition tasks"
```

---

### Task 11: README Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add daemon lifecycle documentation**

In `README.md`, after the `daemon` section, add:

```markdown
### Daemon 自动同步订单生命周期

`daemon` 会周期性同步已提交订单状态：

- `runtime.order_sync_interval_seconds` 控制同步频率，默认 `30` 秒。
- `runtime.submitted_order_timeout_seconds` 控制未成交超时时间，默认 `180` 秒。
- `runtime.max_rebalance_retries` 控制同一任务最多重试次数，默认 `3` 次。

同步时会查询 QMT 委托和成交：

- 全部成交后回传 `success`。
- 部分成交回传 `partial`。
- 未成交且未超时保持 `submitted`。
- 超时后先撤未成交委托，再刷新账户和持仓，按原目标权重重新生成订单。
- 撤单失败时不会重新下单，避免重复活跃委托。
```

- [ ] **Step 2: Verify README mentions new config keys**

Run:

```bash
rg -n "order_sync_interval_seconds|submitted_order_timeout_seconds|max_rebalance_retries" README.md config.example.yaml
```

Expected: all three keys appear in both files.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document daemon order lifecycle sync"
```

---

### Task 12: Full Verification

**Files:**
- No source edits unless failures reveal bugs.

- [ ] **Step 1: Run focused suites**

Run:

```bash
PYTHONPATH=. pytest tests/test_sync_results.py tests/test_gateway_mock_broker.py tests/test_storage.py -q
```

Expected: PASS.

- [ ] **Step 2: Run all tests**

Run:

```bash
PYTHONPATH=. pytest -q
```

Expected: PASS.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: only intended files changed.

- [ ] **Step 4: Commit remaining verification fixes if needed**

If any small fixes were needed during verification:

```bash
git add <changed-files>
git commit -m "fix: stabilize daemon order lifecycle automation"
```

If no fixes were needed, do not create an empty commit.

---

## Self-Review Notes

- Spec coverage:
  - Config defaults: Task 1.
  - Daemon order sync loop: Tasks 3 and 4.
  - Submitted/partial reconciliation: Tasks 3 and 5.
  - Timeout cancellation: Task 6.
  - Retry after cancellation: Task 7.
  - Cancellation failure safety: Task 8.
  - Retry budget persistence: Task 9.
  - Condition task endpoint: Task 10.
  - Documentation: Task 11.
- Completion scan:
  - No incomplete markers or unspecified code steps remain.
- Type consistency:
  - Methods use existing `GatewayService`, `Storage`, `ExecutionResult`,
    `SubmittedOrder`, `RebalanceTask`, and broker interfaces.
  - New config keys are consistently named across config, tests, and docs.
