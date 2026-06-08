from __future__ import annotations

import time
from typing import Any, Callable

from trade_xquant.broker import QmtGatewayEvent
from trade_xquant.condition_indicators import PriceBar
from trade_xquant.config import QmtConfig
from trade_xquant.models import AccountSnapshot, PlannedOrder, Position


class QmtAdapter:
    def __init__(
        self,
        config: QmtConfig,
        event_handler: Callable[[QmtGatewayEvent], None] | None = None,
    ) -> None:
        self.config = config
        self.event_handler = event_handler
        self.trader: Any | None = None
        self.account: Any | None = None
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        trader_cls, callback_base, account_cls = load_xtquant()
        session_id = self.config.session_id or generate_session_id()
        trader = trader_cls(self.config.userdata_mini_path, session_id)
        if hasattr(trader, "register_callback"):
            trader.register_callback(make_callback(callback_base, self._emit_event))
        trader.start()
        connect_result = trader.connect()
        if connect_result != 0:
            raise ConnectionError(
                "QMT connect failed. connect() must return 0. "
                "Confirm MiniQMT is logged in, userdata_mini path is correct, "
                "and 独立交易 is checked."
            )
        account = account_cls(self.config.account_id)
        self.trader = trader
        self.account = account
        subscribe_result = trader.subscribe(account)
        if subscribe_result != 0:
            diagnostics = self._collect_account_diagnostics()
            raise ConnectionError(
                "QMT subscribe failed. subscribe() must return 0. "
                f"subscribe_result={subscribe_result}; "
                f"configured_account_id={self.config.account_id}; "
                f"account_diagnostics={diagnostics}. "
                "Confirm the configured account_id matches the QMT logged-in "
                "stock account, QMT trade login is active, and strategy trading "
                "permission is enabled."
            )
        self._connected = True

    def get_account_snapshot(self) -> AccountSnapshot:
        self._ensure_connected()
        asset = self.trader.query_stock_asset(self.account)
        if asset is None:
            raise RuntimeError("query_stock_asset returned None")
        return AccountSnapshot(
            account_id=self.config.account_id,
            total_asset=float(getattr(asset, "total_asset", 0) or 0),
            cash=float(getattr(asset, "cash", 0) or 0),
            market_value=float(getattr(asset, "market_value", 0) or 0),
            frozen_cash=float(getattr(asset, "frozen_cash", 0) or 0),
        )

    def get_positions(self) -> list[Position]:
        self._ensure_connected()
        rows = self.trader.query_stock_positions(self.account) or []
        positions: list[Position] = []
        for row in rows:
            symbol = getattr(row, "stock_code", None) or getattr(row, "m_strInstrumentID", None)
            if not symbol:
                continue
            quantity = int(getattr(row, "volume", getattr(row, "m_nVolume", 0)) or 0)
            sellable = int(getattr(row, "can_use_volume", getattr(row, "m_nCanUseVolume", quantity)) or 0)
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=quantity,
                    sellable_quantity=sellable,
                    market_value=float(getattr(row, "market_value", 0) or 0),
                    cost_price=_optional_float(getattr(row, "open_price", None)),
                )
            )
        return positions

    def get_orders(self) -> list[Any]:
        self._ensure_connected()
        return list(self.trader.query_stock_orders(self.account) or [])

    def get_trades(self) -> list[Any]:
        self._ensure_connected()
        return list(self.trader.query_stock_trades(self.account) or [])

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        try:
            from xtquant import xtdata  # type: ignore
        except ImportError as exc:
            raise RuntimeError("xtquant.xtdata is unavailable") from exc
        prices: dict[str, float] = {}
        for symbol in symbols:
            tick = xtdata.get_full_tick([symbol])
            row = tick.get(symbol) if isinstance(tick, dict) else None
            price = row.get("lastPrice") if isinstance(row, dict) else None
            if price is None or float(price) <= 0:
                raise RuntimeError(f"cannot fetch valid price for {symbol}")
            prices[symbol] = float(price)
        return prices

    def get_price_bars(
        self, symbol: str, interval: str, window: int
    ) -> list[PriceBar]:
        raise NotImplementedError("QMT historical price bars are not wired yet")

    def place_order(self, order: PlannedOrder) -> int:
        self._ensure_connected()
        order_id = self.trader.order_stock(
            self.account,
            order.symbol,
            order.qmt_order_type,
            order.quantity,
            order.qmt_price_type,
            order.price,
            self.config.strategy_name,
            order.remark or order.task_id,
        )
        if order_id == -1:
            raise RuntimeError("order_stock returned -1")
        return int(order_id)

    def cancel_order(self, order_id: str) -> Any:
        self._ensure_connected()
        return self.trader.cancel_order_stock(self.account, int(order_id))

    def check_connection(self) -> dict[str, Any]:
        self.connect()
        account = self.get_account_snapshot()
        positions = self.get_positions()
        return {"account": account.model_dump(), "positions_count": len(positions)}

    def _ensure_connected(self) -> None:
        if not self._connected or self.trader is None or self.account is None:
            raise RuntimeError("QMT adapter is not connected")

    def _emit_event(self, event_type: str, obj: Any) -> None:
        event = normalize_qmt_event(event_type, obj)
        if self.event_handler:
            self.event_handler(event)

    def _collect_account_diagnostics(self) -> dict[str, Any]:
        if self.trader is None:
            return {}
        diagnostics: dict[str, Any] = {}
        for method_name in ("query_account_infos", "query_account_status"):
            method = getattr(self.trader, method_name, None)
            if method is None:
                continue
            try:
                value = method()
            except Exception as exc:  # noqa: BLE001 - broker diagnostics
                diagnostics[method_name] = {"error": str(exc)}
                continue
            diagnostics[method_name] = compact_value(value)
        return diagnostics


