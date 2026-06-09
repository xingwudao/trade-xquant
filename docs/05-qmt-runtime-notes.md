# QMT Runtime Notes

## Required Runtime State

- QMT is installed on the Windows trading machine.
- QMT is logged in with the intended stock account.
- `独立交易` is checked at login.
- `userdata_mini` path is configured.
- Strategy trading permission is enabled for the account.

The validated path from local notes is:

```text
C:\Apps\QMT\国金证券QMT交易端\userdata_mini
```

## MiniQMT Connection

`QmtAdapter.connect()` uses:

```python
trader = XtQuantTrader(userdata_mini_path, session_id)
trader.start()
connect_result = trader.connect()
subscribe_result = trader.subscribe(account)
```

Both results must be `0`.

Session ID defaults to an auto-generated value based on current time. This
avoids collisions from a fixed session ID. A fixed value can be configured
only when the operator knows no other process is using it.

## Order API

The MVP uses `xtquant.xttrader.order_stock`, not model-trading `passorder`.

QMT PDF notes:

- `passorder` operation `23` is stock buy.
- `passorder` operation `24` is stock sell.
- Price type `5` is latest price.
- Price type `11` is fixed/model price.
- Price type `14` is counterparty price.
- Callback data includes order and deal objects with order sys ID, status,
  traded volume, trade amount, remark, and error fields.

`order_stock` is better for this gateway because the process is an external
Windows service, not a QMT model script.

## Callback Handling

The adapter registers callbacks for:

- `on_connected`
- `on_disconnected`
- `on_stock_order`
- `on_stock_trade`
- `on_order_error`
- `on_cancel_error`
- `on_order_stock_async_response`
- `on_cancel_order_stock_async_response`

Callbacks are normalized to `QmtGatewayEvent` and stored in SQLite
`order_events`.

Do not rely only on `order_id` to determine success. A successful API return
means the request was accepted by the local interface; final state comes from
order queries, trades, and error callbacks.

## Troubleshooting `connect_result=-1`

Checklist:

- Confirm QMT is running and logged in.
- Confirm `独立交易` was checked at login.
- Confirm `userdata_mini` is used, not an unrelated directory.
- Confirm account has strategy trading permission.
- Confirm the Python process and QMT run as the same Windows user.
- Try a different `session_id` or leave it `null` for auto generation.
- Run the original `hello.py check` to isolate SDK/runtime issues.
- Check QMT client popups, permission prompts, and network state.

