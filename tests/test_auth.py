from __future__ import annotations

import json

import httpx

from trade_xquant.auth import TokenStore, token_path_for_config
from trade_xquant.config import load_settings
from trade_xquant.xquant_adapter import XquantAdapter


def test_xquant_login_uses_existing_auth_contract() -> None:
    requests: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.read().decode()))
        if request.url.path.endswith("/auth/otp/send"):
            return httpx.Response(200, json={"ok": True, "otp": "123456", "ttl_seconds": 600})
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "jwt-token", "token_type": "bearer"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", client=client)

    otp = adapter.send_otp(channel="phone", phone="test-phone")
    token = adapter.login(method="phone_otp", phone="test-phone", otp="123456")

    assert otp["otp"] == "123456"
    assert token["access_token"] == "jwt-token"
    assert requests[0][1] == "/api/v1/auth/otp/send"
    assert requests[1][1] == "/api/v1/auth/login"


def test_token_store_writes_to_config_directory_and_load_settings_uses_it(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
xquant:
  base_url: "http://xquant/api/v1"
  product_code: "prod_global_rotation_etf"
  api_token: null
qmt:
  userdata_mini_path: "C:/QMT/userdata_mini"
  account_id: "acct"
""",
        encoding="utf-8",
    )
    store = TokenStore(token_path_for_config(config_path))

    store.save(access_token="jwt-token", token_type="bearer")

    assert token_path_for_config(config_path).parent == tmp_path
    assert json.loads(token_path_for_config(config_path).read_text(encoding="utf-8"))[
        "access_token"
    ] == "jwt-token"
    assert load_settings(config_path).xquant.api_token == "jwt-token"
    assert load_settings(config_path).xquant.product_code == "prod_global_rotation_etf"
