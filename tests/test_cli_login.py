from __future__ import annotations

import httpx

from trade_xquant.auth import token_path_for_config
from trade_xquant.cli import login_command


def test_login_command_writes_token_next_to_config(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
xquant:
  base_url: "http://xquant/api/v1"
  api_token: null
qmt:
  userdata_mini_path: "C:/QMT/userdata_mini"
  account_id: "acct"
""",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "jwt-token", "token_type": "bearer"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")

    result = login_command(
        config_path=str(config_path),
        phone="test-phone",
        email=None,
        otp="123456",
        send_otp=False,
        client=client,
    )

    assert result["token_path"] == str(token_path_for_config(config_path))
    assert token_path_for_config(config_path).exists()
