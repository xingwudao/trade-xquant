from __future__ import annotations

import httpx

from trade_xquant.cli import heartbeat_command, register_account_command
from trade_xquant.heartbeat import MAX_HEARTBEAT_ERROR_LENGTH, append_heartbeat_error
from trade_xquant.models import AccountSnapshot, Position


def write_config(path) -> None:
    path.write_text(
        """
xquant:
  base_url: "http://xquant/api/v1"
  api_token: "token"
qmt:
  userdata_mini_path: "C:/QMT/userdata_mini"
  account_id: "acct"
""",
        encoding="utf-8",
    )


def test_register_account_command_uses_config_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "gwacct_1", "account_id": "acct", "enabled": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")

    result = register_account_command(
        config_path=str(config_path),
        broker="guojin",
        client_type="qmt",
        display_name="QMT",
        default_mode="dry_run",
        client=client,
    )

    assert result["account_id"] == "acct"


def test_heartbeat_command_posts_status(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")

    result = heartbeat_command(
        config_path=str(config_path),
        last_error=None,
        client=client,
        broker=FailingBroker(),
    )

    assert result["ok"] is True
    assert seen[0]["qmt_connected"] is False
    assert "heartbeat qmt check failed" in seen[0]["last_error"]
    assert len(seen[0]["last_error"]) <= MAX_HEARTBEAT_ERROR_LENGTH


def test_append_heartbeat_error_deduplicates_consecutive_errors() -> None:
    error = "QMT connect failed"

    last_error = error
    for _ in range(10):
        last_error = append_heartbeat_error(last_error, error)

    assert last_error == error


def test_append_heartbeat_error_caps_payload_length() -> None:
    last_error = None
    for index in range(200):
        last_error = append_heartbeat_error(last_error, f"error-{index:03d}-" + "x" * 40)

    assert last_error is not None
    assert len(last_error) <= MAX_HEARTBEAT_ERROR_LENGTH
    assert "error-199" in last_error


class FailingBroker:
    def connect(self) -> None:
        raise ConnectionError("qmt offline")


class FakeBroker:
    def __init__(self) -> None:
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(account_id="acct", cash=98000.0, total_asset=100000.0)

    def get_positions(self) -> list[Position]:
        return [
            Position(
                symbol="510300.SH",
                quantity=1000,
                sellable_quantity=1000,
                market_value=4200.0,
            )
        ]

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        return {symbol: 4.2 for symbol in symbols}


def test_heartbeat_command_uploads_holdings_when_qmt_connected(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    broker = FakeBroker()

    result = heartbeat_command(
        config_path=str(config_path),
        last_error=None,
        client=client,
        broker=broker,
    )

    assert result["ok"] is True
    assert broker.connected is True
    assert seen[0]["qmt_connected"] is True
    assert seen[0]["cash"] == 98000.0
    assert seen[0]["total_asset"] == 100000.0
    assert seen[0]["holdings"] == [
        {
            "symbol": "510300.SH",
            "shares": 1000,
            "reference_price": 4.2,
            "market_value": 4200.0,
            "weight": 0.042,
            "target_weight": None,
        }
    ]


def test_runtime_config_includes_order_lifecycle_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)

    from trade_xquant.config import load_settings

    settings = load_settings(config_path)

    assert settings.runtime.order_sync_interval_seconds == 30
    assert settings.runtime.submitted_order_timeout_seconds == 180
    assert settings.runtime.max_rebalance_retries == 3
