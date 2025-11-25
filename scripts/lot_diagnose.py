# scripts/lot_diagnose.py

from app.core.strategy_profile import get_profile
from app.core.mt5_client import TickSpec

def diagnose(profile_name: str = "michibiki_std"):
    profile = get_profile(profile_name)

    print("=== StrategyProfile ===")
    print("name       :", profile.name)
    print("target_ret :", profile.target_monthly_return)
    print("max_monthDD:", profile.max_monthly_dd)
    print("ATR SL mult:", profile.atr_mult_sl)
    print()

    # 仮のMT5前提データ（実際はTradeServiceで取得）
    equity = 1_000_000.0
    tick_spec = TickSpec(0.01, 100.0)  # 1ティック0.01円、損益100円

    atr_list = [0.05, 0.10, 0.20, 0.40, 0.80]

    print("=== ロット診断テーブル ===")
    for atr in atr_list:
        result = profile.compute_lot_size_from_atr(
            equity=equity,
            atr=atr,
            tick_size=tick_spec.tick_size,
            tick_value=tick_spec.tick_value,
            # ★ テスト用に min_lot をかなり小さくする
            min_lot=0.0001,
            max_lot=10.0,
        )

        # sl_price が None の場合もあるのでガードする
        sl_price = getattr(result, "sl_price", None)
        if sl_price is not None:
            sl_pips = sl_price / tick_spec.tick_size
            sl_str = f"{sl_pips:6.1f}"
        else:
            sl_str = "  n/a "

        # LotSizingResult の中身も一応表示（dataclass ならフィールドが全部出る）
        print(result)

        print(
            f"ATR={atr:5.2f} | lot={result.lot:10.6f} | "
            f"risk_pct={result.risk_pct*100:5.2f}% | sl_pips={sl_str}"
        )

def main():
    diagnose()

if __name__ == "__main__":
    main()
