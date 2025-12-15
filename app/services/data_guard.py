# app/services/data_guard.py
from __future__ import annotations
import subprocess
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # app/services/ → app → プロジェクトルート
DATA_DIR = PROJECT_ROOT / "data"

def csv_path(symbol_tag: str, timeframe: str, layout: str="per-symbol") -> Path:
    """
    symbol_tag は接尾辞なし（例: USDJPY）
    layout: "flat" or "per-symbol"
    """
    if layout == "per-symbol":
        return DATA_DIR / symbol_tag / "ohlcv" / f"{symbol_tag}_{timeframe}.csv"
    return DATA_DIR / f"{symbol_tag}_{timeframe}.csv"

def ensure_data(symbol_tag: str, timeframe: str, start_date: str, end_date: str,
                env: str="laptop", layout: str="per-symbol") -> Path:
    """
    指定の [start_date, end_date] を満たすCSVが存在するか確認し、足りなければ scripts.make_csv_from_mt5 を呼んで追記する。
    戻り値: CSVのフルパス
    """
    # MT5用シンボル（USDJPY-）をCSV用シンボル（USDJPY）に正規化
    symbol_tag = symbol_tag.rstrip("-")
    out_csv = csv_path(symbol_tag, timeframe, layout)
    need_fetch = True

    if out_csv.exists():
        try:
            df = pd.read_csv(out_csv, parse_dates=["time"])
            if not df.empty:
                has_start = (df["time"].min() <= pd.Timestamp(start_date))
                has_end   = (df["time"].max() >= pd.Timestamp(end_date))
                need_fetch = not (has_start and has_end)
        except Exception:
            need_fetch = True

    if need_fetch:
        # make_csv_from_mt5 を呼ぶ（不足分は自動追記）
        cmd = [
            str((PROJECT_ROOT / "scripts" / "make_csv_from_mt5.py").resolve()),
            "--symbol", symbol_tag,
            "--timeframes", timeframe,
            "--start", start_date,
            "--layout", layout,
            "--env", env,
        ]
        # Windows では python 経由で実行
        subprocess.check_call(["python", *cmd], cwd=str(PROJECT_ROOT))

    # 最終チェック
    if not out_csv.exists():
        raise FileNotFoundError(f"CSV not found after update: {out_csv}")
    return out_csv
