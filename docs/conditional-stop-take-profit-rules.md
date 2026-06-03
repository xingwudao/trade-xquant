# Conditional Stop-Loss And Take-Profit Rules

This document is the development reference for conditional stop-loss and
take-profit rules in trade-xquant.

Source document:
- Feishu: `量化交易止盈止损完整体系手册`
- URL:
  `https://icnzri2zfn9e.feishu.cn/docx/SCz6dluyqoReycxTcVocdK2Ingb`
- Fetched with `lark-cli docs +fetch --api-version v2`
- Source revision read during drafting: `351`

## Non-Negotiable Parameter Rule

Every numeric value from the Feishu document must be treated as a parameter.

trade-xquant must not hardcode:

- Stop-loss percentages.
- Take-profit percentages.
- Trailing drawdown percentages.
- ATR windows.
- ATR multipliers.
- HV windows.
- HV annualization factors.
- HV lambda values.
- Standard-deviation windows.
- Standard-deviation multipliers.
- Minimum reward-risk ratios.
- Activation thresholds.
- Execution percentages.
- Holding-period buckets.
- Account-level drawdown or profit thresholds.

Concrete parameter values must be determined by Xquant-side research,
backtesting, validation, and portfolio configuration. They are then delivered
to the gateway through Xquant trading tasks.

The gateway may validate presence, types, ranges, and consistency. It must not
invent defaults for trading thresholds.

## Development Scope

This document describes the full rule taxonomy from the Feishu document and
normalizes it into implementable contracts.

Current MVP implementation:
- `instrument` scope only.
- Sell-side conditional exits only.
- `static_pct`.
- `trailing_pct`.
- Condition values under `constraints.condition_orders`.

Future implementation may add:
- Portfolio-scope rules.
- ATR trailing rules.
- HV log-return volatility rules.
- Standard-deviation trailing rules.
- Activation gates for trailing take-profit.
- Portfolio-level liquidation or partial de-risk actions.

## Core Concepts

`stop_loss`
- Purpose: control loss or downside risk.
- Fixed stop-loss usually triggers below entry price.
- Trailing stop-loss can move upward after price or account wealth makes a
  new high.
- It can exit at a loss, breakeven, or profit depending on market path.

`take_profit`
- Purpose: realize or lock in profit.
- Fixed take-profit usually triggers above entry price.
- Trailing take-profit should normally be activated only after a configured
  profit gate is reached.
- After activation, it exits on a pullback from the high-water mark.

Important distinction:
- Trailing stop-loss and trailing take-profit can use the same mathematical
  line.
- They are different business rules because they have different purpose,
  activation semantics, reporting, and validation requirements.

## Normalized Symbols

Instrument price variables:
- `P_t`: latest instrument price.
- `P_in`: entry or reference price supplied by Xquant.
- `P_high_t`: high-water price since the condition became active.
- `H_t`: current bar high, used for ATR calculation.
- `L_t`: current bar low, used for ATR calculation.
- `C_t`: current bar close.
- `ATR_t`: average true range computed from a parameterized window.
- `HV_t`: annualized historical volatility of log returns.
- `sigma_P_t`: standard deviation of recent price observations.

Portfolio wealth variables:
- `W_t`: latest account or portfolio wealth.
- `W_0`: reference wealth supplied by Xquant.
- `W_high_t`: high-water wealth since the condition became active.
- `ATR_W_t`: account or portfolio wealth true range.
- `HV_W_t`: annualized historical volatility of wealth log returns.
- `sigma_W_t`: standard deviation of recent wealth observations.

Parameter naming:
- Use explicit names in task payloads.
- Avoid ambiguous names like `N` in the API contract.
- `N` in the Feishu document maps to method-specific parameters such as
  `atr_multiple` or `std_multiple`.

## Condition Order Shape

Condition orders live under:

```text
constraints.condition_orders[]
```

Recommended normalized fields:

```json
{
  "condition_id": "string",
  "scope": "instrument | portfolio",
  "purpose": "stop_loss | take_profit",
  "method": "static_pct | trailing_pct | atr_trailing | hv_log_trailing | std_trailing",
  "symbol": "string for instrument scope",
  "reference_price": "number supplied by Xquant when scope is instrument",
  "reference_wealth": "number supplied by Xquant when scope is portfolio",
  "high_water_price": "optional number supplied by Xquant or maintained locally",
  "high_water_wealth": "optional number supplied by Xquant or maintained locally",
  "params": {},
  "action": {},
  "enabled": true
}
```

Implementation notes:
- JSON examples use strings above only to document shape.
- Real task payloads must use typed values.
- The gateway must reject rules whose required numeric parameters are missing.
- If both Xquant and the gateway maintain high-water state, reconciliation
  rules must be explicit before production use.

