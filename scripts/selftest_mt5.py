from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

# ============================================
# プロジェクトルートを sys.path に追加
# （scripts/ の 1 個上が fxbot ルート）
# ============================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import mt5_client  # noqa: E402


def _get_attr(obj: Any, name: str, default: Any = "(n/a)") -> Any:
    """dict / MT5 の AccountInfo のどちらでも安全に属性を取り出すヘルパー。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def main() -> int:
    print("=== MT5 self test ===")
    print("このスクリプトは、現在の設定タブで選択されている口座プロファイルを使って接続確認を行います。")
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print()

    try:
        # 1) initialize
        print("[1] mt5_client.initialize() ...")
        ok = mt5_client.initialize()
        print(f"    -> initialize() returned: {ok!r}")
        if not ok:
            print("ERROR: MT5 の初期化に失敗しました。")
            print(" - MT5 ターミナルが起動しているか？")
            print(" - 設定タブで選択した口座ID / サーバー / パスワードは正しいか？")
            return 1

        # 2) account_info
        print()
        print("[2] mt5_client.get_account_info() ...")
        info = mt5_client.get_account_info()
        if not info:
            print("ERROR: get_account_info() が None / False を返しました。")
            print("      ログイン情報やサーバー設定を確認してください。")
            return 2

        login = _get_attr(info, "login")
        name = _get_attr(info, "name")
        server = _get_attr(info, "server")
        balance = _get_attr(info, "balance")
        equity = _get_attr(info, "equity")
        trade_mode = _get_attr(info, "trade_mode")

        print("  --- Account Info ---")
        print(f"  login      : {login}")
        print(f"  name       : {name}")
        print(f"  server     : {server}")
        print(f"  balance    : {balance}")
        print(f"  equity     : {equity}")
        print(f"  trade_mode : {trade_mode}")
        print("  --------------------")

        # 3) positions (raw)
        print()
        print("[3] mt5_client.get_positions() ...")
        positions = mt5_client.get_positions()
        n_pos = len(positions) if positions is not None else 0
        print(f"    -> open positions: {n_pos}")
        if positions:
            # 先頭数件だけざっくり表示
            print("    sample positions (up to 5):")
            for i, pos in enumerate(positions[:5]):
                print(f"      [{i}] {pos!r}")

        # 4) positions_df (DataFrame)
        print()
        print("[4] mt5_client.get_positions_df() ...")
        try:
            df = mt5_client.get_positions_df()
        except Exception as e:  # noqa: BLE001
            print(f"    WARN: get_positions_df() で例外が発生しました: {e!r}")
        else:
            if df is None:
                print("    -> DataFrame: None （ポジション無しか、未対応の可能性）")
            else:
                try:
                    print("    -> DataFrame 形式のポジション一覧:")
                    print(df)
                except Exception:
                    print("    -> DataFrame の print 中に例外が出たため簡易表示に切替えます。")
                    print(repr(df))

        print()
        print("=== MT5 self test finished: SUCCESS ===")
        return 0

    except Exception:
        print()
        print("=== MT5 self test crashed ===")
        traceback.print_exc()
        return 99

    finally:
        # shutdown は念のため例外握りつぶしで
        try:
            print()
            print("[*] mt5_client.shutdown() ...")
            mt5_client.shutdown()
            print("    -> shutdown() 完了")
        except Exception as e:  # noqa: BLE001
            print(f"    WARN: shutdown() 中に例外が発生しました: {e!r}")


if __name__ == "__main__":
    raise SystemExit(main())
