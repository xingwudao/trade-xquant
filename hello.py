# -*- coding: utf-8 -*-
"""
QMT xtquant 通路与程序化下单验证脚本。

PowerShell 示例：

  python .\hello.py doctor

  python .\hello.py scan

  python .\hello.py check

  python .\hello.py dry-run `
    --stock-code 600000.SH --side buy --volume 100 `
    --price-type fix --price 1.00

  $env:QMT_ENABLE_REAL_ORDER="1"
  python .\hello.py order --real-order `
    --stock-code 600000.SH --side buy --volume 100 `
    --price-type fix --price 1.00 --cancel-after-order

说明：
  - 默认只查询，不下单。
  - 真实下单必须同时设置环境变量和命令行参数。
  - 建议首次实盘测试使用可撤限价单，并人工确认委托状态。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable


DEFAULT_QMT_PATH = (
    r"C:\Apps\QMT\国金证券QMT交易端\userdata_mini"
)
DEFAULT_ACCOUNT_ID = "replace-with-qmt-account-id"
DEFAULT_SESSION_ID = None

ORDER_SIDE = {
    "buy": 23,
    "sell": 24,
}

PRICE_TYPE = {
    "latest": 5,
    "fix": 11,
    "counterparty": 14,
}


@dataclass(frozen=True)
class OrderIntent:
    stock_code: str
    side: str
    volume: int
    price_type: str
    price: float
    strategy_name: str
    order_remark: str

    @property
    def order_type_value(self) -> int:
        return ORDER_SIDE[self.side]

    @property
    def price_type_value(self) -> int:
        return PRICE_TYPE[self.price_type]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QMT xtquant 查询与程序化下单验证脚本"
    )
    parser.add_argument(
        "--path",
        default=os.getenv("QMT_USERDATA_PATH", DEFAULT_QMT_PATH),
        help="QMT userdata_mini 路径",
    )
    parser.add_argument(
        "--account-id",
        default=os.getenv("QMT_ACCOUNT_ID", DEFAULT_ACCOUNT_ID),
        help="资金账号，可用 QMT_ACCOUNT_ID 覆盖",
    )
    parser.add_argument(
        "--session-id",
        type=int,
        default=get_default_session_id(),
        help="xtquant 会话 ID",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "doctor",
        help="诊断当前 Python 与 xtquant 安装状态",
    )

    scan_parser = subparsers.add_parser(
        "scan",
        help="用多个 session_id 尝试连接 QMT",
    )
    scan_parser.add_argument("--session-start", type=int, default=100)
    scan_parser.add_argument("--session-end", type=int, default=200)

    subparsers.add_parser(
        "check",
        help="只验证连接、订阅、资金、持仓、委托查询",
    )

    add_order_parser(subparsers, "dry-run", real_order=False)
    add_order_parser(subparsers, "order", real_order=True)

    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "check"
    return args


def get_default_session_id() -> int:
    value = os.getenv("QMT_SESSION_ID")
    if value:
        return int(value)
    return int(time.time()) % 2_000_000_000


def add_order_parser(
    subparsers: argparse._SubParsersAction,
    name: str,
    real_order: bool,
) -> None:
    parser = subparsers.add_parser(
        name,
        help="打印委托参数" if not real_order else "发送真实委托",
    )
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--side", choices=sorted(ORDER_SIDE), required=True)
    parser.add_argument("--volume", type=int, required=True)
    parser.add_argument(
        "--price-type",
        choices=sorted(PRICE_TYPE),
        required=True,
    )
    parser.add_argument("--price", type=float, default=0.0)
    parser.add_argument("--strategy-name", default="qmt_permission_test")
    parser.add_argument("--order-remark", default="qmt_permission_test")

    if real_order:
        parser.add_argument(
            "--real-order",
            action="store_true",
            help="确认发送真实委托",
        )
        parser.add_argument(
            "--cancel-after-order",
            action="store_true",
            help="下单后立即尝试撤单",
        )


def ensure_real_order_enabled(args: argparse.Namespace) -> None:
    if args.command != "order":
        return

    env_enabled = os.getenv("QMT_ENABLE_REAL_ORDER") == "1"
    if not args.real_order or not env_enabled:
        raise SystemExit(
            "拒绝实盘下单：必须同时设置 "
            "QMT_ENABLE_REAL_ORDER=1 和 --real-order"
        )


def build_order_intent(args: argparse.Namespace) -> OrderIntent:
    if args.volume <= 0:
        raise SystemExit("--volume 必须大于 0")

    if args.price_type == "fix" and args.price <= 0:
        raise SystemExit("--price-type fix 时 --price 必须大于 0")

    return OrderIntent(
        stock_code=args.stock_code.upper(),
        side=args.side,
        volume=args.volume,
        price_type=args.price_type,
        price=args.price,
        strategy_name=args.strategy_name,
        order_remark=args.order_remark,
    )


def load_xtquant() -> tuple[type[Any], type[Any], type[Any]]:
    try:
        from xtquant.xttrader import (  # type: ignore
            XtQuantTrader,
            XtQuantTraderCallback,
        )
        from xtquant.xttype import StockAccount  # type: ignore
    except ImportError as exc:
        print_environment_diagnostics()
        raise SystemExit(
            "无法导入 xtquant。\n"
            "请在同一个 PowerShell 里执行：\n"
            "  python -m pip install xtquant -i "
            "https://pypi.tuna.tsinghua.edu.cn/simple\n"
            "安装后执行：\n"
            "  python .\\hello.py doctor\n"
            "  python .\\hello.py check\n"
            "如果服务器不能访问外网，请从迅投 xtquant 下载页下载压缩包，"
            "把其中的 xtquant 文件夹放到当前 Python 的 "
            "Lib\\site-packages 目录。"
        ) from exc

    return XtQuantTrader, XtQuantTraderCallback, StockAccount


def print_environment_diagnostics() -> None:
    print("=== Python 环境诊断 ===")
    print(f"  sys.executable : {sys.executable}")
    print(f"  sys.version    : {sys.version.split()[0]}")
    print(f"  cwd            : {os.getcwd()}")
    print("  sys.path 前 8 项:")
    for path in sys.path[:8]:
        print(f"    - {path}")
    print("")


def print_path_diagnostics(path: str) -> None:
    print("=== QMT 路径诊断 ===")
    print(f"  path          : {path}")
    print(f"  exists        : {os.path.isdir(path)}")
    if not os.path.isdir(path):
        print("")
        return

    probe = os.path.join(path, "xtquant_write_probe.txt")
    try:
        with open(probe, "w", encoding="utf-8") as file:
            file.write("ok")
        os.remove(probe)
        print("  writable      : True")
    except OSError as exc:
        print("  writable      : False")
        print(f"  write_error   : {exc}")
    print("")


def run_doctor() -> int:
    print_environment_diagnostics()
    print_path_diagnostics(DEFAULT_QMT_PATH)
    try:
        import xtquant  # type: ignore
        from xtquant.xttrader import XtQuantTrader  # noqa: F401
        from xtquant.xttype import StockAccount  # noqa: F401
    except ImportError as exc:
        print("xtquant 导入失败:", exc)
        print("")
        print("建议 PowerShell 命令:")
        print("  python -m pip --version")
        print("  python -m pip install xtquant -i "
              "https://pypi.tuna.tsinghua.edu.cn/simple")
        print("  python -c \"import xtquant; print(xtquant.__file__)\"")
        return 1

    print("xtquant 导入成功")
    print(f"  xtquant path   : {getattr(xtquant, '__file__', '')}")
    return 0


def run_scan(args: argparse.Namespace) -> int:
    trader_cls, _, _ = load_xtquant()
    print_path_diagnostics(args.path)
    print(
        f"扫描 session_id: {args.session_start} "
        f"到 {args.session_end - 1}"
    )

    for session_id in range(args.session_start, args.session_end):
        print(f"尝试 session_id={session_id}")
        trader = trader_cls(args.path, session_id)
        trader.start()
        result = trader.connect()
        print(f"  connect_result={result}")
        if result == 0:
            print(f"连接成功，后续可使用 --session-id {session_id}")
            return 0

    print("扫描范围内全部连接失败。")
    return 1


def make_callback(callback_base: type[Any]) -> Any:
    class QmtCallback(callback_base):  # type: ignore[misc, valid-type]
        def on_connected(self) -> None:
            print("[callback] QMT connected")

        def on_disconnected(self) -> None:
            print("[callback] QMT disconnected")

        def on_stock_order(self, order: Any) -> None:
            print("[callback] order:", compact_obj(order))

        def on_stock_trade(self, trade: Any) -> None:
            print("[callback] trade:", compact_obj(trade))

        def on_order_error(self, error: Any) -> None:
            print("[callback] order_error:", compact_obj(error))

        def on_cancel_error(self, error: Any) -> None:
            print("[callback] cancel_error:", compact_obj(error))

        def on_order_stock_async_response(self, response: Any) -> None:
            print("[callback] order_response:", compact_obj(response))

        def on_cancel_order_stock_async_response(
            self,
            response: Any,
        ) -> None:
            print("[callback] cancel_response:", compact_obj(response))

    return QmtCallback()


def compact_obj(obj: Any) -> str:
    if obj is None:
        return "None"

    keys = [
        "account_id",
        "stock_code",
        "order_id",
        "order_sysid",
        "order_type",
        "order_volume",
        "price_type",
        "price",
        "traded_volume",
        "order_status",
        "error_id",
        "error_msg",
        "msg",
    ]
    parts = []
    for key in keys:
        if hasattr(obj, key):
            parts.append(f"{key}={getattr(obj, key)}")
    return ", ".join(parts) if parts else repr(obj)


def print_list(title: str, items: Iterable[Any] | None, limit: int = 8) -> None:
    rows = list(items or [])
    print(f"{title}: {len(rows)}")
    for item in rows[:limit]:
        print("  ", compact_obj(item))
    if len(rows) > limit:
        print(f"  ... 还有 {len(rows) - limit} 条")


def connect_trader(args: argparse.Namespace) -> tuple[Any, Any]:
    trader_cls, callback_base, account_cls = load_xtquant()

    print(f"[1/6] 创建 trader, path={args.path}")
    trader = trader_cls(args.path, args.session_id)

    if hasattr(trader, "register_callback"):
        trader.register_callback(make_callback(callback_base))

    print("[2/6] 启动 trader")
    trader.start()

    print("[3/6] 连接 QMT 客户端")
    connect_result = trader.connect()
    print(f"   连接结果: {connect_result}  (0=成功)")
    if connect_result != 0:
        raise SystemExit(
            "连接失败。请确认 QMT 已登录，路径是 userdata_mini；"
            "若失败可改用 userdata 路径重试。"
        )

    account = account_cls(args.account_id)

    print(f"[4/6] 订阅账户 {args.account_id}")
    subscribe_result = trader.subscribe(account)
    print(f"   订阅结果: {subscribe_result}  (0=成功)")
    if subscribe_result != 0:
        raise SystemExit("订阅账户失败，请检查账号和 QMT 交易登录状态。")

    return trader, account


def run_check(trader: Any, account: Any) -> None:
    print("[5/6] 查询账号状态")
    safe_query("账号列表", trader, "query_account_infos")
    safe_query("账号状态", trader, "query_account_status")

    print("[6/6] 查询资金、持仓、当日委托、成交")
    asset = trader.query_stock_asset(account)
    if asset:
        print("=== 账户资金 ===")
        print(f"  总资产    : {getattr(asset, 'total_asset', 0):>15,.2f}")
        print(f"  可用资金  : {getattr(asset, 'cash', 0):>15,.2f}")
        print(f"  持仓市值  : {getattr(asset, 'market_value', 0):>15,.2f}")
        print(f"  冻结资金  : {getattr(asset, 'frozen_cash', 0):>15,.2f}")
    else:
        print("query_stock_asset 返回 None")

    print_list("=== 当前持仓 ===", trader.query_stock_positions(account))
    print_list("=== 当日委托 ===", trader.query_stock_orders(account))
    print_list("=== 当日成交 ===", trader.query_stock_trades(account))


def safe_query(title: str, trader: Any, method_name: str) -> None:
    method = getattr(trader, method_name, None)
    if method is None:
        print(f"{title}: 当前 xtquant 无 {method_name}")
        return

    try:
        result = method()
    except Exception as exc:  # noqa: BLE001 - 打印券商/SDK返回细节
        print(f"{title}: 查询失败: {exc}")
        return

    if isinstance(result, list):
        print_list(title, result)
    else:
        print(f"{title}: {compact_obj(result)}")


def print_order_intent(intent: OrderIntent) -> None:
    print("=== 委托参数 ===")
    print(f"  stock_code   : {intent.stock_code}")
    print(f"  side         : {intent.side} ({intent.order_type_value})")
    print(f"  volume       : {intent.volume}")
    print(f"  price_type   : {intent.price_type} ({intent.price_type_value})")
    print(f"  price        : {intent.price}")
    print(f"  strategy_name: {intent.strategy_name}")
    print(f"  order_remark : {intent.order_remark}")


def place_order(
    trader: Any,
    account: Any,
    intent: OrderIntent,
    cancel_after_order: bool,
) -> int:
    print("[order] 发送真实委托")
    order_id = trader.order_stock(
        account,
        intent.stock_code,
        intent.order_type_value,
        intent.volume,
        intent.price_type_value,
        intent.price,
        intent.strategy_name,
        intent.order_remark,
    )
    print(f"[order] order_id={order_id}")
    if order_id == -1:
        raise SystemExit("下单接口返回 -1，委托失败。")

    time.sleep(2)
    order = trader.query_stock_order(account, order_id)
    print("[order] 委托详情:", compact_obj(order))

    if cancel_after_order:
        print("[order] 尝试撤单")
        cancel_result = trader.cancel_order_stock(account, order_id)
        print(f"[order] cancel_result={cancel_result}  (0=成功)")

    return order_id


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.command == "doctor":
        return run_doctor()

    if args.command == "scan":
        return run_scan(args)

    ensure_real_order_enabled(args)

    if args.command in {"dry-run", "order"}:
        intent = build_order_intent(args)
        print_order_intent(intent)
        if args.command == "dry-run":
            print("dry-run 完成：未连接 QMT，未发送委托。")
            return 0

    trader, account = connect_trader(args)
    run_check(trader, account)

    if args.command == "order":
        place_order(
            trader,
            account,
            intent,
            cancel_after_order=args.cancel_after_order,
        )
        print("实盘委托请求已发送，请在 QMT 委托/成交界面复核。")
    else:
        print("查询验证通过：未发送任何委托。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