## Actions

Supported action concepts:

`sell_pct`
- Sell a parameterized percentage of the sellable position.
- `action.pct` is supplied by Xquant.

`clear`
- Clear sellable position for the target instrument or portfolio scope.
- The exact liquidation behavior must respect QMT lot size, sellable quantity,
  risk gates, and market restrictions.

Future action concepts:
- Portfolio-level partial de-risk.
- Portfolio-level full liquidation.
- Symbol whitelist or blacklist for portfolio actions.

## Instrument Stop-Loss Rules

### Static Percentage Stop-Loss

Purpose:
- Cap loss against an instrument reference price.

Required task data:
- `scope: "instrument"`.
- `purpose: "stop_loss"`.
- `method: "static_pct"`.
- `reference_price`.
- `params.stop_loss_pct`.

Trigger line:

```text
trigger_price = reference_price * (1 - params.stop_loss_pct)
```

Trigger condition:

```text
P_t <= trigger_price
```

Applicable scenarios:
- Short-horizon strategies.
- Mean-reversion or range strategies with predefined invalidation level.
- First implementation because it is simple and auditable.

Risks:
- Can be triggered by short-lived price spikes.
- Does not adapt to current volatility.
- May execute worse than trigger price because of gaps or poor liquidity.

Validation requirements:
- `reference_price` must be positive.
- `params.stop_loss_pct` must be present and valid.
- Xquant must backtest the parameter for the strategy and symbol universe.

### Fixed-Ratio Trailing Stop-Loss

Purpose:
- Move the downside protection line upward when the instrument reaches a new
  high-water price.

Required task data:
- `scope: "instrument"`.
- `purpose: "stop_loss"`.
- `method: "trailing_pct"`.
- `reference_price`.
- `high_water_price`, or permission for gateway-local high-water tracking.
- `params.trail_pct`.

Trigger line:

```text
high_water_price = max(existing_high_water_price, P_t)
trigger_price = high_water_price * (1 - params.trail_pct)
```

Trigger condition:

```text
P_t <= trigger_price
```

Applicable scenarios:
- Trend-following positions.
- Positions that need protection to improve after favorable movement.

Risks:
- Tight parameters may exit during normal pullbacks.
- If activated from entry, it can still close at a loss.
- Local high-water tracking can diverge from Xquant if data sources differ.

Validation requirements:
- Define whether Xquant or gateway owns high-water state.
- Backtest with slippage, fees, gaps, and symbol liquidity.
- Validate repeated trigger prevention and idempotency.

### ATR Trailing Stop-Loss

Purpose:
- Adapt trailing stop distance to recent instrument volatility.

Required task data:
- `scope: "instrument"`.
- `purpose: "stop_loss"`.
- `method: "atr_trailing"`.
- `high_water_price`.
- `params.atr_window`.
- `params.atr_multiple`.
- `params.atr_smoothing`, if smoothing behavior is not fixed by Xquant.

True range:

```text
TR_t = max(
  H_t - L_t,
  abs(H_t - C_{t-1}),
  abs(C_{t-1} - L_t)
)
```

Trigger line:

```text
trigger_price = high_water_price - params.atr_multiple * ATR_t
```

Trigger condition:

```text
P_t <= trigger_price
```

Corrections from source:
- The source used `P_H,t` both as rolling high-water price and current bar
  high. This document uses `P_high_t` for rolling high-water price and `H_t`
  for current bar high.
- Any source value for ATR window is a parameter, not an implementation
  default.

Applicable scenarios:
- Volatility-sensitive trend strategies.
- Mixed symbol universes where fixed percentages are too rigid.

Risks:
- ATR measures volatility, not direction.
- Wide ATR in volatile markets can allow larger losses.
- ATR data quality and bar interval must be aligned with the strategy.

Validation requirements:
- Xquant must define data frequency and ATR calculation method.
- Backtests must include volatility regime changes.
- Stress tests must include gaps and high-volatility reversals.

### HV Log-Return Trailing Stop-Loss

Purpose:
- Use return volatility to produce a percentage-style trailing line that is
  more comparable across different price levels.

Required task data:
- `scope: "instrument"`.
- `purpose: "stop_loss"`.
- `method: "hv_log_trailing"`.
- `high_water_price`.
- `params.hv_window`.
- `params.hv_annualization`.
- `params.lambda`.

Trigger line:

```text
trigger_price = high_water_price * exp(-params.lambda * HV_t)
```

Trigger condition:

```text
P_t <= trigger_price
```

