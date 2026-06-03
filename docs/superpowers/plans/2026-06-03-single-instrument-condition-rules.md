# Single-Instrument Condition Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete all single-instrument sell-side stop-loss and take-profit rules with task-supplied hyperparameters, locally computed market state, SQLite audit state, and Xquant audit reporting.

**Architecture:** Xquant tasks, mocked by local JSON during development, carry condition hyperparameters under `constraints.condition_orders`. The gateway persists condition rules and runtime state in SQLite, computes market-derived values from latest prices and historical bars, executes triggered sell orders through the existing risk and execution path, then reports a condition audit payload to Xquant.

**Tech Stack:** Python 3.10, Pydantic v2, SQLite, pytest, httpx MockTransport, existing trade-xquant gateway modules.

---

## File Structure

Create:

- `trade_xquant/condition_indicators.py`
  - Price bar models.
  - ATR, HV log-return volatility, and price standard deviation calculations.

- `tests/test_condition_indicators.py`
  - Deterministic indicator tests.

- `tests/test_condition_rule_schema.py`
  - Local JSON and Pydantic parsing tests for all single-instrument methods.

Modify:

- `trade_xquant/condition_orders.py`
  - Extend supported methods.
  - Add market-state and trigger-audit models.
  - Validate required hyperparameters.
  - Use indicator snapshots and activation state in trigger calculations.

- `trade_xquant/storage.py`
  - Add `condition_market_states`.
  - Add `condition_trigger_audits`.
  - Add load/save methods for market state and audits.

- `trade_xquant/broker.py`
  - Add `PriceBar` import and `get_price_bars` protocol method.

- `trade_xquant/mock_qmt_adapter.py`
  - Add configurable bars for tests.
  - Implement `get_price_bars`.

- `trade_xquant/qmt_adapter.py`
  - Add `get_price_bars` method that raises a clear unsupported error until a QMT historical-bar implementation is wired.

- `trade_xquant/daemon.py`
  - Pass broker market data to `ConditionEngine`.
  - Record trigger audits after execution.
  - Report condition audit payloads to Xquant.
  - Mark audit-report failure without re-submitting trades.

- `trade_xquant/xquant_adapter.py`
  - Add `report_condition_result`.

- `docs/local-condition-orders.md`
  - Expand local JSON examples to all single-instrument rules.

- `docs/xquant-api-contract.md`
  - Add condition-result audit endpoint proposal.

Modify tests:

- `tests/test_condition_orders.py`
- `tests/test_gateway_condition_orders.py`
- `tests/test_local_task_file_adapter.py`
- `tests/test_mock_qmt_adapter.py`
- `tests/test_xquant_adapter.py`

---

### Task 1: Indicator Engine

**Files:**
- Create: `trade_xquant/condition_indicators.py`
- Create: `tests/test_condition_indicators.py`

- [ ] **Step 1: Write failing indicator tests**

Create `tests/test_condition_indicators.py`:

```python
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from trade_xquant.condition_indicators import (
    ConditionIndicatorEngine,
    PriceBar,
)


def bars() -> list[PriceBar]:
    tz = ZoneInfo("Asia/Shanghai")
    return [
        PriceBar(symbol="513100.SH", high=10.5, low=9.5, close=10.0, timestamp=datetime(2026, 6, 1, tzinfo=tz)),
        PriceBar(symbol="513100.SH", high=11.0, low=10.0, close=10.8, timestamp=datetime(2026, 6, 2, tzinfo=tz)),
        PriceBar(symbol="513100.SH", high=12.2, low=10.6, close=12.0, timestamp=datetime(2026, 6, 3, tzinfo=tz)),
    ]


def test_atr_uses_true_range_average() -> None:
    engine = ConditionIndicatorEngine()

    value = engine.atr(bars())

    assert value == 1.2


def test_hv_uses_annualized_log_returns() -> None:
    engine = ConditionIndicatorEngine()

    value = engine.hv_log_return(bars(), annualization=252)

    returns = [
        math.log(10.8 / 10.0),
        math.log(12.0 / 10.8),
    ]
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / len(returns)
    expected = math.sqrt(variance) * math.sqrt(252)
    assert round(value, 10) == round(expected, 10)


def test_price_standard_deviation_uses_closes() -> None:
    engine = ConditionIndicatorEngine()

    value = engine.price_std(bars())

    closes = [10.0, 10.8, 12.0]
    mean = sum(closes) / len(closes)
    expected = math.sqrt(sum((item - mean) ** 2 for item in closes) / len(closes))
    assert round(value, 10) == round(expected, 10)


def test_indicator_engine_rejects_insufficient_bars() -> None:
    engine = ConditionIndicatorEngine()

    try:
        engine.hv_log_return(bars()[:1], annualization=252)
    except ValueError as exc:
        assert "at least two bars" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_condition_indicators.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'trade_xquant.condition_indicators'
```

- [ ] **Step 3: Implement indicator engine**

Create `trade_xquant/condition_indicators.py`:

