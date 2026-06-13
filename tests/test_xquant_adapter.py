from __future__ import annotations

from datetime import date

import httpx

import pytest

from trade_xquant.heartbeat import MAX_HEARTBEAT_ERROR_LENGTH
from trade_xquant.xquant_adapter import XquantAdapter, XquantAdapterError


def test_fetch_pending_tasks_parses_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token"
        assert request.url.path == "/api/v1/trading-gateway/tasks"
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {
                        "task_id": "rebalance_1",
                        "portfolio_id": "demo",
                        "account_id": "acct",
                        "mode": "dry_run",
                        "signal_as_of_date": "2026-05-20",
                        "signal_effective_date": "2026-05-21",
                        "created_at": "2026-05-27T09:35:00+08:00",
                        "expires_at": "2026-05-27T14:50:00+08:00",
                        "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", api_token="token", client=client)

    tasks = adapter.fetch_pending_tasks(account_id="acct", limit=10)

    assert len(tasks) == 1
    assert tasks[0].task_id == "rebalance_1"
    assert str(tasks[0].signal_as_of_date) == "2026-05-20"
    assert str(tasks[0].signal_effective_date) == "2026-05-21"
    assert tasks[0].targets[0].symbol == "513100.SH"


def test_fetch_trading_calendar_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token"
        assert request.url.path == "/api/v1/trading-gateway/trading-calendar"
        assert request.url.params["market"] == "CN_A"
        assert request.url.params["start_date"] == "2026-06-12"
        assert request.url.params["end_date"] == "2026-06-13"
        return httpx.Response(
            200,
            json={
                "market": "CN_A",
                "timezone": "Asia/Shanghai",
                "start_date": "2026-06-12",
                "end_date": "2026-06-13",
                "calendar_version": "cn-a-2026.06.12-sse2026r001-szse2026r001",
                "generated_at": "2026-06-12T19:50:00+08:00",
                "days": [
                    {
                        "date": "2026-06-12",
                        "is_trading_day": True,
                        "sessions": [
                            {"name": "morning", "start": "09:30", "end": "11:30"},
                            {"name": "afternoon", "start": "13:00", "end": "14:57"},
                        ],
                    },
                    {"date": "2026-06-13", "is_trading_day": False, "sessions": []},
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", api_token="token", client=client)

    calendar = adapter.fetch_trading_calendar(
        market="CN_A",
        start_date=date(2026, 6, 12),
        end_date=date(2026, 6, 13),
    )

    assert calendar["market"] == "CN_A"
    assert calendar["days"][1]["is_trading_day"] is False


def test_report_result_posts_execution_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", api_token=None, client=client)

    adapter.report_result("task-1", "success", {"status": "stale", "orders": []})

    assert seen["path"] == "/api/v1/trading-gateway/tasks/task-1/result"
    assert '"status":"success"' in str(seen["payload"])
    assert '"status":"stale"' not in str(seen["payload"])


def test_report_condition_result_posts_audit_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", client=client)

    adapter.report_condition_result(
        "task-1",
        "cond-1",
        {
            "condition_task_id": "condition:cond-1",
            "status": "submitted",
            "trigger": {"reason": "latest_price <= trigger_price"},
        },
    )

    assert seen["path"] == "/api/v1/trading-gateway/tasks/task-1/condition-results"
    assert '"source_task_id":"task-1"' in str(seen["payload"])
    assert '"condition_id":"cond-1"' in str(seen["payload"])


def test_manual_task_preview_and_create_contract() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.read().decode()))
        if request.url.path.endswith("/preview"):
            return httpx.Response(200, json={"preview_token": "preview-token"})
        return httpx.Response(
            200,
            json={"ok": True, "task_id": "manual-task-1", "status": "pending"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", api_token="token", client=client)

    preview = adapter.preview_manual_task("prod", "acct", mode="dry_run")
    created = adapter.create_manual_task("prod", "acct", preview["preview_token"], mode="dry_run")

    assert preview["preview_token"] == "preview-token"
    assert created["task_id"] == "manual-task-1"
    assert seen[0][0] == "/api/v1/trading-gateway/products/prod/manual-tasks/preview"
    assert '"account_id":"acct"' in seen[0][1]
    assert seen[1][0] == "/api/v1/trading-gateway/products/prod/manual-tasks"
    assert '"preview_token":"preview-token"' in seen[1][1]


def test_404_error_exposes_status_code() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(404, json={"detail": "Not Found"})),
        base_url="http://xquant",
    )
    adapter = XquantAdapter("http://xquant/api/v1", client=client)

    with pytest.raises(XquantAdapterError) as exc:
        adapter.fetch_pending_tasks(account_id="acct")

    assert exc.value.status_code == 404


def test_fetch_pending_tasks_accepts_null_expires_at() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {
                        "task_id": "task-1",
                        "portfolio_id": "prod",
                        "account_id": "acct",
                        "mode": "dry_run",
                        "created_at": "2026-05-27T09:35:00+08:00",
                        "expires_at": None,
                        "targets": [{"symbol": "513100.SH", "target_weight": 0.5}],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", client=client)

    tasks = adapter.fetch_pending_tasks("acct")

    assert tasks[0].expires_at is None


def test_register_account_and_heartbeat_contracts() -> None:
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append((request.method, request.url.path, json.loads(request.read().decode())))
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json={"id": "gwacct_1", "account_id": "acct", "enabled": True})
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", api_token="token", client=client)

    account = adapter.register_account(
        account_id="acct",
        broker="guojin",
        client_type="qmt",
        display_name="QMT",
        default_mode="dry_run",
        risk_profile={"max_turnover_ratio": 0.8},
    )
    heartbeat = adapter.heartbeat(
        account_id="acct",
        client_version="0.1.0",
        hostname="WIN-QMT-01",
        qmt_connected=True,
        xtquant_importable=True,
        last_error=None,
        cash=98000.0,
        total_asset=100000.0,
        holdings=[
            {
                "symbol": "510300.SH",
                "shares": 1000,
                "reference_price": 4.2,
                "market_value": 4200.0,
                "weight": 0.042,
                "target_weight": None,
            }
        ],
    )

    assert account["id"] == "gwacct_1"
    assert heartbeat["ok"] is True
    assert seen[0][1] == "/api/v1/trading-gateway/accounts"
    assert seen[1][1] == "/api/v1/trading-gateway/accounts/acct/heartbeat"
    assert seen[1][2]["cash"] == 98000.0
    assert seen[1][2]["total_asset"] == 100000.0
    assert seen[1][2]["holdings"][0]["symbol"] == "510300.SH"


def test_heartbeat_caps_last_error_before_posting() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xquant")
    adapter = XquantAdapter("http://xquant/api/v1", api_token="token", client=client)

    adapter.heartbeat(
        account_id="acct",
        client_version="0.1.0",
        hostname="WIN-QMT-01",
        qmt_connected=False,
        xtquant_importable=True,
        last_error="x" * 5000,
    )

    assert len(seen[0]["last_error"]) <= MAX_HEARTBEAT_ERROR_LENGTH


def test_default_client_ignores_environment_proxy(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9")

    adapter = XquantAdapter("http://xquant/api/v1")

    assert adapter.client._trust_env is False
    adapter.close()
