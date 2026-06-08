from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from trade_xquant.models import AccountSnapshot, Position, RebalanceTask

MAX_HEARTBEAT_ERROR_LENGTH = 1024
_ERROR_SEPARATOR = "; "
_TRUNCATION_PREFIX = "... "


@dataclass(frozen=True)
class QmtHeartbeatStatus:
    qmt_connected: bool
    snapshot: dict[str, Any] | None
    last_error: str | None


def check_qmt_connection_for_heartbeat(
    broker: Any,
    last_error: str | None = None,
    logger: logging.Logger | None = None,
) -> QmtHeartbeatStatus:
    snapshot: dict[str, Any] | None = None
    try:
        broker.connect()
        account = broker.get_account_snapshot()
        positions = broker.get_positions()
        prices = _heartbeat_prices(broker, positions, last_error, logger)
        last_error = prices.last_error
        snapshot = account_result_snapshot(account, positions, prices.values)
        return QmtHeartbeatStatus(
            qmt_connected=True,
            snapshot=snapshot,
            last_error=normalize_heartbeat_error(last_error),
        )
    except Exception as exc:  # noqa: BLE001 - heartbeat must still reach Xquant
        if logger is not None:
            logger.exception("failed to check QMT during heartbeat")
        return QmtHeartbeatStatus(
            qmt_connected=False,
            snapshot=None,
            last_error=append_heartbeat_error(last_error, f"heartbeat qmt check failed: {exc}"),
        )


@dataclass(frozen=True)
class _HeartbeatPrices:
    values: dict[str, float]
    last_error: str | None


def _heartbeat_prices(
    broker: Any,
    positions: list[Position],
    last_error: str | None,
    logger: logging.Logger | None,
) -> _HeartbeatPrices:
    symbols = sorted({position.symbol for position in positions})
    if not symbols:
        return _HeartbeatPrices({}, last_error)
    try:
        return _HeartbeatPrices(broker.get_prices(symbols), last_error)
    except Exception as exc:  # noqa: BLE001 - holdings can still use broker market_value
        if logger is not None:
            logger.exception("failed to query account prices during heartbeat")
        return _HeartbeatPrices(
            {},
            append_heartbeat_error(last_error, f"heartbeat price query failed: {exc}"),
        )


def account_result_snapshot(
    account: AccountSnapshot,
    positions: list[Position],
    prices: dict[str, float],
    task: RebalanceTask | None = None,
) -> dict[str, Any]:
    target_weights = {target.symbol: target.target_weight for target in task.targets} if task else {}
    holdings = []
    for position in positions:
        reference_price = prices.get(position.symbol)
        market_value = position.market_value
        if not market_value and reference_price is not None:
            market_value = position.quantity * reference_price
        weight = market_value / account.total_asset if account.total_asset > 0 else None
        holdings.append(
            {
                "symbol": position.symbol,
                "shares": position.quantity,
                "reference_price": reference_price,
                "market_value": market_value,
                "weight": weight,
                "target_weight": target_weights.get(position.symbol),
            }
        )
    return {"cash": account.cash, "total_asset": account.total_asset, "holdings": holdings}


def xtquant_importable() -> bool:
    try:
        import xtquant  # noqa: F401
    except ImportError:
        return False
    return True


def append_heartbeat_error(last_error: str | None, error: str) -> str:
    error = error.strip()
    if not error:
        return normalize_heartbeat_error(last_error) or ""
    parts = _error_parts(last_error)
    if not parts or parts[-1] != error:
        parts.append(error)
    return _cap_heartbeat_error(_ERROR_SEPARATOR.join(parts))


def normalize_heartbeat_error(last_error: str | None) -> str | None:
    if last_error is None:
        return None
    parts = _error_parts(last_error)
    if not parts:
        return None
    return _cap_heartbeat_error(_ERROR_SEPARATOR.join(parts))


def _error_parts(last_error: str | None) -> list[str]:
    if not last_error:
        return []
    parts: list[str] = []
    for raw_part in last_error.split(_ERROR_SEPARATOR):
        part = raw_part.strip()
        if part and (not parts or parts[-1] != part):
            parts.append(part)
    return parts


def _cap_heartbeat_error(value: str) -> str:
    if len(value) <= MAX_HEARTBEAT_ERROR_LENGTH:
        return value
    tail_length = MAX_HEARTBEAT_ERROR_LENGTH - len(_TRUNCATION_PREFIX)
    if tail_length <= 0:
        return value[-MAX_HEARTBEAT_ERROR_LENGTH:]
    return f"{_TRUNCATION_PREFIX}{value[-tail_length:]}"