```python
from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from trade_xquant.models import normalize_symbol, round_money


class PriceBar(BaseModel):
    symbol: str
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    timestamp: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol_value(cls, value: str) -> str:
        return normalize_symbol(value)


class ConditionIndicatorEngine:
    def atr(self, bars: list[PriceBar]) -> float:
        if not bars:
            raise ValueError("ATR requires at least one bar")
        true_ranges: list[float] = []
        previous_close: float | None = None
        for bar in bars:
            ranges = [bar.high - bar.low]
            if previous_close is not None:
                ranges.append(abs(bar.high - previous_close))
                ranges.append(abs(previous_close - bar.low))
            true_ranges.append(max(ranges))
            previous_close = bar.close
        return round_money(sum(true_ranges) / len(true_ranges))

    def hv_log_return(self, bars: list[PriceBar], annualization: float) -> float:
        if len(bars) < 2:
            raise ValueError("HV requires at least two bars")
        if annualization <= 0:
            raise ValueError("annualization must be positive")
        returns = [
            math.log(current.close / previous.close)
            for previous, current in zip(bars, bars[1:])
        ]
        mean = sum(returns) / len(returns)
        variance = sum((item - mean) ** 2 for item in returns) / len(returns)
        return math.sqrt(variance) * math.sqrt(annualization)

    def price_std(self, bars: list[PriceBar]) -> float:
        if not bars:
            raise ValueError("standard deviation requires at least one bar")
        closes = [bar.close for bar in bars]
        mean = sum(closes) / len(closes)
        variance = sum((item - mean) ** 2 for item in closes) / len(closes)
        return math.sqrt(variance)
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
python -m pytest tests/test_condition_indicators.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add trade_xquant/condition_indicators.py tests/test_condition_indicators.py
git commit -m "Add condition indicator calculations"
```

---

### Task 2: Rule Schema And Hyperparameter Validation

**Files:**
- Modify: `trade_xquant/condition_orders.py`
- Create: `tests/test_condition_rule_schema.py`
- Modify: `tests/test_local_task_file_adapter.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/test_condition_rule_schema.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from trade_xquant.condition_orders import (
    ConditionOrder,
    required_condition_params,
    validate_condition_hyperparameters,
)


def condition(method: str, purpose: str, params: dict) -> ConditionOrder:
    return ConditionOrder(
        condition_id=f"cond-{method}-{purpose}",
        task_id="task-1",
        portfolio_id="prod",
        account_id="acct",
        mode="dry_run",
        symbol="513100.SH",
        purpose=purpose,
        method=method,
        reference_price=1.0,
        params=params,
    )


def test_all_single_instrument_methods_parse() -> None:
    cases = [
        ("static_pct", "stop_loss", {"stop_loss_pct": 0.05}),
        ("static_pct", "take_profit", {"take_profit_pct": 0.10}),
        ("trailing_pct", "stop_loss", {"trail_pct": 0.08}),
        ("trailing_pct", "take_profit", {"trail_pct": 0.08, "activation_profit_pct": 0.12}),
        ("atr_trailing", "stop_loss", {"atr_window": 3, "atr_multiple": 2.0, "bar_interval": "1d"}),
        ("atr_trailing", "take_profit", {"atr_window": 3, "atr_multiple": 2.0, "bar_interval": "1d", "activation_price": 1.2}),
        ("hv_log_trailing", "stop_loss", {"hv_window": 3, "hv_annualization": 252, "lambda": 1.0, "bar_interval": "1d"}),
        ("hv_log_trailing", "take_profit", {"hv_window": 3, "hv_annualization": 252, "lambda": 1.0, "bar_interval": "1d", "activation_profit_pct": 0.1}),
        ("std_trailing", "stop_loss", {"std_window": 3, "std_multiple": 1.5, "bar_interval": "1d"}),
        ("std_trailing", "take_profit", {"std_window": 3, "std_multiple": 1.5, "bar_interval": "1d", "activation_price": 1.2}),
    ]

    for method, purpose, params in cases:
        order = condition(method, purpose, params)
        assert order.method == method
        assert validate_condition_hyperparameters(order) == []


def test_required_condition_params_are_method_and_purpose_specific() -> None:
    order = condition("trailing_pct", "take_profit", {"trail_pct": 0.08})

    assert required_condition_params(order) == {"trail_pct", "activation_profit_pct|activation_price"}
    assert validate_condition_hyperparameters(order) == ["activation_profit_pct|activation_price"]


def test_unsupported_method_fails_validation() -> None:
    with pytest.raises(ValidationError):
        condition("unsupported", "stop_loss", {})
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_condition_rule_schema.py -q
```

Expected:

```text
ImportError: cannot import name 'required_condition_params'
```

- [ ] **Step 3: Extend condition methods and validators**

Modify `trade_xquant/condition_orders.py`:

```python
ConditionMethod = Literal[
    "static_pct",
    "trailing_pct",
    "atr_trailing",
    "hv_log_trailing",
    "std_trailing",
]
```

Add helpers near `_param`:

```python
def required_condition_params(order: ConditionOrder) -> set[str]:
    if order.method == "static_pct" and order.purpose == "stop_loss":
        return {"stop_loss_pct"}
    if order.method == "static_pct" and order.purpose == "take_profit":
        return {"take_profit_pct"}
    if order.method == "trailing_pct":
        required = {"trail_pct"}
    elif order.method == "atr_trailing":
        required = {"atr_window", "atr_multiple", "bar_interval"}
    elif order.method == "hv_log_trailing":
        required = {"hv_window", "hv_annualization", "lambda", "bar_interval"}
    elif order.method == "std_trailing":
        required = {"std_window", "std_multiple", "bar_interval"}
    else:
        return set()
    if order.purpose == "take_profit":
        required.add("activation_profit_pct|activation_price")
    return required


def validate_condition_hyperparameters(order: ConditionOrder) -> list[str]:
    missing: list[str] = []
    for key in sorted(required_condition_params(order)):
        if "|" in key:
            alternatives = key.split("|")
            if not any(order.params.get(name) is not None for name in alternatives):
                missing.append(key)
        elif order.params.get(key) is None:
            missing.append(key)
    if order.scope != "instrument":
        missing.append("scope:instrument")
    if order.reference_price is None:
        missing.append("reference_price")
    return missing
```

