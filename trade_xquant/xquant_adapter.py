from __future__ import annotations

from typing import Any

import httpx

from trade_xquant.heartbeat import normalize_heartbeat_error
from trade_xquant.models import ExecutionResult, RebalanceTask


class XquantAdapterError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class XquantAdapter:
    def __init__(
        self,
        base_url: str,
        api_token: str | None = None,
        timeout_seconds: float = 15.0,
        trust_env: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds,
            trust_env=trust_env,
        )

    def fetch_pending_tasks(self, account_id: str, limit: int = 10) -> list[RebalanceTask]:
        response = self.client.get(
            self._url("/trading-gateway/tasks"),
            params={"account_id": account_id, "limit": limit},
            headers=self._headers(),
        )
        data = self._handle(response)
        raw_tasks = data.get("tasks", data if isinstance(data, list) else [])
        return [RebalanceTask.model_validate({**task, "raw": task}) for task in raw_tasks]

    def send_otp(
        self,
        channel: str,
        phone: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.post(
            self._url("/auth/otp/send"),
            json={"channel": channel, "phone": phone, "email": email},
            headers={"Content-Type": "application/json"},
        )
        return self._handle(response)

    def login(
        self,
        method: str,
        otp: str,
        phone: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.post(
            self._url("/auth/login"),
            json={"method": method, "phone": phone, "email": email, "otp": otp},
            headers={"Content-Type": "application/json"},
        )
        data = self._handle(response)
        token = data.get("access_token")
        if not token:
            raise XquantAdapterError("Xquant login response missing access_token")
        return data

    def register_account(
        self,
        account_id: str,
        broker: str,
        client_type: str,
        display_name: str | None = None,
        default_mode: str = "dry_run",
        risk_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.client.post(
            self._url("/trading-gateway/accounts"),
            json={
                "account_id": account_id,
                "broker": broker,
                "client_type": client_type,
                "display_name": display_name,
                "default_mode": default_mode,
                "risk_profile": risk_profile or {},
            },
            headers=self._headers(),
        )
        return self._handle(response)

    def heartbeat(
        self,
        account_id: str,
        client_version: str,
        hostname: str,
        qmt_connected: bool,
        xtquant_importable: bool,
        last_error: str | None = None,
        cash: float | None = None,
        total_asset: float | None = None,
        holdings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "client_version": client_version,
            "hostname": hostname,
            "qmt_connected": qmt_connected,
            "xtquant_importable": xtquant_importable,
            "last_error": normalize_heartbeat_error(last_error),
        }
        if cash is not None:
            payload["cash"] = cash
        if total_asset is not None:
            payload["total_asset"] = total_asset
        if holdings is not None:
            payload["holdings"] = holdings
        response = self.client.post(
            self._url(f"/trading-gateway/accounts/{account_id}/heartbeat"),
            json=payload,
            headers=self._headers(),
        )
        return self._handle(response)

    def preview_manual_task(
        self,
        product_code: str,
        account_id: str,
        mode: str = "dry_run",
    ) -> dict[str, Any]:
        response = self.client.post(
            self._url(f"/trading-gateway/products/{product_code}/manual-tasks/preview"),
            json={"account_id": account_id, "mode": mode},
            headers=self._headers(),
        )
        return self._handle(response)

    def create_manual_task(
        self,
        product_code: str,
        account_id: str,
        preview_token: str,
        mode: str = "dry_run",
    ) -> dict[str, Any]:
        response = self.client.post(
            self._url(f"/trading-gateway/products/{product_code}/manual-tasks"),
            json={"account_id": account_id, "mode": mode, "preview_token": preview_token},
            headers=self._headers(),
        )
        return self._handle(response)

    def report_plan(self, task_id: str, plan: dict[str, Any]) -> None:
        response = self.client.post(
            self._url(f"/trading-gateway/tasks/{task_id}/plan"),
            json=plan,
            headers=self._headers(),
        )
        self._handle(response)

    def report_result(self, task_id: str, status: str, payload: dict[str, Any] | ExecutionResult) -> None:
        if isinstance(payload, ExecutionResult):
            body = payload.model_dump(mode="json")
            body["status"] = status
        else:
            body = {**payload, "status": status}
        response = self.client.post(
            self._url(f"/trading-gateway/tasks/{task_id}/result"),
            json=body,
            headers=self._headers(),
        )
        self._handle(response)

    def report_condition_result(
        self,
        source_task_id: str,
        condition_id: str,
        payload: dict[str, Any],
    ) -> None:
        body = {**payload, "source_task_id": source_task_id, "condition_id": condition_id}
        response = self.client.post(
            self._url(f"/trading-gateway/tasks/{source_task_id}/condition-results"),
            json=body,
            headers=self._headers(),
        )
        self._handle(response)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _handle(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise XquantAdapterError(
                f"Xquant API error {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        return response.json()
