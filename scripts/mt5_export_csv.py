# scripts/mt5_export_csv.py
# MT5のローカルヒストリから rates を取得→ data/{SYMBOL}_{TF}.csv に保存
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
from app.core.symbol_map import resolve_symbol

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", default="M5", help="M1/M5/M15/M30/H1/H4/D1")
    ap.add_argument("--days", type=int, default=365*2, help="過去n日を取得")
    args = ap.parse_args()

    import MetaTrader5 as mt5  # pip install MetaTrader5

    if not mt5.initialize():
        raise SystemExit("MT5 initialize failed")

    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1
    }
    tf = tf_map.get(args.timeframe.upper(), mt5.TIMEFRAME_M5)

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=args.days)

    rates = mt5.copy_rates_range(resolve_symbol(args.symbol), tf, utc_from, utc_to)
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise SystemExit("no rates from MT5")

    df = pd.DataFrame(rates)
    # MT5の time はunix秒
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(columns={"real_volume":"tick_volume"}, inplace=True)

    out_dir = Path(__file__).resolve().parents[1] / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.symbol.upper()}_{args.timeframe.upper()}.csv"
    df[["time","open","high","low","close","tick_volume"]].to_csv(out, index=False)
    print(f"wrote: {out} rows={len(df)}")

if __name__ == "__main__":
    main()