- [ ] **Step 4: Run schema tests**

Run:

```bash
python -m pytest tests/test_condition_rule_schema.py tests/test_local_task_file_adapter.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add trade_xquant/condition_orders.py tests/test_condition_rule_schema.py tests/test_local_task_file_adapter.py
git commit -m "Extend condition rule schema"
```

---

### Task 3: SQLite Market State And Trigger Audit Storage

**Files:**
- Modify: `trade_xquant/storage.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_condition_orders.py`

- [ ] **Step 1: Write failing storage tests**

Append to `tests/test_storage.py`:

```python
def test_condition_market_state_roundtrip(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    storage.record_condition_market_state(
        condition_id="cond-1",
        symbol="513100.SH",
        latest_price=1.23,
        high_water_price=1.4,
        trigger_price=1.18,
        activated=True,
        activated_at="2026-06-03T10:00:00+08:00",
        atr_value=0.03,
        hv_value=None,
        std_value=None,
        computed_at="2026-06-03T10:30:00+08:00",
        market_data_source="mock",
        state={"reason": "test"},
    )

    state = storage.get_condition_market_state("cond-1")

    assert state["symbol"] == "513100.SH"
    assert state["latest_price"] == 1.23
    assert state["activated"] is True
    assert state["state"]["reason"] == "test"


def test_condition_trigger_audit_roundtrip_and_report_status(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()

    storage.record_condition_trigger_audit(
        condition_id="cond-1",
        source_task_id="task-1",
        condition_task_id="condition:cond-1",
        symbol="513100.SH",
        purpose="take_profit",
        method="static_pct",
        rule={"params": {"take_profit_pct": 0.1}},
        market_state={"latest_price": 1.1},
        trigger={"reason": "latest_price >= trigger_price"},
        execution_result={"status": "dry_run_success"},
    )
    storage.update_condition_audit_report_status("condition:cond-1", "failed", "http 409")

    audit = storage.get_condition_trigger_audit("condition:cond-1")

    assert audit["source_task_id"] == "task-1"
    assert audit["rule"]["params"]["take_profit_pct"] == 0.1
    assert audit["xquant_report_status"] == "failed"
    assert audit["xquant_report_error"] == "http 409"
```

- [ ] **Step 2: Run storage tests to verify failure**

Run:

```bash
python -m pytest tests/test_storage.py::test_condition_market_state_roundtrip tests/test_storage.py::test_condition_trigger_audit_roundtrip_and_report_status -q
```

Expected:

```text
AttributeError: 'Storage' object has no attribute 'record_condition_market_state'
```

- [ ] **Step 3: Add tables and storage methods**

Modify the `initialize()` SQL in `trade_xquant/storage.py` by adding:

