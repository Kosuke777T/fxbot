# scripts/mt5_smoke.py
#
# 目的:
#   - MetaTrader5 の初期化が成功するか
#   - 現在ログインしている口座情報が取れるか
# を確認するスモークテスト。

import MetaTrader5 as mt5


def main() -> None:
    print("[mt5_smoke] initialize() ...")
    if not mt5.initialize():
        print(f"[mt5_smoke] initialize() FAILED: last_error={mt5.last_error()}")
        print("  -> MT5 が起動しているか、ログイン状態かを確認してください。")
        return

    print("[mt5_smoke] initialize() OK")

    info = mt5.account_info()
    if info is None:
        print("[mt5_smoke] account_info() is None.")
        print("  -> MT5 が起動しているか、デモ口座などにログインしているか確認してください。")
    else:
        print("[mt5_smoke] account_info():")
        print(f"  login   = {info.login}")
        print(f"  name    = {info.name}")
        print(f"  balance = {info.balance}")
        print(f"  equity  = {info.equity}")

    mt5.shutdown()
    print("[mt5_smoke] shutdown() done.")


if __name__ == "__main__":
    main()
