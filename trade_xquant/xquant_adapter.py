from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from trade_xquant.models import ExecutionResult, RebalanceTask, TargetPosition, normalize_symbol


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

    def fetch_latest_signal_task(
        self,
        product_code: str,
        account_id: str,
        mode: str = "dry_run",
        cash_buffer_ratio: float = 0.002,
    ) -> RebalanceTask | None:
        response = self.client.get(
            self._url(f"/internal/products/{product_code}/signal/latest"),
            headers=self._headers(),
        )
        if response.status_code == 404:
            response = self.client.get(
                self._url(f"/products/{product_code}/signal/latest"),
                headers=self._headers(),
            )
        if response.status_code == 404:
            return None
        data = self._handle(response)
        if not data:
            return None

        raw_weights = data.get("target_weights") or data.get("weights") or {}
        targets = [
            TargetPosition(symbol=normalize_symbol(symbol), target_weight=float(weight))
            for symbol, weight in raw_weights.items()
            if symbol.upper() != "CASH" and float(weight) > 0
        ]
        if not targets:
            return None

        as_of_date = str(data.get("as_of_date") or data.get("effective_date") or datetime.now().date())
        created_at = _date_to_datetime(as_of_date, hour=9, minute=35)
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        expires_at = _date_to_datetime(str(data.get("effective_date") or as_of_date), hour=14, minute=50)
        if expires_at <= now:
            expires_at = now + timedelta(days=1)

        return RebalanceTask(
            task_id=f"signal_{product_code}_{as_of_date}",
            portfolio_id=product_code,
            account_id=account_id,
            mode=mode,  # type: ignore[arg-type]
            signal_as_of_date=data.get("as_of_date"),
            signal_effective_date=data.get("effective_date"),
            created_at=created_at,
            expires_at=expires_at,
            cash_buffer_ratio=cash_buffer_ratio,
            targets=targets,
            raw=data,
        )

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
    ) -> dict[str, Any]:
        response = self.client.post(
            self._url(f"/trading-gateway/accounts/{account_id}/heartbeat"),
            json={
                "client_version": client_version,
                "hostname": hostname,
                "qmt_connected": qmt_connected,
                "xtquant_importable": xtquant_importable,
                "last_error": last_error,
            },
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


def _date_to_datetime(value: str, hour: int, minute: int) -> datetime:
    if "T" in value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    date_part = datetime.fromisoformat(value).date()
    return datetime(
        date_part.year,
        date_part.month,
        date_part.day,
        hour,
        minute,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
