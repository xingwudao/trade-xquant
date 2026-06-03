from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade-xquant")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", dest="config_sub", default=None)
    common.add_argument("--verbose", dest="verbose_sub", action="store_true")
    sub.add_parser("doctor", parents=[common])
    login = sub.add_parser("login", parents=[common])
    login.add_argument("--phone")
    login.add_argument("--email")
    login.add_argument("--otp")
    login.add_argument("--send-otp", action="store_true")
    register = sub.add_parser("register-account", parents=[common])
    register.add_argument("--broker", default="guojin")
    register.add_argument("--client-type", default="qmt")
    register.add_argument("--display-name")
    register.add_argument("--default-mode", choices=["dry_run", "real"], default="dry_run")
    heartbeat = sub.add_parser("heartbeat", parents=[common])
    heartbeat.add_argument("--qmt-connected", action="store_true")
    heartbeat.add_argument("--last-error")
    sub.add_parser("check-qmt", parents=[common])
    poll = sub.add_parser("poll-once", parents=[common])
    poll.add_argument("--task-id")
    sub.add_parser("daemon", parents=[common])
    dry = sub.add_parser("dry-run", parents=[common])
    dry.add_argument("--task-id")
    mock = sub.add_parser("mock-run", parents=[common])
    mock.add_argument("--task-id")
    sync = sub.add_parser("sync-results", parents=[common])
    sync.add_argument("--task-id")
    sync.add_argument("--status", choices=["all", "submitted", "success", "failed", "partial"], default="all")
    sub.add_parser("show-status", parents=[common])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.config = args.config_sub or args.config
    args.verbose = bool(args.verbose or args.verbose_sub)
    if args.command == "doctor":
        return doctor(args.config)
    if args.command == "login":
        result = login_command(
            config_path=args.config,
            phone=args.phone,
            email=args.email,
            otp=args.otp,
            send_otp=args.send_otp,
        )
        print_json(result)
        return 0
    if args.command == "register-account":
        result = register_account_command(
            config_path=args.config,
            broker=args.broker,
            client_type=args.client_type,
            display_name=args.display_name,
            default_mode=args.default_mode,
        )
        print_json(result)
        return 0
    if args.command == "heartbeat":
        result = heartbeat_command(
            config_path=args.config,
            qmt_connected=args.qmt_connected,
            last_error=args.last_error,
        )
        print_json(result)
        return 0

    from trade_xquant.config import load_settings
    from trade_xquant.logging_config import configure_logging

    settings = load_settings(args.config)
    configure_logging(settings.runtime.log_path, args.verbose)

    if args.command == "check-qmt":
        from trade_xquant.qmt_adapter import QmtAdapter

        try:
            print_json(QmtAdapter(settings.qmt).check_connection())
            return 0
        except Exception as exc:  # noqa: BLE001 - CLI should show actionable diagnostics
            print_json({"ok": False, "error": str(exc)})
            return 1
    if args.command == "poll-once":
        from trade_xquant.daemon import GatewayService

        return _run_gateway_command(lambda: GatewayService(settings).poll_once(task_id=args.task_id))
    if args.command == "dry-run":
        from trade_xquant.daemon import GatewayService

        return _run_gateway_command(
            lambda: GatewayService(settings).poll_once(force_dry_run=True, task_id=args.task_id)
        )
    if args.command == "mock-run":
        from trade_xquant.daemon import GatewayService

        settings.runtime.broker_adapter = "mock"
        settings.runtime.mock_submit_dry_run_orders = True
        settings.runtime.simulate_real_orders = True
        return _run_gateway_command(
            lambda: GatewayService(settings).poll_once(force_dry_run=True, task_id=args.task_id)
        )
    if args.command == "sync-results":
        from trade_xquant.daemon import GatewayService

        return _run_gateway_command(
            lambda: GatewayService(settings).sync_results(task_id=args.task_id, status=args.status)
        )
    if args.command == "daemon":
        from trade_xquant.daemon import GatewayService

        GatewayService(settings).run_forever()
        return 0
    if args.command == "show-status":
        from trade_xquant.storage import Storage

        storage = Storage(settings.runtime.db_path)
        storage.initialize()
        print_json(storage.status_summary())
        return 0
    raise SystemExit(f"unknown command: {args.command}")


def doctor(config_path: str) -> int:
    result = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "config_exists": Path(config_path).exists(),
        "xtquant_importable": False,
    }
    try:
        import xtquant  # type: ignore

        result["xtquant_importable"] = True
        result["xtquant_path"] = getattr(xtquant, "__file__", "")
    except ImportError as exc:
        result["xtquant_error"] = str(exc)
    print_json(result)
    return 0 if result["xtquant_importable"] else 1


