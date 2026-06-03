from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from trade_xquant.condition_orders import (
    ConditionAction,
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
        action=ConditionAction(type="sell_pct", pct=1.0),
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
        "activation_profit_pct|activation_price",
        "trail_pct",
    }
    assert validate_condition_hyperparameters(order) == [
        "activation_profit_pct|activation_price"
    ]


def test_trailing_pct_stop_loss_legacy_shape_does_not_require_reference() -> None:
    stop_loss = condition("trailing_pct", "stop_loss", {"trail_pct": 0.08})

    assert validate_condition_hyperparameters(
        stop_loss.model_copy(update={"reference_price": None})
    ) == []


def test_trailing_pct_take_profit_activation_pct_requires_reference_price() -> None:
    order = condition(
        "trailing_pct",
        "take_profit",
        {"trail_pct": 0.08, "activation_profit_pct": 0.12},
    )

    assert validate_condition_hyperparameters(
        order.model_copy(update={"reference_price": None})
    ) == ["reference_price"]


def test_trailing_pct_take_profit_activation_price_does_not_require_reference() -> None:
    order = condition(
        "trailing_pct",
        "take_profit",
        {"trail_pct": 0.08, "activation_price": 1.2},
    )

    assert validate_condition_hyperparameters(
        order.model_copy(update={"reference_price": None})
    ) == []


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


def test_pct_alias_does_not_satisfy_deferred_method_params() -> None:
    order = condition(
        "atr_trailing",
        "stop_loss",
        {"pct": 0.08, "atr_window": 3, "bar_interval": "1d"},
    )

    assert validate_condition_hyperparameters(order) == ["atr_multiple"]


def test_missing_reference_price_is_reported() -> None:
    order = condition("static_pct", "stop_loss", {"stop_loss_pct": 0.05})
    order = order.model_copy(update={"reference_price": None})

    assert validate_condition_hyperparameters(order) == ["reference_price"]


def test_deferred_take_profit_reference_depends_on_activation_form() -> None:
    activation_price = condition(
        "atr_trailing",
        "take_profit",
        {
            "atr_window": 3,
            "atr_multiple": 2.0,
            "bar_interval": "1d",
            "activation_price": 1.2,
        },
    )
    activation_profit_pct = condition(
        "atr_trailing",
        "take_profit",
        {
            "atr_window": 3,
            "atr_multiple": 2.0,
            "bar_interval": "1d",
            "activation_profit_pct": 0.1,
        },
    )

    assert validate_condition_hyperparameters(
        activation_price.model_copy(update={"reference_price": None})
    ) == []
    assert validate_condition_hyperparameters(
        activation_profit_pct.model_copy(update={"reference_price": None})
    ) == ["reference_price"]


def test_non_instrument_scope_is_reported() -> None:
    order = condition("static_pct", "stop_loss", {"stop_loss_pct": 0.05})
    order = order.model_copy(update={"scope": "portfolio"})

    assert validate_condition_hyperparameters(order) == ["scope:instrument"]


def test_unsupported_method_fails_validation() -> None:
    with pytest.raises(ValidationError):
        condition("unsupported", "stop_loss", {})


def test_condition_order_requires_explicit_action() -> None:
    with pytest.raises(ValidationError, match="action"):
        ConditionOrder(
            condition_id="cond-missing-action",
            task_id="task-1",
            portfolio_id="prod",
            account_id="acct",
            mode="dry_run",
            symbol="513100.SH",
            purpose="take_profit",
            method="static_pct",
            reference_price=1.0,
            params={"take_profit_pct": 0.1},
        )


def test_condition_action_requires_explicit_type() -> None:
    with pytest.raises(ValidationError, match="type"):
        ConditionAction.model_validate({"pct": 1.0})


def test_sell_pct_action_requires_explicit_safe_pct() -> None:
    with pytest.raises(ValidationError, match="pct"):
        ConditionAction.model_validate({"type": "sell_pct"})

    for value in [0, -0.1, 1.1, math.inf, math.nan]:
        with pytest.raises(ValidationError, match="pct"):
            ConditionAction.model_validate({"type": "sell_pct", "pct": value})


def test_clear_action_parses_without_pct() -> None:
    action = ConditionAction.model_validate({"type": "clear"})

    assert action.type == "clear"


