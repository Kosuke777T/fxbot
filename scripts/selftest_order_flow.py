from __future__ import annotations

import os
import sys
import time
from typing import Any

import MetaTrader5 as MT5  # type: ignore[import]
from core.config import cfg  # noqa: F401  # ensure configuration loads
from app.core.mt5_client import MT5Client


def _get_env(name: str) -> str:
    v = os.getenv(name, "")
    if not v:
        raise SystemExit(
            f"環境変数 {name} が設定されていません。\n"
            "GUI の設定タブで口座プロファイルを選択してから、再度このスクリプトを実行してください。"
        )
    return v


def _get_attr(obj: Any, name: str, default: Any = "(n/a)") -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _wait_for_position(ticket: int, timeout_sec: float = 10.0, interval: float = 0.5) -> bool:
    """指定 ticket のポジションが MT5.positions_get で見えるようになるまで待つ。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        pos = MT5.positions_get(ticket=ticket)
        if pos:
            return True
        time.sleep(interval)
    return False


def main() -> int:
    print("=== MT5 order flow self test ===")
    print("このスクリプトは DEMO 口座で、")
    print("  1) 成行 BUY で 0.01 lot エントリー")
    print("  2) 約定確認")
    print("  3) すぐ成行クローズ")
    print("を行います。必ずデモ口座で実行してください。")
    print()

    # --- 認証情報 & テストパラメータ ---
    login = int(_get_env("MT5_LOGIN"))
    password = _get_env("MT5_PASSWORD")
    server = _get_env("MT5_SERVER")

    symbol = os.getenv("FXBOT_TEST_SYMBOL", "USDJPY-")
    lot = float(os.getenv("FXBOT_TEST_LOT", "0.01"))

    print(f"[config] login={login} server={server} symbol={symbol} lot={lot}")
    print()

    client = MT5Client(login=login, password=password, server=server)

    try:
        # 1) initialize
        print("[1] client.initialize()")
        if not client.initialize():
            print("    -> initialize() 失敗")
            return 1
        print("    -> OK")

        # 2) login_account
        print("[2] client.login_account()")
        if not client.login_account():
            print("    -> login_account() 失敗")
            return 2
        print("    -> OK")

        # 3) account_info 表示
        info = MT5.account_info()
        print()
        print("[3] account_info()")
        if info is None:
            print("    -> account_info() が None")
        else:
            print(f"    login   : {_get_attr(info, 'login')}")
            print(f"    name    : {_get_attr(info, 'name')}")
            print(f"    balance : {_get_attr(info, 'balance')}")
            print(f"    equity  : {_get_attr(info, 'equity')}")

        # 4) 成行 BUY エントリー
        print()
        print(f"[4] order_send() で BUY エントリー: symbol={symbol}, lot={lot}")
        order_result = client.order_send(symbol=symbol, order_type="BUY", lot=lot)
        ticket = (order_result or (None, None, None))[0]
        if not ticket:
            print("    -> order_send() が ticket を返しませんでした。")
            return 3
        print(f"    -> 発注成功: ticket={ticket}")

        # 5) ポジション出現待ち
        print()
        print("[5] positions_get(ticket) でポジション出現を待機中...")
        if not _wait_for_position(ticket):
            print("    -> タイムアウト: ポジションが見つかりません。")
            return 4
        print("    -> ポジション検出 OK")

        # 6) 成行クローズ
        print()
        print("[6] close_position() で即クローズ")
        ok = client.close_position(ticket=ticket, symbol=symbol)
        print(f"    -> close_position() returned {ok!r}")
        if not ok:
            print("    -> クローズ失敗")
            return 5

        # 7) クローズ後の確認
        time.sleep(1.0)
        remaining = MT5.positions_get(ticket=ticket)
        print()
        print(f"[7] close 後の positions_get(ticket={ticket}) = {remaining}")
        if remaining:
            print("    WARN: クローズ後もポジションが残っています。")
            return 6

        print()
        print("=== MT5 order flow self test finished: SUCCESS ===")
        return 0

    finally:
        print()
        print("[*] client.shutdown()")
        try:
            client.shutdown()
            print("    -> shutdown() 完了")
        except Exception as e:  # noqa: BLE001
            print(f"    WARN: shutdown() 中に例外発生: {e!r}")


if __name__ == "__main__":
    raise SystemExit(main())
