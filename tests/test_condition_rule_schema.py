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
        (
            "trailing_pct",
            "take_profit",
            {"trail_pct": 0.08, "activation_profit_pct": 0.12},
        ),
        (
            "atr_trailing",
            "stop_loss",
            {"atr_window": 3, "atr_multiple": 2.0, "bar_interval": "1d"},
        ),
        (
            "atr_trailing",
            "take_profit",
            {
                "atr_window": 3,
                "atr_multiple": 2.0,
                "bar_interval": "1d",
                "activation_price": 1.2,
            },
        ),
        (
            "hv_log_trailing",
            "stop_loss",
            {
                "hv_window": 3,
                "hv_annualization": 252,
                "lambda": 1.0,
                "bar_interval": "1d",
            },
        ),
        (
            "hv_log_trailing",
            "take_profit",
            {
                "hv_window": 3,
                "hv_annualization": 252,
                "lambda": 1.0,
                "bar_interval": "1d",
                "activation_profit_pct": 0.1,
            },
        ),
        (
            "std_trailing",
            "stop_loss",
            {"std_window": 3, "std_multiple": 1.5, "bar_interval": "1d"},
        ),
        (
            "std_trailing",
            "take_profit",
            {
                "std_window": 3,
                "std_multiple": 1.5,
                "bar_interval": "1d",
                "activation_price": 1.2,
            },
        ),
    ]

    for method, purpose, params in cases:
        order = condition(method, purpose, params)
        assert order.method == method
        assert validate_condition_hyperparameters(order) == []


def test_required_condition_params_are_method_and_purpose_specific() -> None:
    order = condition("trailing_pct", "take_profit", {"trail_pct": 0.08})

    assert required_condition_params(order) == {
        "trail_pct",
        "activation_profit_pct|activation_price",
    }
    assert validate_condition_hyperparameters(order) == [
        "activation_profit_pct|activation_price"
    ]


def test_legacy_pct_alias_satisfies_static_and_trailing_validation() -> None:
    cases = [
        condition("static_pct", "stop_loss", {"pct": 0.05}),
        condition("static_pct", "take_profit", {"pct": 0.10}),
        condition("trailing_pct", "stop_loss", {"pct": 0.08}),
        condition(
            "trailing_pct",
            "take_profit",
            {"pct": 0.08, "activation_profit_pct": 0.12},
        ),
    ]

    for order in cases:
        assert validate_condition_hyperparameters(order) == []


def test_missing_reference_price_is_reported() -> None:
    order = condition("static_pct", "stop_loss", {"stop_loss_pct": 0.05})
    order = order.model_copy(update={"reference_price": None})

    assert validate_condition_hyperparameters(order) == ["reference_price"]


def test_non_instrument_scope_is_reported() -> None:
    order = condition("static_pct", "stop_loss", {"stop_loss_pct": 0.05})
    order = order.model_copy(update={"scope": "portfolio"})

    assert validate_condition_hyperparameters(order) == ["scope:instrument"]


def test_unsupported_method_fails_validation() -> None:
    with pytest.raises(ValidationError):
        condition("unsupported", "stop_loss", {})
