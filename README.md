# trade-xquant

Windows QMT / MiniQMT trading gateway for Xquant target-weight tasks.

The MVP runs on the Windows machine where 国金 QMT is already logged in.
It pulls target-weight tasks from Xquant, builds an auditable order plan,
checks risk gates, and sends orders through `xtquant` only when real order
mode is explicitly enabled twice.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
python -m pip install xtquant
```

On Windows, install `xtquant` in the same Python environment used to run
the gateway. If PyPI is unavailable, install the SDK from the QMT package
as described in `docs/qmt-miniqmt-setup-and-hello-validation.md`.

## Configure

```bash
copy config.example.yaml config.yaml
notepad config.yaml
```

Important defaults:

- `runtime.allow_real_order` is `false`.
- `runtime.dry_run_default` is `true`.
- `xquant.product_code` is `null`, so the formal task API is used.
- `xquant.api_token` can be supplied by `XQUANT_API_TOKEN`.
- `trade-xquant login` stores JWT at `xquant-token.json` beside config.
- Do not commit `config.yaml`, `.env`, SQLite DBs, or logs.

## Run

```bash
trade-xquant doctor --config config.yaml
trade-xquant login --config config.yaml --phone replace-with-phone --send-otp
trade-xquant register-account --config config.yaml
trade-xquant heartbeat --config config.yaml
trade-xquant check-qmt --config config.yaml
trade-xquant dry-run --config config.yaml --task-id rebalance_20260527_001
trade-xquant mock-run --config config.yaml --task-id rebalance_20260527_001
trade-xquant poll-once --config config.yaml
trade-xquant daemon --config config.yaml
trade-xquant show-status --config config.yaml
```

If the Xquant server exposes OTP in test mode, the command prints it.
Otherwise enter the OTP received by SMS or email when prompted.

The formal runtime pulls:

```text
GET /api/v1/trading-gateway/tasks
```

Set `xquant.product_code` only when using the temporary latest-signal
fallback endpoint.

To enable real orders, both gates are required:

```bash
# config.yaml
runtime:
  allow_real_order: true
```

```bash
set TRADE_XQUANT_ENABLE_REAL_ORDER=1
trade-xquant poll-once --config config.yaml
```

## Safety

Real order mode is blocked unless:

- The task mode is `real`.
- `runtime.allow_real_order` is `true`.
- `TRADE_XQUANT_ENABLE_REAL_ORDER=1`.
- The current time is in the A-share trading session.
- The task is not expired and not already terminal locally.
- Account ID, weights, prices, order amounts, and turnover pass risk checks.

## Test

```bash
python -m pytest tests
```

Current coverage focuses on portfolio order planning, 100-share lots,
cash buffer, min order amount, turnover limits, task idempotency, real
order gates, Xquant response parsing, and QMT adapter event normalization.

## Docs

- `docs/architecture.md`
- `docs/configuration.md`
- `docs/operations.md`
- `docs/xquant-api-contract.md`
- `docs/qmt-runtime-notes.md`
