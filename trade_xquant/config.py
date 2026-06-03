from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from trade_xquant.auth import TokenStore, token_path_for_config


class XquantConfig(BaseModel):
    base_url: str
    api_token: str | None = None
    product_code: str | None = None
    timeout_seconds: float = 15.0
    trust_env: bool = False


class QmtConfig(BaseModel):
    userdata_mini_path: str
    account_id: str
    session_id: int | None = None
    session_id_strategy: str = "auto"
    order_price_type: str = "fix"
    strategy_name: str = "trade_xquant"
    cancel_after_order: bool = False


class RuntimeConfig(BaseModel):
    poll_interval_seconds: int = 30
    condition_poll_interval_seconds: int = 3
    allow_real_order: bool = False
    dry_run_default: bool = True
    broker_adapter: str = "qmt"
    local_task_file: str | None = None
    simulate_real_orders: bool = False
    mock_submit_dry_run_orders: bool = False
    mock_order_behavior: str = "filled"
    mock_partial_fill_ratio: float = 0.5
    mock_total_asset: float = 100_000
    mock_cash: float = 100_000
    mock_prices: dict[str, float] = Field(default_factory=dict)
    db_path: str = "data/trade_xquant.db"
    log_path: str = "logs/trade_xquant.jsonl"


class RiskConfig(BaseModel):
    max_single_order_amount: float = 50_000
    min_order_amount: float = 1_000
    max_turnover_ratio: float = 0.8
    cash_buffer_ratio: float = 0.002
    timezone: str = "Asia/Shanghai"


class Settings(BaseModel):
    xquant: XquantConfig
    qmt: QmtConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)

    def sanitized_summary(self) -> dict[str, Any]:
        return {
            "xquant": {
                "base_url": self.xquant.base_url,
                "api_token": "***" if self.xquant.api_token else None,
                "product_code": self.xquant.product_code,
                "trust_env": self.xquant.trust_env,
            },
            "qmt": {
                "userdata_mini_path": self.qmt.userdata_mini_path,
                "account_id": self.qmt.account_id,
                "session_id_strategy": self.qmt.session_id_strategy,
            },
            "runtime": self.runtime.model_dump(),
            "risk": self.risk.model_dump(),
        }


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    data = _apply_env_overrides(data, config_path)
    return Settings.model_validate(data)


def _apply_env_overrides(data: dict[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    result = dict(data)
    if os.getenv("XQUANT_API_TOKEN"):
        result.setdefault("xquant", {})["api_token"] = os.environ["XQUANT_API_TOKEN"]
    elif config_path is not None and _is_blank_token(result.get("xquant", {}).get("api_token")):
        token = TokenStore(token_path_for_config(config_path)).load_access_token()
        if token:
            result.setdefault("xquant", {})["api_token"] = token
    if os.getenv("XQUANT_API_BASE_URL"):
        result.setdefault("xquant", {})["base_url"] = os.environ["XQUANT_API_BASE_URL"]
    if os.getenv("QMT_ACCOUNT_ID"):
        result.setdefault("qmt", {})["account_id"] = os.environ["QMT_ACCOUNT_ID"]
    if os.getenv("QMT_USERDATA_MINI_PATH"):
        result.setdefault("qmt", {})["userdata_mini_path"] = os.environ["QMT_USERDATA_MINI_PATH"]
    return result


def _is_blank_token(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    return value.strip() == "" or value.startswith("replace-with-")
