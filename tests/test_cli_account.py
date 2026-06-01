from __future__ import annotations

import httpx

from trade_xquant.cli import heartbeat_command, register_account_command


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

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")

    result = heartbeat_command(
        config_path=str(config_path),
        qmt_connected=True,
        last_error=None,
        client=client,
    )

    assert result["ok"] is True