def test_clear_action_rejects_malformed_pct_when_present() -> None:
    for value in [0, -0.1, 1.1, math.inf, math.nan]:
        with pytest.raises(ValidationError, match="pct"):
            ConditionAction.model_validate({"type": "clear", "pct": value})


def test_non_finite_reference_price_is_reported() -> None:
    order = condition("static_pct", "stop_loss", {"stop_loss_pct": 0.05})
    order = order.model_copy(update={"reference_price": math.inf})

    assert validate_condition_hyperparameters(order) == ["reference_price"]


def test_non_finite_stored_price_state_is_reported_when_present() -> None:
    order = condition("trailing_pct", "stop_loss", {"trail_pct": 0.08})
    order = order.model_copy(
        update={
            "high_water_price": math.inf,
            "trigger_price": math.nan,
        }
    )

    assert validate_condition_hyperparameters(order) == [
        "high_water_price",
        "trigger_price",
    ]


@pytest.mark.parametrize(
    ("method", "purpose", "params", "expected"),
    [
        ("static_pct", "stop_loss", {"stop_loss_pct": 0}, "stop_loss_pct"),
        ("static_pct", "stop_loss", {"stop_loss_pct": -0.1}, "stop_loss_pct"),
        ("static_pct", "stop_loss", {"stop_loss_pct": 1}, "stop_loss_pct"),
        ("static_pct", "stop_loss", {"stop_loss_pct": math.inf}, "stop_loss_pct"),
        ("static_pct", "take_profit", {"take_profit_pct": 0}, "take_profit_pct"),
        ("static_pct", "take_profit", {"take_profit_pct": -0.1}, "take_profit_pct"),
        ("static_pct", "take_profit", {"take_profit_pct": math.nan}, "take_profit_pct"),
        ("trailing_pct", "stop_loss", {"trail_pct": 0}, "trail_pct"),
        ("trailing_pct", "stop_loss", {"trail_pct": 1}, "trail_pct"),
        (
            "trailing_pct",
            "take_profit",
            {"trail_pct": 0.1, "activation_profit_pct": 0},
            "activation_profit_pct",
        ),
        (
            "trailing_pct",
            "take_profit",
            {"trail_pct": 0.1, "activation_price": 0},
            "activation_price",
        ),
        (
            "atr_trailing",
            "stop_loss",
            {"atr_window": 0, "atr_multiple": 2.0, "bar_interval": "1d"},
            "atr_window",
        ),
        (
            "atr_trailing",
            "stop_loss",
            {"atr_window": 3.5, "atr_multiple": 2.0, "bar_interval": "1d"},
            "atr_window",
        ),
        (
            "atr_trailing",
            "stop_loss",
            {"atr_window": 3, "atr_multiple": 0, "bar_interval": "1d"},
            "atr_multiple",
        ),
        (
            "hv_log_trailing",
            "stop_loss",
            {
                "hv_window": 0,
                "hv_annualization": 252,
                "lambda": 1.0,
                "bar_interval": "1d",
            },
            "hv_window",
        ),
        (
            "hv_log_trailing",
            "stop_loss",
            {
                "hv_window": 3,
                "hv_annualization": 0,
                "lambda": 1.0,
                "bar_interval": "1d",
            },
            "hv_annualization",
        ),
        (
            "hv_log_trailing",
            "stop_loss",
            {
                "hv_window": 3,
                "hv_annualization": 252,
                "lambda": 0,
                "bar_interval": "1d",
            },
            "lambda",
        ),
        (
            "std_trailing",
            "stop_loss",
            {"std_window": 0, "std_multiple": 1.5, "bar_interval": "1d"},
            "std_window",
        ),
        (
            "std_trailing",
            "stop_loss",
            {"std_window": 3, "std_multiple": math.inf, "bar_interval": "1d"},
            "std_multiple",
        ),
        (
            "std_trailing",
            "stop_loss",
            {"std_window": 3, "std_multiple": 1.5, "bar_interval": ""},
            "bar_interval",
        ),
    ],
)
def test_invalid_condition_hyperparameters_are_reported(
    method: str,
    purpose: str,
    params: dict,
    expected: str,
) -> None:
    order = condition(method, purpose, params)

    assert expected in validate_condition_hyperparameters(order)