Applicable scenarios:
- Cross-instrument stock or ETF portfolios.
- Strategies that need percentage-consistent volatility adaptation.

Risks:
- Assumes log-return volatility is a useful risk proxy.
- Volatility can lag sudden regime changes.
- Large `lambda` values can create very wide exits.

Validation requirements:
- Xquant must define the HV window, annualization convention, and input bars.
- Parameter stability must be checked by symbol class and market regime.

### Standard-Deviation Trailing Stop-Loss

Purpose:
- Use absolute price-point volatility to define the trailing distance.

Required task data:
- `scope: "instrument"`.
- `purpose: "stop_loss"`.
- `method: "std_trailing"`.
- `high_water_price`.
- `params.std_window`.
- `params.std_multiple`.

Trigger line:

```text
trigger_price = high_water_price - params.std_multiple * sigma_P_t
```

Trigger condition:

```text
P_t <= trigger_price
```

Corrections from source:
- This is not the same as HV log-return volatility.
- It is an absolute price standard-deviation model.

Applicable scenarios:
- Same-instrument strategies.
- Futures or other fixed-point trading contexts.
- Symbol groups with similar price scales.

Risks:
- Not naturally comparable across high-price and low-price instruments.
- Can distort percentage drawdown across symbol universes.

Validation requirements:
- Xquant must only enable it where absolute point movement is meaningful.
- Backtests must check price-scale sensitivity.

## Portfolio Stop-Loss Rules

Portfolio stop-loss rules use account or portfolio wealth instead of a single
instrument price.

### Portfolio Static Stop-Loss

Required task data:
- `scope: "portfolio"`.
- `purpose: "stop_loss"`.
- `method: "static_pct"`.
- `reference_wealth`.
- `params.stop_loss_pct`.

Trigger line:

```text
trigger_wealth = reference_wealth * (1 - params.stop_loss_pct)
```

Trigger condition:

```text
W_t <= trigger_wealth
```

Applicable scenarios:
- Account-level maximum drawdown control.
- Portfolio-level risk cap across many positions.

Risks:
- May force liquidation of otherwise profitable positions.
- Wealth calculation must match Xquant's accounting basis.

Validation requirements:
- Define account wealth source and timestamp.
- Validate behavior for cash, frozen cash, pending orders, and unsettled trades.

### Portfolio Trailing Stop-Loss

Required task data:
- `scope: "portfolio"`.
- `purpose: "stop_loss"`.
- `method: "trailing_pct"`.
- `reference_wealth`.
- `high_water_wealth`, or permission for gateway-local tracking.
- `params.trail_pct`.

Trigger line:

```text
high_water_wealth = max(existing_high_water_wealth, W_t)
trigger_wealth = high_water_wealth * (1 - params.trail_pct)
```

Trigger condition:

```text
W_t <= trigger_wealth
```

Applicable scenarios:
- Lock account-level gains after portfolio wealth makes new highs.
- Trend or rotation portfolios where drawdown should trail wealth.

Risks:
- Can trigger broad liquidation during temporary mark-to-market drawdowns.
- Local portfolio valuation can diverge from Xquant.

Validation requirements:
- Define ownership of high-water wealth state.
- Define action granularity: partial de-risk, full liquidation, or target reset.

### Portfolio ATR, HV, And Standard-Deviation Stop-Loss

Use the same method families as instrument rules, replacing price series with
wealth series:

```text
atr_trailing:
trigger_wealth = high_water_wealth - params.atr_multiple * ATR_W_t

hv_log_trailing:
trigger_wealth = high_water_wealth * exp(-params.lambda * HV_W_t)

std_trailing:
trigger_wealth = high_water_wealth - params.std_multiple * sigma_W_t
```

Required task data follows the same parameter model:
- `params.atr_window`.
- `params.atr_multiple`.
- `params.hv_window`.
- `params.hv_annualization`.
- `params.lambda`.
- `params.std_window`.
- `params.std_multiple`.

Corrections from source:
- The source text says account trailing stop-loss has three subtypes, but then
  lists four. This document treats them as four method families.

## Instrument Take-Profit Rules

### Static Percentage Take-Profit

Purpose:
- Exit or reduce an instrument position after a configured profit target.

Required task data:
- `scope: "instrument"`.
- `purpose: "take_profit"`.
- `method: "static_pct"`.
- `reference_price`.
- `params.take_profit_pct`.

Trigger line:

```text
trigger_price = reference_price * (1 + params.take_profit_pct)
```

Trigger condition:

```text
P_t >= trigger_price
```

Applicable scenarios:
- Range-bound or mean-reversion systems.
- Strategies that need fixed reward-risk planning.