```sql
CREATE TABLE IF NOT EXISTS condition_market_states (
    condition_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    latest_price REAL,
    high_water_price REAL,
    trigger_price REAL,
    activated INTEGER NOT NULL,
    activated_at TEXT,
    atr_value REAL,
    hv_value REAL,
    std_value REAL,
    computed_at TEXT NOT NULL,
    market_data_source TEXT NOT NULL,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS condition_trigger_audits (
    condition_task_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    source_task_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    purpose TEXT NOT NULL,
    method TEXT NOT NULL,
    rule_json TEXT NOT NULL,
    market_state_json TEXT NOT NULL,
    trigger_json TEXT NOT NULL,
    execution_result_json TEXT NOT NULL,
    xquant_report_status TEXT NOT NULL,
    xquant_report_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Add methods to `Storage`:

```python
    def record_condition_market_state(
        self,
        *,
        condition_id: str,
        symbol: str,
        latest_price: float | None,
        high_water_price: float | None,
        trigger_price: float | None,
        activated: bool,
        activated_at: str | None,
        atr_value: float | None,
        hv_value: float | None,
        std_value: float | None,
        computed_at: str,
        market_data_source: str,
        state: dict[str, Any],
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO condition_market_states (
                    condition_id, symbol, latest_price, high_water_price,
                    trigger_price, activated, activated_at, atr_value,
                    hv_value, std_value, computed_at, market_data_source,
                    state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(condition_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    latest_price=excluded.latest_price,
                    high_water_price=excluded.high_water_price,
                    trigger_price=excluded.trigger_price,
                    activated=excluded.activated,
                    activated_at=excluded.activated_at,
                    atr_value=excluded.atr_value,
                    hv_value=excluded.hv_value,
                    std_value=excluded.std_value,
                    computed_at=excluded.computed_at,
                    market_data_source=excluded.market_data_source,
                    state_json=excluded.state_json
                """,
                (
                    condition_id,
                    symbol,
                    latest_price,
                    high_water_price,
                    trigger_price,
                    1 if activated else 0,
                    activated_at,
                    atr_value,
                    hv_value,
                    std_value,
                    computed_at,
                    market_data_source,
                    json.dumps(state, ensure_ascii=False),
                ),
            )

    def get_condition_market_state(self, condition_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM condition_market_states WHERE condition_id=?",
                (condition_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["activated"] = bool(result["activated"])
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def record_condition_trigger_audit(
        self,
        *,
        condition_id: str,
        source_task_id: str,
        condition_task_id: str,
        symbol: str,
        purpose: str,
        method: str,
        rule: dict[str, Any],
        market_state: dict[str, Any],
        trigger: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO condition_trigger_audits (
                    condition_task_id, condition_id, source_task_id, symbol,
                    purpose, method, rule_json, market_state_json,
                    trigger_json, execution_result_json, xquant_report_status,
                    xquant_report_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(condition_task_id) DO UPDATE SET
                    execution_result_json=excluded.execution_result_json,
                    updated_at=excluded.updated_at
                """,
                (
                    condition_task_id,
                    condition_id,
                    source_task_id,
                    symbol,
                    purpose,
                    method,
                    json.dumps(rule, ensure_ascii=False),
                    json.dumps(market_state, ensure_ascii=False),
                    json.dumps(trigger, ensure_ascii=False),
                    json.dumps(execution_result, ensure_ascii=False),
                    "pending",
                    None,
                    now,
                    now,
                ),
            )

    def update_condition_audit_report_status(
        self,
        condition_task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE condition_trigger_audits
                SET xquant_report_status=?, xquant_report_error=?, updated_at=?
                WHERE condition_task_id=?
                """,
                (status, error, utc_now(), condition_task_id),
            )

    def get_condition_trigger_audit(self, condition_task_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM condition_trigger_audits WHERE condition_task_id=?",
                (condition_task_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["rule"] = json.loads(result.pop("rule_json"))
        result["market_state"] = json.loads(result.pop("market_state_json"))
        result["trigger"] = json.loads(result.pop("trigger_json"))
        result["execution_result"] = json.loads(result.pop("execution_result_json"))
        return result
```

- [ ] **Step 4: Run storage tests**

Run:

```bash
python -m pytest tests/test_storage.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add trade_xquant/storage.py tests/test_storage.py tests/test_condition_orders.py
git commit -m "Persist condition market state and audits"
```

---

### Task 4: Broker Market Data Boundary

**Files:**
- Modify: `trade_xquant/broker.py`
- Modify: `trade_xquant/mock_qmt_adapter.py`
- Modify: `trade_xquant/qmt_adapter.py`
- Modify: `tests/test_mock_qmt_adapter.py`

- [ ] **Step 1: Write failing mock broker bar tests**

Append to `tests/test_mock_qmt_adapter.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from trade_xquant.condition_indicators import PriceBar


def test_mock_qmt_returns_price_bars() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    bars = [
        PriceBar(symbol="513100.SH", high=1.1, low=1.0, close=1.05, timestamp=datetime(2026, 6, 1, tzinfo=tz)),
        PriceBar(symbol="513100.SH", high=1.2, low=1.05, close=1.18, timestamp=datetime(2026, 6, 2, tzinfo=tz)),
    ]
    adapter = MockBrokerAdapter(
        account_id="acct",
        total_asset=100_000,
        cash=100_000,
        prices={"513100.SH": 1.18},
        price_bars={"513100.SH": {"1d": bars}},
    )

    result = adapter.get_price_bars("513100.SS", interval="1d", window=2)

    assert [bar.close for bar in result] == [1.05, 1.18]
    assert result[0].symbol == "513100.SH"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python -m pytest tests/test_mock_qmt_adapter.py::test_mock_qmt_returns_price_bars -q
```

Expected:

```text
TypeError: MockBrokerAdapter.__init__() got an unexpected keyword argument 'price_bars'
```

- [ ] **Step 3: Add broker protocol and mock implementation**

Modify `trade_xquant/broker.py`:

```python
from trade_xquant.condition_indicators import PriceBar


class BrokerAdapter(Protocol):
    def connect(self) -> None: ...
    def get_account_snapshot(self) -> AccountSnapshot: ...
    def get_positions(self) -> list[Position]: ...
    def get_prices(self, symbols: list[str]) -> dict[str, float]: ...
    def get_price_bars(self, symbol: str, interval: str, window: int) -> list[PriceBar]: ...
    def place_order(self, order: PlannedOrder) -> Any: ...
    def cancel_order(self, order_id: str) -> Any: ...
```

Modify `MockBrokerAdapter.__init__`:

```python
        price_bars: dict[str, dict[str, list[PriceBar]]] | None = None,
```

Inside `__init__`:

```python
        self.price_bars = {
            normalize_symbol(symbol): intervals
            for symbol, intervals in (price_bars or {}).items()
        }
```

Add method:

```python
    def get_price_bars(self, symbol: str, interval: str, window: int) -> list[PriceBar]:
        normalized = normalize_symbol(symbol)
        bars = self.price_bars.get(normalized, {}).get(interval)
        if bars is None:
            raise RuntimeError(f"mock bars missing for {normalized} interval {interval}")
        if window <= 0:
            raise ValueError("window must be positive")
        if len(bars) < window:
            raise RuntimeError(f"mock bars insufficient for {normalized}: need {window}, got {len(bars)}")
        return bars[-window:]
```

Modify `QmtAdapter` with a clear unsupported method:

```python
    def get_price_bars(self, symbol: str, interval: str, window: int):
        raise NotImplementedError("QMT historical price bars are not wired yet")
```

- [ ] **Step 4: Run tests**

Run:

```bash
python -m pytest tests/test_mock_qmt_adapter.py tests/test_qmt_adapter.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add trade_xquant/broker.py trade_xquant/mock_qmt_adapter.py trade_xquant/qmt_adapter.py tests/test_mock_qmt_adapter.py
git commit -m "Add condition market data boundary"
```

---

### Task 5: Condition Engine Rule Evaluation

**Files:**
- Modify: `trade_xquant/condition_orders.py`
- Modify: `tests/test_condition_orders.py`

- [ ] **Step 1: Write failing rule evaluation tests**

Append to `tests/test_condition_orders.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from trade_xquant.condition_indicators import PriceBar


class BarProvider:
    def __init__(self, prices: dict[str, float], bars: list[PriceBar]) -> None:
        self.prices = prices
        self.bars = bars

    def get_price_bars(self, symbol: str, interval: str, window: int) -> list[PriceBar]:
        return self.bars[-window:]


def price_bars() -> list[PriceBar]:
    tz = ZoneInfo("Asia/Shanghai")
    return [
        PriceBar(symbol="513100.SH", high=1.1, low=1.0, close=1.05, timestamp=datetime(2026, 6, 1, tzinfo=tz)),
        PriceBar(symbol="513100.SH", high=1.2, low=1.05, close=1.18, timestamp=datetime(2026, 6, 2, tzinfo=tz)),
        PriceBar(symbol="513100.SH", high=1.3, low=1.15, close=1.24, timestamp=datetime(2026, 6, 3, tzinfo=tz)),
    ]


def account() -> AccountSnapshot:
    return AccountSnapshot(account_id="acct", total_asset=100_000, cash=90_000)


def position() -> Position:
    return Position(symbol="513100.SH", quantity=1000, sellable_quantity=1000, market_value=1200, cost_price=1.0)


def test_atr_trailing_stop_loss_triggers(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders([
        ConditionOrder(
            condition_id="cond-atr-sl",
            task_id="task-1",
            portfolio_id="prod",
            account_id="acct",
            symbol="513100.SH",
            purpose="stop_loss",
            method="atr_trailing",
            reference_price=1.0,
            high_water_price=1.30,
            params={"atr_window": 3, "atr_multiple": 1.0, "bar_interval": "1d"},
        )
    ])
    engine = ConditionEngine(storage, market_data=BarProvider({"513100.SH": 1.12}, price_bars()))

    plans = engine.evaluate(account(), [position()], {"513100.SH": 1.12}, now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert [plan.order.condition_id for plan in plans] == ["cond-atr-sl"]
    state = storage.get_condition_market_state("cond-atr-sl")
    assert state["atr_value"] is not None
    assert state["trigger_price"] is not None


def test_trailing_take_profit_requires_activation_before_trigger(tmp_path) -> None:
    storage = Storage(tmp_path / "audit.db")
    storage.initialize()
    storage.upsert_condition_orders([
        ConditionOrder(
            condition_id="cond-trailing-tp",
            task_id="task-1",
            portfolio_id="prod",
            account_id="acct",
            symbol="513100.SH",
            purpose="take_profit",
            method="trailing_pct",
            reference_price=1.0,
            high_water_price=1.0,
            params={"trail_pct": 0.08, "activation_profit_pct": 0.2},
        )
    ])
    engine = ConditionEngine(storage)

    inactive = engine.evaluate(account(), [position()], {"513100.SH": 1.1}, now=datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    active = storage.get_condition_market_state("cond-trailing-tp")

    assert inactive == []
    assert active["activated"] is False

    engine.evaluate(account(), [position()], {"513100.SH": 1.25}, now=datetime(2026, 6, 3, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai")))
    triggered = engine.evaluate(account(), [position()], {"513100.SH": 1.14}, now=datetime(2026, 6, 3, 10, 2, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert [plan.order.condition_id for plan in triggered] == ["cond-trailing-tp"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_condition_orders.py::test_atr_trailing_stop_loss_triggers tests/test_condition_orders.py::test_trailing_take_profit_requires_activation_before_trigger -q
```

Expected:

```text
TypeError: ConditionEngine.__init__() got an unexpected keyword argument 'market_data'
```

- [ ] **Step 3: Implement market-state evaluation**

Modify `ConditionEngine.__init__`:

```python
    def __init__(self, storage, market_data=None) -> None:
        self.storage = storage
        self.market_data = market_data
        self.indicators = ConditionIndicatorEngine()
```

Add imports:

```python
import math
from trade_xquant.condition_indicators import ConditionIndicatorEngine
```

Add helpers:

```python
    def _latest_market_state(self, order: ConditionOrder) -> dict[str, Any]:
        return self.storage.get_condition_market_state(order.condition_id) or {}

    def _activation_state(self, order: ConditionOrder, latest_price: float, now: datetime) -> tuple[bool, str | None]:
        stored = self._latest_market_state(order)
        if stored.get("activated"):
            return True, stored.get("activated_at")
        if order.purpose != "take_profit" or order.method == "static_pct":
            return True, None
        activation_price = order.params.get("activation_price")
        if activation_price is None and order.params.get("activation_profit_pct") is not None:
            if order.reference_price is None:
                raise ValueError(f"condition {order.condition_id} missing reference_price")
            activation_price = order.reference_price * (1 + float(order.params["activation_profit_pct"]))
        if activation_price is None:
            raise ValueError(f"condition {order.condition_id} missing activation_profit_pct|activation_price")
        if latest_price >= float(activation_price):
            return True, now.isoformat()
        return False, None

    def _indicator_values(self, order: ConditionOrder) -> tuple[float | None, float | None, float | None]:
        if order.method not in {"atr_trailing", "hv_log_trailing", "std_trailing"}:
            return None, None, None
        if self.market_data is None:
            raise ValueError(f"condition {order.condition_id} missing market data provider")
        interval = str(order.params["bar_interval"])
        if order.method == "atr_trailing":
            bars = self.market_data.get_price_bars(order.symbol, interval, int(order.params["atr_window"]))
            return self.indicators.atr(bars), None, None
        if order.method == "hv_log_trailing":
            bars = self.market_data.get_price_bars(order.symbol, interval, int(order.params["hv_window"]))
            return None, self.indicators.hv_log_return(bars, float(order.params["hv_annualization"])), None
        bars = self.market_data.get_price_bars(order.symbol, interval, int(order.params["std_window"]))
        return None, None, self.indicators.price_std(bars)
```

Replace `_with_market_state` with logic that:

- Calls `validate_condition_hyperparameters`.
- Uses stored `high_water_price` if present.
- Updates high-water for all trailing methods.
- Applies activation for take-profit trailing methods.
- Computes trigger price by method.
- Writes `record_condition_market_state`.

Keep old static behavior compatible.

- [ ] **Step 4: Run condition tests**

Run:

```bash
python -m pytest tests/test_condition_orders.py tests/test_condition_rule_schema.py tests/test_condition_indicators.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add trade_xquant/condition_orders.py tests/test_condition_orders.py
git commit -m "Evaluate all single-instrument condition rules"
```

---

### Task 6: Gateway Execution Audit And Xquant Reporting

**Files:**
- Modify: `trade_xquant/daemon.py`
- Modify: `trade_xquant/xquant_adapter.py`
- Modify: `tests/test_xquant_adapter.py`
- Modify: `tests/test_gateway_condition_orders.py`

- [ ] **Step 1: Write failing Xquant adapter report test**

Append to `tests/test_xquant_adapter.py`:

```python
def test_report_condition_result_posts_audit_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", client=client)

    adapter.report_condition_result(
        "task-1",
        "cond-1",
        {
            "condition_task_id": "condition:cond-1",
            "status": "submitted",
            "trigger": {"reason": "latest_price <= trigger_price"},
        },
    )

    assert seen["path"] == "/api/v1/trading-gateway/tasks/task-1/condition-results"
    assert '"condition_id":"cond-1"' in str(seen["payload"])
```

- [ ] **Step 2: Run adapter test to verify failure**

Run:

```bash
python -m pytest tests/test_xquant_adapter.py::test_report_condition_result_posts_audit_payload -q
```

Expected:

```text
AttributeError: 'XquantAdapter' object has no attribute 'report_condition_result'
```

- [ ] **Step 3: Implement Xquant report method**

Add to `XquantAdapter`:

```python
    def report_condition_result(
        self,
        source_task_id: str,
        condition_id: str,
        payload: dict[str, Any],
    ) -> None:
        body = {**payload, "source_task_id": source_task_id, "condition_id": condition_id}
        response = self.client.post(
            self._url(f"/trading-gateway/tasks/{source_task_id}/condition-results"),
            json=body,
            headers=self._headers(),
        )
        self._handle(response)
```

- [ ] **Step 4: Write gateway audit integration test**

Append to `tests/test_gateway_condition_orders.py`:

```python
class AuditXquant:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.payloads: list[tuple[str, str, dict]] = []

    def report_condition_result(self, source_task_id: str, condition_id: str, payload: dict) -> None:
        self.payloads.append((source_task_id, condition_id, payload))
        if self.fail:
            raise RuntimeError("xquant audit failed")


def test_gateway_records_and_reports_condition_audit(tmp_path) -> None:
    service = GatewayService(settings_for(tmp_path))
    service.storage.initialize()
    service.storage.upsert_condition_orders([
        ConditionOrder(
            condition_id="cond-audit",
            task_id="task-1",
            portfolio_id="prod",
            account_id="acct",
            mode="dry_run",
            symbol="513100.SH",
            purpose="take_profit",
            method="static_pct",
            reference_price=1.0,
            params={"take_profit_pct": 0.1},
            action=ConditionAction(type="sell_pct", pct=1.0),
        )
    ])
    broker = PositionBroker()
    service.qmt = broker  # type: ignore[assignment]
    audit = AuditXquant()
    service.xquant = audit  # type: ignore[assignment]

    service.condition_poll_once()

    assert audit.payloads[0][0] == "task-1"
    assert audit.payloads[0][1] == "cond-audit"
    payload = audit.payloads[0][2]
    assert payload["condition_task_id"] == "condition:cond-audit"
    assert payload["rule"]["params"]["take_profit_pct"] == 0.1
    stored = service.storage.get_condition_trigger_audit("condition:cond-audit")
    assert stored["xquant_report_status"] == "success"


def test_gateway_xquant_audit_failure_does_not_repeat_trade(tmp_path) -> None:
    service = GatewayService(settings_for(tmp_path))
    service.storage.initialize()
    service.storage.upsert_condition_orders([
        ConditionOrder(
            condition_id="cond-audit-fail",
            task_id="task-1",
            portfolio_id="prod",
            account_id="acct",
            mode="dry_run",
            symbol="513100.SH",
            purpose="take_profit",
            method="static_pct",
            reference_price=1.0,
            params={"take_profit_pct": 0.1},
            action=ConditionAction(type="sell_pct", pct=1.0),
        )
    ])
    broker = PositionBroker()
    service.qmt = broker  # type: ignore[assignment]
    service.xquant = AuditXquant(fail=True)  # type: ignore[assignment]

    service.condition_poll_once()
    service.condition_poll_once()

    assert len(broker.submitted_orders) == 1
    stored = service.storage.get_condition_trigger_audit("condition:cond-audit-fail")
    assert stored["xquant_report_status"] == "failed"
```

- [ ] **Step 5: Implement gateway audit recording and reporting**

In `GatewayService.condition_poll_once`, after `record_execution_result(result)`:

```python
                audit_payload = self._condition_audit_payload(triggered, result)
                self.storage.record_condition_trigger_audit(
                    condition_id=condition_id,
                    source_task_id=triggered.order.task_id,
                    condition_task_id=triggered.task.task_id,
                    symbol=triggered.order.symbol,
                    purpose=triggered.order.purpose,
                    method=triggered.order.method,
                    rule=audit_payload["rule"],
                    market_state=audit_payload["market_state"],
                    trigger=audit_payload["trigger"],
                    execution_result=result.model_dump(mode="json"),
                )
                try:
                    self.xquant.report_condition_result(
                        triggered.order.task_id,
                        condition_id,
                        audit_payload,
                    )
                    self.storage.update_condition_audit_report_status(triggered.task.task_id, "success")
                except Exception as exc:  # noqa: BLE001
                    logger.exception("failed to report condition result to Xquant")
                    self.storage.update_condition_audit_report_status(
                        triggered.task.task_id,
                        "failed",
                        str(exc),
                    )
                    self.storage.update_condition_order_status(condition_id, "needs_reconcile")
```

Add helper:

```python
    def _condition_audit_payload(self, triggered, result: ExecutionResult) -> dict[str, Any]:
        market_state = self.storage.get_condition_market_state(triggered.order.condition_id) or {}
        trigger = {
            "triggered_at": datetime.now(ZoneInfo(self.settings.risk.timezone)).isoformat(),
            "latest_price": market_state.get("latest_price"),
            "trigger_price": market_state.get("trigger_price"),
            "reason": market_state.get("state", {}).get("trigger_reason"),
        }
        return {
            "condition_task_id": triggered.task.task_id,
            "account_id": triggered.order.account_id,
            "portfolio_id": triggered.order.portfolio_id,
            "symbol": triggered.order.symbol,
            "status": result.status,
            "trigger": trigger,
            "rule": {
                "scope": triggered.order.scope,
                "purpose": triggered.order.purpose,
                "method": triggered.order.method,
                "params": triggered.order.params,
                "action": triggered.order.action.model_dump(mode="json"),
            },
            "market_state": market_state,
            "execution_result": result.model_dump(mode="json"),
        }
```

- [ ] **Step 6: Run gateway audit tests**

Run:

```bash
python -m pytest tests/test_xquant_adapter.py tests/test_gateway_condition_orders.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 7: Commit**

Run:

```bash
git add trade_xquant/daemon.py trade_xquant/xquant_adapter.py tests/test_xquant_adapter.py tests/test_gateway_condition_orders.py
git commit -m "Report condition trigger audits to Xquant"
```

---

### Task 7: Local JSON End-To-End Coverage

**Files:**
- Modify: `tests/test_gateway_condition_orders.py`
- Modify: `docs/local-condition-orders.md`

- [ ] **Step 1: Write end-to-end local JSON test**

Append to `tests/test_gateway_condition_orders.py`:

```python
def test_local_json_can_arm_all_single_instrument_methods(tmp_path) -> None:
    task_file = tmp_path / "tasks.json"
    conditions = [
        {"condition_id": "static-sl", "symbol": "513100.SH", "purpose": "stop_loss", "method": "static_pct", "reference_price": 1.0, "params": {"stop_loss_pct": 0.05}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "static-tp", "symbol": "513100.SH", "purpose": "take_profit", "method": "static_pct", "reference_price": 1.0, "params": {"take_profit_pct": 0.1}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "trail-sl", "symbol": "513100.SH", "purpose": "stop_loss", "method": "trailing_pct", "reference_price": 1.0, "params": {"trail_pct": 0.08}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "trail-tp", "symbol": "513100.SH", "purpose": "take_profit", "method": "trailing_pct", "reference_price": 1.0, "params": {"trail_pct": 0.08, "activation_profit_pct": 0.12}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "atr-sl", "symbol": "513100.SH", "purpose": "stop_loss", "method": "atr_trailing", "reference_price": 1.0, "params": {"atr_window": 3, "atr_multiple": 2.0, "bar_interval": "1d"}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "atr-tp", "symbol": "513100.SH", "purpose": "take_profit", "method": "atr_trailing", "reference_price": 1.0, "params": {"activation_profit_pct": 0.12, "atr_window": 3, "atr_multiple": 2.0, "bar_interval": "1d"}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "hv-sl", "symbol": "513100.SH", "purpose": "stop_loss", "method": "hv_log_trailing", "reference_price": 1.0, "params": {"hv_window": 3, "hv_annualization": 252, "lambda": 1.0, "bar_interval": "1d"}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "hv-tp", "symbol": "513100.SH", "purpose": "take_profit", "method": "hv_log_trailing", "reference_price": 1.0, "params": {"activation_profit_pct": 0.12, "hv_window": 3, "hv_annualization": 252, "lambda": 1.0, "bar_interval": "1d"}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "std-sl", "symbol": "513100.SH", "purpose": "stop_loss", "method": "std_trailing", "reference_price": 1.0, "params": {"std_window": 3, "std_multiple": 1.5, "bar_interval": "1d"}, "action": {"type": "sell_pct", "pct": 1.0}},
        {"condition_id": "std-tp", "symbol": "513100.SH", "purpose": "take_profit", "method": "std_trailing", "reference_price": 1.0, "params": {"activation_profit_pct": 0.12, "std_window": 3, "std_multiple": 1.5, "bar_interval": "1d"}, "action": {"type": "sell_pct", "pct": 1.0}},
    ]
    task_file.write_text(
        json.dumps({
            "tasks": [{
                "task_id": "task-all-conditions",
                "portfolio_id": "prod",
                "account_id": "acct",
                "mode": "dry_run",
                "created_at": "2026-06-03T09:35:00+08:00",
                "expires_at": None,
                "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                "constraints": {"condition_orders": conditions},
            }]
        }),
        encoding="utf-8",
    )
    service = GatewayService(settings_for(tmp_path, task_file))

    service.poll_once(force_dry_run=True)

    active = service.storage.list_active_condition_orders()
    assert sorted(order.condition_id for order in active) == sorted(item["condition_id"] for item in conditions)
```

- [ ] **Step 2: Run test**

Run:

```bash
python -m pytest tests/test_gateway_condition_orders.py::test_local_json_can_arm_all_single_instrument_methods -q
```

Expected:

```text
1 passed
```

- [ ] **Step 3: Update local JSON docs**

Modify `docs/local-condition-orders.md`:

```markdown
## Supported Single-Instrument Rules

Local JSON may mock every Xquant single-instrument sell-side condition rule:

- `static_pct` with `purpose: "stop_loss"`
- `static_pct` with `purpose: "take_profit"`
- `trailing_pct` with `purpose: "stop_loss"`
- `trailing_pct` with `purpose: "take_profit"`
- `atr_trailing` with `purpose: "stop_loss"`
- `atr_trailing` with `purpose: "take_profit"`
- `hv_log_trailing` with `purpose: "stop_loss"`
- `hv_log_trailing` with `purpose: "take_profit"`
- `std_trailing` with `purpose: "stop_loss"`
- `std_trailing` with `purpose: "take_profit"`

The JSON file mocks Xquant task payloads only. It does not store high-water
state, activation state, indicator snapshots, trigger evidence, or execution
audits. Those are stored in SQLite.
```

- [ ] **Step 4: Commit**

Run:

```bash
git add tests/test_gateway_condition_orders.py docs/local-condition-orders.md
git commit -m "Cover local JSON condition rule payloads"
```

---

### Task 8: API Contract And Rule Docs

**Files:**
- Modify: `docs/xquant-api-contract.md`
- Modify: `docs/conditional-stop-take-profit-rules.md`

- [ ] **Step 1: Update Xquant API contract**

Append to `docs/xquant-api-contract.md`:

```markdown
## Report Condition Result

`POST /api/v1/trading-gateway/tasks/{source_task_id}/condition-results`

The gateway calls this endpoint after a condition rule triggers and the local
condition execution path has produced an `ExecutionResult`.

Payload:

```json
{
  "source_task_id": "task-1",
  "condition_id": "cond-513100-atr-tp",
  "condition_task_id": "condition:cond-513100-atr-tp",
  "account_id": "acct",
  "portfolio_id": "prod",
  "symbol": "513100.SH",
  "status": "submitted",
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
    "reference_price": 1.0,
    "high_water_price": 1.4,
    "trigger_price": 1.18,
    "activated": true,
    "atr_value": 0.03,
    "hv_value": null,
    "std_value": null,
    "computed_at": "2026-06-03T10:30:00+08:00",
    "market_data_source": "qmt",
    "state_source": "local_sqlite"
  },
  "execution_result": {}
}
```

Rules:

- `condition_task_id` is idempotent.
- A failed condition-result report must not cause repeated trading.
- Xquant should accept repeated audit reports for the same
  `condition_task_id` idempotently.
```

- [ ] **Step 2: Update rule docs**

Add this sentence to `docs/conditional-stop-take-profit-rules.md`
under implementation guidance:

```markdown
Condition-triggered execution must report the triggering rule, the Xquant
hyperparameter snapshot, the gateway market-derived state snapshot, and the
execution result back to Xquant for audit.
```

- [ ] **Step 3: Run doc format check**

Run:

```bash
git diff --check
```

Expected:

```text
no output
```

- [ ] **Step 4: Commit**

Run:

```bash
git add docs/xquant-api-contract.md docs/conditional-stop-take-profit-rules.md
git commit -m "Document condition audit API contract"
```

---

### Task 9: Full Verification And Cleanup

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
python -m pytest \
  tests/test_condition_indicators.py \
  tests/test_condition_rule_schema.py \
  tests/test_condition_orders.py \
  tests/test_gateway_condition_orders.py \
  tests/test_local_task_file_adapter.py \
  tests/test_mock_qmt_adapter.py \
  tests/test_xquant_adapter.py \
  tests/test_storage.py \
  -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 2: Run full test suite**

Run:

```bash
python -m pytest
```

Expected:

```text
all tests pass
```

- [ ] **Step 3: Run repository checks**

Run:

```bash
git diff --check
rg -n "<<<<<<<|=======|>>>>>>>" docs trade_xquant tests || true
```

Expected:

```text
git diff --check produces no output
conflict marker scan produces no output
```

- [ ] **Step 4: Inspect final diff and status**

Run:

```bash
git status --short --branch
git log --oneline --decorate -10
```

Expected:

```text
working tree clean after all commits
recent commits show this feature's implementation commits on feature/conditional-stop-take-profit-orders
```

- [ ] **Step 5: Report completion evidence**

Final report must include:

- Commit hashes created during implementation.
- Full test command and pass count.
- Any pytest warnings.
- Confirmation that audit-report failure does not repeat trades.
- Confirmation that all numeric trading thresholds are task-supplied.
