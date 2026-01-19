# app/services/data_guard.py
from __future__ import annotations
import subprocess
import os
import sys
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
    # MT5用シンボル（USDJPY-）とCSV用シンボル（USDJPY）を分離
    mt5_symbol = symbol_tag  # 例: USDJPY-
    csv_symbol_tag = symbol_tag.rstrip("-")  # 例: USDJPY
    out_csv = csv_path(csv_symbol_tag, timeframe, layout)
    need_fetch = True

    if out_csv.exists():
        try:
            df = pd.read_csv(out_csv, parse_dates=["time"])
            if not df.empty:
                has_start = (df["time"].min() <= pd.Timestamp(start_date))
                # end_date は当日23:59:59基準で比較（日付00:00基準の誤判定を回避）
                end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
                has_end   = (df["time"].max() >= end_ts)
                need_fetch = not (has_start and has_end)
        except Exception:
            need_fetch = True

    if need_fetch:
        # make_csv_from_mt5 を呼ぶ（不足分は自動追記）
        # --symbol には MT5用シンボル（元の symbol_tag）を渡す（内部で resolve_symbol される）
        cmd = [
            str((PROJECT_ROOT / "scripts" / "make_csv_from_mt5.py").resolve()),
            "--symbol", mt5_symbol,
            "--timeframes", timeframe,
            "--start", start_date,
            "--end", end_date,
            "--layout", layout,
            "--env", env,
        ]
        # Windows では python 経由で実行
        run_env = dict(os.environ)
        # Ensure project package "app" is importable when called as a script
        run_env["PYTHONPATH"] = str(PROJECT_ROOT)
        proc = subprocess.run(
            [sys.executable, *cmd],
            cwd=str(PROJECT_ROOT),
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # cp932混入耐性（Windows/MT5出力対応）
        )
        if proc.returncode != 0:
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            msg = (
                f"make_csv_from_mt5 failed (rc={proc.returncode})\n"
                f"cmd={[sys.executable, *cmd]}\n"
                f"stdout=\n{out[-2000:]}\n"
                f"stderr=\n{err[-4000:]}\n"
            )
            raise RuntimeError(msg)
        else:
            # 成功時も観測ログ（symbol not found 等のケースを潰す）
            # 末尾 N行表示（例: 80行）で df_new.time dtype(before) 等を確実に観測できるようにする
            stdout_lines = (proc.stdout or "").strip().splitlines()
            stderr_lines = (proc.stderr or "").strip().splitlines()
            out_tail = "\n".join(stdout_lines[-80:]) if len(stdout_lines) > 80 else "\n".join(stdout_lines)
            err_tail = "\n".join(stderr_lines[-80:]) if len(stderr_lines) > 80 else "\n".join(stderr_lines)
            from loguru import logger
            logger.info(
                "[data_guard] make_csv_from_mt5 success: stdout_tail (last 80 lines)=\n{} stderr_tail (last 80 lines)=\n{}",
                out_tail,
                err_tail,
            )

    # 最終チェック
    if not out_csv.exists():
        raise FileNotFoundError(f"CSV not found after update: {out_csv}")
    return out_csv
