# --- project root on sys.path ---
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# --------------------------------

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
from typing import NoReturn

SYMBOL = os.environ.get("FXBOT_SYMBOL", "USDJPY-")
TIMEFRAME = mt5.TIMEFRAME_M5

# 端末パスを明示したい場合は環境変数 FXBOT_MT5_TERMINAL を使う
# 例: setx FXBOT_MT5_TERMINAL "C:\Program Files\MetaTrader 5\terminal64.exe"
TERM_PATH = os.environ.get("FXBOT_MT5_TERMINAL")

def die(msg: str) -> NoReturn:
    print(msg)
    mt5.shutdown()
    raise SystemExit(1)

def ensure_init() -> None:
    ok = mt5.initialize() if not TERM_PATH else mt5.initialize(path=TERM_PATH)
    if not ok:
        die(f"MT5 initialize() failed: last_error={mt5.last_error()} term_path={TERM_PATH!r}")
    ver = mt5.version()
    print(f"MT5 initialized. version={ver} term_path={TERM_PATH!r}")

def ensure_logged_in() -> None:
    ai = mt5.account_info()
    if ai is None:
        die(f"Not logged in or terminal not ready. last_error={mt5.last_error()}")
    print(f"Account: {ai.login} / {ai.server}")

def ensure_symbol(symbol: str) -> None:
    info = mt5.symbol_info(symbol)
    if info is None:
        die(f"symbol_info({symbol}) is None. last_error={mt5.last_error()}")
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            die(f"symbol_select({symbol}) failed. last_error={mt5.last_error()}")
    # 試しに最新ティックも触っておく
    _ = mt5.symbol_info_tick(symbol)
    print(f"Symbol {symbol} ready (visible={mt5.symbol_info(symbol).visible})")

def try_copy_small(symbol: str, timeframe: int, count: int = 1000) -> int:
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None:
        return 0
    return len(rates)

def export_range(symbol: str, timeframe: int, days: int = 365 * 5) -> str:
    to = datetime.now()
    frm = to - timedelta(days=days)

    # まず小さく取れるか診断
    small = try_copy_small(symbol, timeframe, 1000)
    print(f"diagnostic: copy_rates_from_pos count={small}")

    # --- 安全取得モード ---
    print(f"fetching {symbol} {days}days range in chunks ...")
    chunk_days = 30   # 1か月単位で遡る
    frames = []
    cursor_to = to
    while cursor_to > frm:
        cursor_from = cursor_to - timedelta(days=chunk_days)
        rates = mt5.copy_rates_range(symbol, timeframe, cursor_from, cursor_to)
        if rates is None or len(rates) == 0:
            print(f"chunk {cursor_from.date()}~{cursor_to.date()} => no data (skip)")
        else:
            df = pd.DataFrame(rates)
            frames.append(df)
            print(f"chunk {cursor_from.date()}~{cursor_to.date()} => {len(df)} bars")
        cursor_to = cursor_from

    if not frames:
        die("no data returned even by chunked fetch. Try shorter days or different symbol/timeframe.")

    df = pd.concat(frames).drop_duplicates(subset=["time"]).sort_values("time")
    df["Date"] = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={
        "open": "open", "high": "high", "low": "low", "close": "close",
        "tick_volume": "volume"
    })
    df = df[["Date", "open", "high", "low", "close", "volume"]]
    df["label"] = (df["close"].shift(-1) > df["close"]).map({True: "BUY", False: "SELL"})

    outdir = os.path.join("data", "usdjpy")
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, "USDJPY_M5_mt5.csv")
    df.to_csv(out, index=False)
    print(f"wrote {out} rows:{len(df)}")
    return out


def main() -> None:
    ensure_init()
    ensure_logged_in()
    ensure_symbol(SYMBOL)
    export_range(SYMBOL, TIMEFRAME, days=365*5)
    mt5.shutdown()

if __name__ == "__main__":
    main()
