from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trade_xquant.condition_indicators import (
    ConditionIndicatorEngine,
    PriceBar,
)


def bars() -> list[PriceBar]:
    tz = ZoneInfo("Asia/Shanghai")
    return [
        PriceBar(
            symbol="513100.SH",
            high=10.5,
            low=9.5,
            close=10.0,
            timestamp=datetime(2026, 6, 1, tzinfo=tz),
        ),
        PriceBar(
            symbol="513100.SH",
            high=11.0,
            low=10.0,
            close=10.8,
            timestamp=datetime(2026, 6, 2, tzinfo=tz),
        ),
        PriceBar(
            symbol="513100.SH",
            high=12.2,
            low=10.6,
            close=12.0,
            timestamp=datetime(2026, 6, 3, tzinfo=tz),
        ),
    ]


def test_atr_uses_true_range_average() -> None:
    engine = ConditionIndicatorEngine()
    value = engine.atr(bars())
    assert value == 1.2


def test_hv_uses_annualized_log_returns() -> None:
    engine = ConditionIndicatorEngine()
    value = engine.hv_log_return(bars(), annualization=252)
    returns = [math.log(10.8 / 10.0), math.log(12.0 / 10.8)]
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

    with pytest.raises(ValueError, match="at least two bars"):
        engine.hv_log_return(bars()[:1], annualization=252)


def test_price_bar_normalizes_symbol() -> None:
    tz = ZoneInfo("Asia/Shanghai")

    bar = PriceBar(
        symbol=" 513100.ss ",
        high=10.5,
        low=9.5,
        close=10.0,
        timestamp=datetime(2026, 6, 1, tzinfo=tz),
    )

    assert bar.symbol == "513100.SH"


def test_price_bar_rejects_high_below_low() -> None:
    tz = ZoneInfo("Asia/Shanghai")

    with pytest.raises(ValueError, match="high must be greater than or equal to low"):
        PriceBar(
            symbol="513100.SH",
            high=9.5,
            low=10.5,
            close=10.0,
            timestamp=datetime(2026, 6, 1, tzinfo=tz),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("high", math.inf),
        ("low", math.nan),
        ("close", -math.inf),
        ("close", math.inf),
    ],
)
def test_price_bar_rejects_non_finite_prices(field: str, value: float) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    payload = {
        "symbol": "513100.SH",
        "high": 10.5,
        "low": 9.5,
        "close": 10.0,
        "timestamp": datetime(2026, 6, 1, tzinfo=tz),
    }
    payload[field] = value

    with pytest.raises(ValueError):
        PriceBar(**payload)


def test_price_bar_rejects_close_above_high() -> None:
    tz = ZoneInfo("Asia/Shanghai")

    with pytest.raises(ValueError, match="close must be between low and high"):
        PriceBar(
            symbol="513100.SH",
            high=10.5,
            low=9.5,
            close=11.0,
            timestamp=datetime(2026, 6, 1, tzinfo=tz),
        )


def test_price_bar_rejects_close_below_low() -> None:
    tz = ZoneInfo("Asia/Shanghai")

    with pytest.raises(ValueError, match="close must be between low and high"):
        PriceBar(
            symbol="513100.SH",
            high=10.5,
            low=9.5,
            close=9.0,
            timestamp=datetime(2026, 6, 1, tzinfo=tz),
        )


@pytest.mark.parametrize("annualization", [0, -1, math.nan, math.inf])
def test_hv_rejects_non_positive_or_non_finite_annualization(
    annualization: float,
) -> None:
    engine = ConditionIndicatorEngine()

    with pytest.raises(ValueError, match="annualization must be finite and positive"):
        engine.hv_log_return(bars(), annualization=annualization)


def test_indicator_engine_rejects_empty_bars() -> None:
    engine = ConditionIndicatorEngine()

    with pytest.raises(ValueError, match="at least one bar"):
        engine.atr([])
    with pytest.raises(ValueError, match="at least one bar"):
        engine.price_std([])