Risks:
- Can exit too early in strong trend markets.
- Fixed targets can reduce upside if the entry signal has momentum edge.

Validation requirements:
- Xquant must validate reward-risk and expected value after costs.
- Backtests must compare fixed take-profit against trailing exits.

### Fixed-Ratio Trailing Take-Profit

Purpose:
- Let profits run, then exit after a configured pullback from high-water price.

Required task data:
- `scope: "instrument"`.
- `purpose: "take_profit"`.
- `method: "trailing_pct"`.
- `reference_price`.
- `high_water_price`, or permission for gateway-local tracking.
- `params.trail_pct`.
- One activation gate:
  `params.activation_profit_pct`, `params.activation_price`, or an explicit
  Xquant-side activation state.

Trigger line after activation:

```text
high_water_price = max(existing_high_water_price, P_t)
trigger_price = high_water_price * (1 - params.trail_pct)
```

Trigger condition after activation:

```text
P_t <= trigger_price
```

Correction from source:
- The formula is mathematically similar to trailing stop-loss. The difference
  is not the formula; it is the purpose and activation semantics.
- Without an activation gate, trailing take-profit degenerates into a trailing
  stop-loss-like rule.

Applicable scenarios:
- Trend-following positions.
- Strategies where capturing larger winners matters more than fixed targets.

Risks:
- Gives back part of the high-water profit.
- Can underperform fixed take-profit in choppy markets.

Validation requirements:
- Xquant must define the activation rule.
- Backtests must report high-water giveback, realized reward-risk, and
  transaction costs.

### ATR, HV, And Standard-Deviation Trailing Take-Profit

These mirror the trailing stop-loss families but use `purpose:
"take_profit"` and require activation semantics.

```text
atr_trailing:
trigger_price = high_water_price - params.atr_multiple * ATR_t

hv_log_trailing:
trigger_price = high_water_price * exp(-params.lambda * HV_t)

std_trailing:
trigger_price = high_water_price - params.std_multiple * sigma_P_t
```

Required task data follows the same parameter model:
- Activation gate.
- High-water state.
- Method-specific volatility parameters.

Applicable scenarios:
- Trend strategies.
- Volatility-adaptive profit protection.

Risks:
- High volatility widens exits and can return more profit before triggering.
- Low volatility tightens exits and may leave a trend early.

Validation requirements:
- Compare against static take-profit and fixed-ratio trailing take-profit.
- Verify behavior across low-volatility, high-volatility, and reversal regimes.

## Portfolio Take-Profit Rules

Portfolio take-profit rules use account or portfolio wealth.

### Portfolio Static Take-Profit

Required task data:
- `scope: "portfolio"`.
- `purpose: "take_profit"`.
- `method: "static_pct"`.
- `reference_wealth`.
- `params.take_profit_pct`.

Trigger line:

```text
trigger_wealth = reference_wealth * (1 + params.take_profit_pct)
```

Trigger condition:

```text
W_t >= trigger_wealth
```

Applicable scenarios:
- Account-level target-return management.
- Strategies with explicit portfolio reward-risk target.

Risks:
- Can force broad de-risking while individual positions still have edge.
- Requires consistent wealth accounting.

### Portfolio Trailing Take-Profit

Required task data:
- `scope: "portfolio"`.
- `purpose: "take_profit"`.
- `method: "trailing_pct"`, `atr_trailing`, `hv_log_trailing`, or
  `std_trailing`.
- `reference_wealth`.
- `high_water_wealth`, or permission for gateway-local tracking.
- Activation gate.
- Method-specific parameters.

Trigger families:

```text
trailing_pct:
trigger_wealth = high_water_wealth * (1 - params.trail_pct)

atr_trailing:
trigger_wealth = high_water_wealth - params.atr_multiple * ATR_W_t

hv_log_trailing:
trigger_wealth = high_water_wealth * exp(-params.lambda * HV_W_t)

std_trailing:
trigger_wealth = high_water_wealth - params.std_multiple * sigma_W_t
```

Applicable scenarios:
- Portfolio-level profit protection.
- Trend or rotation portfolios with account-level high-water management.

Risks:
- Wealth drawdown can be caused by one large position or broad market move;
  action policy must distinguish these cases if needed.
- Portfolio liquidation can create turnover and tax/cost impact.

Validation requirements:
- Validate action policy against account exposure, concentration, and liquidity.
- Verify that account-level rules have priority over instrument-level rules
  when both trigger.

## Reward-Risk Parameterization

Static instrument reward-risk:

```text
reward_risk = params.take_profit_pct / params.stop_loss_pct
```

Static portfolio reward-risk:

```text
portfolio_reward_risk =
  params.take_profit_pct / params.stop_loss_pct
```

Development requirements:
- Minimum reward-risk thresholds are Xquant parameters.
- The gateway must not hardcode any minimum reward-risk value.
- If Xquant sends a `params.min_reward_risk`, the gateway may validate that
  the supplied stop-loss and take-profit parameters satisfy it.

Expected value must be validated by Xquant:

```text
expected_value =
  win_rate * average_profit
  - loss_rate * average_loss
  - transaction_costs
  - slippage_costs
```

Corrections from source:
- Numeric ranges and minimum ratios in the source are research candidates,
  not gateway defaults.
- Strategy horizon labels such as short, medium, and long are Xquant research
  classifications, not gateway constants.

## Applicability Summary

Static percentage rules:
- Best for simple, auditable exits.
- Best for range, mean-reversion, or fixed reward-risk strategies.
- Risk: not volatility adaptive.

Fixed-ratio trailing rules:
- Best for trend-following and high-water protection.
- Risk: sensitive to pullback parameter and activation logic.

ATR trailing rules:
- Best for volatility-adaptive exits using recent price or wealth ranges.
- Risk: ATR does not predict direction and can lag regime changes.

HV log-return rules:
- Best for cross-instrument or cross-account percentage-consistent volatility
  adaptation.
- Risk: depends on return-volatility assumptions and annualization convention.

Standard-deviation trailing rules:
- Best for fixed-point or same-price-scale strategies.
- Risk: not naturally comparable across very different price levels.

Portfolio rules:
- Best for account-level drawdown and profit protection.
- Risk: can override otherwise healthy individual positions and increase
  turnover.

## Execution Risks

Every conditional rule must account for:

- Trigger price is not guaranteed execution price.
- Market orders can slip during volatility.
- Stop-limit-like behavior can avoid bad prices but may not fill.
- A-share limit-up or limit-down states can prevent execution.
- Sellable quantity may be less than position quantity.
- QMT callback and query data can arrive with latency.
- Local gateway state can diverge from Xquant state.
- Repeated triggers must be idempotent.
- Partial fills need reconciliation.
- Account-level actions need concentration and liquidity checks.

## Verification Requirements

Xquant-side validation before production:
- Historical backtest.
- Out-of-sample test.
- Walk-forward or rolling validation.
- Slippage and transaction-cost model.
- Gap and limit-up/limit-down stress test.
- Low-liquidity stress test.
- Volatility-regime split.
- Parameter sensitivity analysis.
- Comparison against no-condition baseline.
- Comparison among static, trailing, ATR, HV, and standard-deviation exits.

Gateway-side validation before production:
- Schema validation for every required parameter.
- Reject unsupported `scope`, `purpose`, or `method`.
- Reject missing reference price, reference wealth, or high-water state.
- Reject impossible action percentages.
- Store every condition state transition.
- Store trigger evidence, including latest price or wealth and trigger line.
- Prevent duplicate submissions for terminal condition orders.
- Verify interaction with existing `RiskControl`.
- Verify real-order gate behavior.

## Source Corrections Applied

The Feishu document was normalized with these corrections:

- `P_SL,fix`, `P_SL,f`, and similar variants are standardized as
  `trigger_price` or method-specific names.
- `k_sl`, `k_sl,f`, and `k_f` are standardized as
  `params.stop_loss_pct`.
- In fixed-ratio trailing stop-loss, the source text says one percentage but
  calculates with another. This document removes the example value and keeps
  only `params.trail_pct`.
- ATR current-bar high/low symbols are separated from rolling high-water
  symbols.
- ATR window and all volatility windows are parameters, not fixed constants.
- The source says account trailing stop-loss has three subtypes but lists
  four. This document treats them as four method families.
- The standard-deviation trailing model is separated from HV log-return
  volatility because it uses absolute point movement.
- The duplicated phrase `机械机械化交易` is normalized to `机械化交易` in the
  conceptual description.
- All parameter recommendation numbers are treated as Xquant research inputs,
  not gateway defaults.

## Implementation Guidance

For trade-xquant:

- Implement only methods explicitly supported by code.
- Unsupported methods must fail closed with a clear error.
- All numeric trading thresholds must come from the task payload.
- The gateway can maintain runtime state, such as high-water price, only when
  the task contract allows it.
- The gateway should report enough evidence for Xquant to audit why a
  condition triggered.

For Xquant:

- Determine parameter values by research and backtesting.
- Send concrete values in each trading task.
- Own portfolio-level policy decisions and account-level priority rules.
- Decide whether high-water state is owned by Xquant or delegated to the
  gateway.