def generate_session_id() -> int:
    return int(time.time()) % 2_000_000_000


def load_xtquant() -> tuple[type[Any], type[Any], type[Any]]:
    try:
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback  # type: ignore
        from xtquant.xttype import StockAccount  # type: ignore
    except ImportError as exc:
        raise ImportError("xtquant is not installed. Install it in the Windows QMT Python environment.") from exc
    return XtQuantTrader, XtQuantTraderCallback, StockAccount


def make_callback(callback_base: type[Any], emit: Callable[[str, Any], None]) -> Any:
    class Callback(callback_base):  # type: ignore[misc, valid-type]
        def on_connected(self) -> None:
            emit("connected", {})

        def on_disconnected(self) -> None:
            emit("disconnected", {})

        def on_stock_order(self, order: Any) -> None:
            emit("stock_order", order)

        def on_stock_trade(self, trade: Any) -> None:
            emit("stock_trade", trade)

        def on_order_error(self, error: Any) -> None:
            emit("order_error", error)

        def on_cancel_error(self, error: Any) -> None:
            emit("cancel_error", error)

        def on_order_stock_async_response(self, response: Any) -> None:
            emit("order_response", response)

        def on_cancel_order_stock_async_response(self, response: Any) -> None:
            emit("cancel_response", response)

    return Callback()


def normalize_qmt_event(event_type: str, obj: Any) -> QmtGatewayEvent:
    payload = compact_obj(obj)
    order_id = _first_str(payload, "order_id", "order_sysid", "m_strOrderSysID", "m_strOrderRef")
    symbol = _first_str(payload, "stock_code", "m_strInstrumentID")
    return QmtGatewayEvent(event_type=event_type, order_id=order_id, symbol=symbol, payload=payload)


def compact_obj(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj)
    keys = [
        "account_id",
        "symbol",
        "stock_code",
        "order_id",
        "order_sysid",
        "broker_order_id",
        "order_type",
        "side",
        "quantity",
        "order_volume",
        "price_type",
        "price",
        "amount",
        "trade_amount",
        "trade_price",
        "trade_id",
        "status",
        "traded_volume",
        "order_status",
        "error_id",
        "error_msg",
        "msg",
        "remark",
        "order_remark",
        "m_strInstrumentID",
        "m_strOrderSysID",
        "m_strOrderRef",
        "m_nOrderStatus",
        "m_nVolumeTraded",
        "m_dTradeAmount",
        "m_strRemark",
    ]
    return {key: getattr(obj, key) for key in keys if hasattr(obj, key)}


def compact_value(value: Any) -> Any:
    if isinstance(value, list):
        return [compact_obj(item) for item in value]
    if isinstance(value, tuple):
        return [compact_obj(item) for item in value]
    if isinstance(value, dict):
        return value
    return compact_obj(value)


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return str(value)
    return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