def login_command(
    config_path: str,
    phone: str | None,
    email: str | None,
    otp: str | None,
    send_otp: bool,
    client=None,
) -> dict[str, str | bool | None]:
    from trade_xquant.auth import TokenStore, token_path_for_config
    from trade_xquant.config import load_settings
    from trade_xquant.xquant_adapter import XquantAdapter

    if bool(phone) == bool(email):
        raise SystemExit("login requires exactly one of --phone or --email")

    settings = load_settings(config_path)
    adapter = XquantAdapter(
        settings.xquant.base_url,
        timeout_seconds=settings.xquant.timeout_seconds,
        trust_env=settings.xquant.trust_env,
        client=client,
    )
    channel = "phone" if phone else "email"
    method = "phone_otp" if phone else "email_otp"

    otp_response = None
    if send_otp or not otp:
        otp_response = adapter.send_otp(channel=channel, phone=phone, email=email)
        if not otp:
            exposed = otp_response.get("otp") if isinstance(otp_response, dict) else None
            if exposed:
                print(f"OTP: {exposed}")
            otp = input("Enter Xquant OTP: ").strip()

    token_response = adapter.login(method=method, phone=phone, email=email, otp=otp)
    token_path = token_path_for_config(config_path)
    TokenStore(token_path).save(
        access_token=str(token_response["access_token"]),
        token_type=str(token_response.get("token_type", "bearer")),
    )
    return {
        "ok": True,
        "token_path": str(token_path),
        "token_type": str(token_response.get("token_type", "bearer")),
        "otp_sent": bool(otp_response),
    }


def register_account_command(
    config_path: str,
    broker: str,
    client_type: str,
    display_name: str | None,
    default_mode: str,
    client=None,
):
    from trade_xquant.config import load_settings
    from trade_xquant.xquant_adapter import XquantAdapter

    settings = load_settings(config_path)
    adapter = XquantAdapter(
        settings.xquant.base_url,
        api_token=settings.xquant.api_token,
        timeout_seconds=settings.xquant.timeout_seconds,
        trust_env=settings.xquant.trust_env,
        client=client,
    )
    return adapter.register_account(
        account_id=settings.qmt.account_id,
        broker=broker,
        client_type=client_type,
        display_name=display_name,
        default_mode=default_mode,
        risk_profile={
            "max_turnover_ratio": settings.risk.max_turnover_ratio,
            "max_single_order_amount": settings.risk.max_single_order_amount,
            "min_order_amount": settings.risk.min_order_amount,
            "cash_buffer_ratio": settings.risk.cash_buffer_ratio,
        },
    )


def heartbeat_command(
    config_path: str,
    qmt_connected: bool,
    last_error: str | None,
    client=None,
    broker=None,
):
    import socket

    from trade_xquant import __version__
    from trade_xquant.config import load_settings
    from trade_xquant.daemon import account_result_snapshot
    from trade_xquant.xquant_adapter import XquantAdapter

    settings = load_settings(config_path)
    adapter = XquantAdapter(
        settings.xquant.base_url,
        api_token=settings.xquant.api_token,
        timeout_seconds=settings.xquant.timeout_seconds,
        trust_env=settings.xquant.trust_env,
        client=client,
    )
    try:
        import xtquant  # noqa: F401

        xtquant_importable = True
    except ImportError:
        xtquant_importable = False
    snapshot: dict | None = None
    if qmt_connected:
        try:
            broker = broker or _build_broker_adapter(settings)
            broker.connect()
            account = broker.get_account_snapshot()
            positions = broker.get_positions()
            symbols = sorted({position.symbol for position in positions})
            prices = broker.get_prices(symbols) if symbols else {}
            snapshot = account_result_snapshot(account, positions, prices)
        except Exception as exc:  # noqa: BLE001 - heartbeat should still reach Xquant
            last_error = _append_last_error(last_error, f"heartbeat snapshot failed: {exc}")
    return adapter.heartbeat(
        account_id=settings.qmt.account_id,
        client_version=__version__,
        hostname=socket.gethostname(),
        qmt_connected=qmt_connected,
        xtquant_importable=xtquant_importable,
        last_error=last_error,
        cash=snapshot["cash"] if snapshot else None,
        total_asset=snapshot["total_asset"] if snapshot else None,
        holdings=snapshot["holdings"] if snapshot else None,
    )


def _build_broker_adapter(settings):
    from trade_xquant.mock_qmt_adapter import MockBrokerAdapter
    from trade_xquant.qmt_adapter import QmtAdapter

    if settings.runtime.broker_adapter == "mock":
        return MockBrokerAdapter(
            account_id=settings.qmt.account_id,
            total_asset=settings.runtime.mock_total_asset,
            cash=settings.runtime.mock_cash,
            prices=settings.runtime.mock_prices,
            order_behavior=settings.runtime.mock_order_behavior,
            partial_fill_ratio=settings.runtime.mock_partial_fill_ratio,
        )
    if settings.runtime.broker_adapter == "qmt":
        return QmtAdapter(settings.qmt)
    raise ValueError("runtime.broker_adapter must be 'qmt' or 'mock'")


def _append_last_error(last_error: str | None, error: str) -> str:
    return f"{last_error}; {error}" if last_error else error


def _run_gateway_command(callback) -> int:
    try:
        print_json(callback())
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should show actionable diagnostics
        status_code = getattr(exc, "status_code", None)
        hint = getattr(exc, "hint", None)
        if status_code == 404:
            hint = (
                "Xquant server does not expose /trading-gateway/tasks yet. "
                "Deploy the trading-gateway API contract before polling tasks."
            )
        payload = {"ok": False, "error": str(exc), "status_code": status_code, "hint": hint}
        results = getattr(exc, "results", None)
        if results is not None:
            payload["results"] = results
        print_json(payload)
        return 1


def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
