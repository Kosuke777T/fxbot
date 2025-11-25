from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from app.services.execution_stub import LOG_DIR


def load_lot_records() -> pd.DataFrame:
    """
    LOG_DIR/decisions_*.jsonl から lot_info を集めて DataFrame にする。
    - lot_info が無い行（SKIP など）はスキップ
    - decision.lot_info と top-level lot_info の両方に対応
    """
    print(f"[info] LOG_DIR = {LOG_DIR}")

    files = sorted(LOG_DIR.glob("decisions_*.jsonl"))
    if not files:
        print(f"[warn] no decisions_*.jsonl found in {LOG_DIR}")
        return pd.DataFrame()

    rows: list[dict] = []

    for path in files:
        # ファイル名からシンボル候補（例: decisions_USDJPY.jsonl -> USDJPY）
        default_symbol = path.stem.replace("decisions_", "")
        print(f"[info] reading {path.name}")

        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"[warn] JSON decode error in {path.name}:{line_no}: {e}")
                    continue

                # --- lot_info を探す -----------------------------------
                decision = rec.get("decision")
                lot_info = None

                if isinstance(decision, dict):
                    lot_info = decision.get("lot_info")

                if lot_info is None and "lot_info" in rec:
                    # 念のため top-level lot_info も見る
                    lot_info = rec["lot_info"]

                if not isinstance(lot_info, dict):
                    # ロット計算していないレコード（SKIP など）は飛ばす
                    continue

                lot = lot_info.get("lot")
                if lot is None:
                    # ロットが無ければ意味がないのでスキップ
                    continue

                # --- タイムスタンプ候補をいくつか見る -----------------
                ts = (
                    rec.get("ts")
                    or rec.get("timestamp")
                    or rec.get("time")
                    or lot_info.get("ts")
                )

                # シンボルも rec / lot_info / ファイル名の順で探す
                symbol = (
                    rec.get("symbol")
                    or lot_info.get("symbol")
                    or default_symbol
                )

                side = (
                    lot_info.get("side")
                    if isinstance(lot_info, dict)
                    else None
                )

                rows.append(
                    {
                        "ts": ts,
                        "symbol": symbol,
                        "side": side,
                        "lot": float(lot),
                        "equity": lot_info.get("equity"),
                        "atr": lot_info.get("atr"),
                        "risk_pct": lot_info.get("risk_pct"),
                        "sl_pips": lot_info.get("sl_pips"),
                    }
                )

    if not rows:
        print("[warn] no lot_info entries found in decisions logs.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # ts を datetime に変換（失敗したら NaT）
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")

    return df


def print_summary(df: pd.DataFrame) -> None:
    """
    ロット情報のざっくりサマリを出力。
    """
    print("=== lot_info summary ===")
    print(f"rows: {len(df)}")
    print()

    if "symbol" not in df.columns or df["symbol"].isna().all():
        print("[warn] symbol column is empty, skip symbol-wise summary.")
        return

    by_symbol = df.groupby("symbol")["lot"]
    summary = by_symbol.agg(["count", "mean", "min", "max"]).sort_index()
    print("--- lot size by symbol ---")
    print(summary)
    print()

    # サイド別件数
    if "side" in df.columns and df["side"].notna().any():
        print("--- trade count by symbol × side ---")
        pivot = (
            df.pivot_table(
                index="symbol",
                columns="side",
                values="lot",
                aggfunc="count",
            )
            .fillna(0)
            .astype(int)
        )
        print(pivot)
        print()

    # 分位点（0.1, 0.5, 0.9）
    quant = by_symbol.quantile([0.1, 0.5, 0.9]).unstack()
    print("--- lot quantiles by symbol (0.1, 0.5, 0.9) ---")
    print(quant)
    print()


def plot_lot_timeseries(df: pd.DataFrame) -> None:
    """
    シンボルごとのロット推移を PNG で保存。
    """
    if df.empty:
        return

    outdir = LOG_DIR / "lot_debug"
    outdir.mkdir(exist_ok=True)

    for symbol, g in df.groupby("symbol"):
        g = g.sort_values("ts")

        if "ts" in g.columns and g["ts"].notna().any():
            x = g["ts"]
            xlabel = "time"
        else:
            x = range(len(g))
            xlabel = "index"

        plt.figure()
        plt.plot(x, g["lot"], marker="o")
        plt.xlabel(xlabel)
        plt.ylabel("lot")
        plt.title(f"lot size over time ({symbol})")
        plt.tight_layout()

        out_path = outdir / f"lot_timeseries_{symbol}.png"
        plt.savefig(out_path)
        plt.close()
        print(f"[saved] {out_path}")


def plot_lot_vs_atr(df: pd.DataFrame) -> None:
    """
    ATR と lot の関係を散布図で保存（シンボルごと）。
    """
    if "atr" not in df.columns:
        return

    outdir = LOG_DIR / "lot_debug"
    outdir.mkdir(exist_ok=True)

    for symbol, g in df.groupby("symbol"):
        gg = g.dropna(subset=["atr"])
        if gg.empty:
            continue

        plt.figure()
        plt.scatter(gg["atr"], gg["lot"])
        plt.xlabel("ATR")
        plt.ylabel("lot")
        plt.title(f"lot vs ATR ({symbol})")
        plt.tight_layout()

        out_path = outdir / f"lot_vs_atr_{symbol}.png"
        plt.savefig(out_path)
        plt.close()
        print(f"[saved] {out_path}")


def main() -> None:
    df = load_lot_records()
    if df.empty:
        print("[info] no data to summarize.")
        return

    print("=== head ===")
    print(df.head())
    print()

    print_summary(df)
    plot_lot_timeseries(df)
    plot_lot_vs_atr(df)


if __name__ == "__main__":
    main()
