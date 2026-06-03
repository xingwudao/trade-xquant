from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

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

    @model_validator(mode="after")
    def require_high_at_least_low(self) -> PriceBar:
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
        return self


class ConditionIndicatorEngine:
    def atr(self, bars: list[PriceBar]) -> float:
        if not bars:
            raise ValueError("atr requires at least one bar")

        true_ranges: list[float] = []
        for index, bar in enumerate(bars):
            if index == 0:
                true_ranges.append(bar.high - bar.low)
                continue
            previous_close = bars[index - 1].close
            true_ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(previous_close - bar.low),
                )
            )
        return round_money(sum(true_ranges) / len(true_ranges))

    def hv_log_return(self, bars: list[PriceBar], annualization: float) -> float:
        if len(bars) < 2:
            raise ValueError("hv_log_return requires at least two bars")
        if not (math.isfinite(annualization) and annualization > 0):
            raise ValueError("annualization must be finite and positive")

        returns = [
            math.log(current.close / previous.close)
            for previous, current in zip(bars, bars[1:])
        ]
        mean = sum(returns) / len(returns)
        variance = sum((item - mean) ** 2 for item in returns) / len(returns)
        return math.sqrt(variance) * math.sqrt(annualization)

    def price_std(self, bars: list[PriceBar]) -> float:
        if not bars:
            raise ValueError("price_std requires at least one bar")

        closes = [bar.close for bar in bars]
        mean = sum(closes) / len(closes)
        variance = sum((item - mean) ** 2 for item in closes) / len(closes)
        return math.sqrt(variance)
