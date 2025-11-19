# tools/backtest_run.py
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# --- プロジェクトルートを sys.path に追加してから app.* を import する ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.strategies.ai_strategy import (
    build_features,
    load_active_model,
    predict_signals,
    trades_from_signals,
)

LOG_DIR = PROJECT_ROOT / "logs" / "backtest"

# === equity utils ===


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    direction: int  # +1 long, -1 short
    profit_jpy: float


def equity_from_bnh(df: pd.DataFrame, capital: float) -> pd.Series:
    """
    Buy&Hold（現物1倍）相当の指数エクイティ。close/close0でスケール。
    """
    close = df["close"].astype(float)
    idx = close / close.iloc[0]
    return capital * idx


def trades_from_signal_series(
    df: pd.DataFrame,
    sig: pd.Series,
    lot: float = 0.1,
    contract_size: int = 100_000,
) -> list[Trade]:
    """
    signal（1/-1/0）からフリップ方式でトレード列を作る。
    - signal が 1→ロング保有、-1→ショート保有、0→ノーポジ
    - signal が変わった時点で前ポジをクローズ→新ポジを建てる
    - JPYペアを想定：損益[JPY] = (exit - entry) * direction * lot * contract_size
    """
    sig = sig.astype(int).reindex(df.index).fillna(0)
    close = df["close"].astype(float)
    times = pd.to_datetime(df["time"])

    cur_dir = 0
    cur_price: float | None = None
    cur_time: pd.Timestamp | None = None
    out: list[Trade] = []

    for t, px, s in zip(times, close, sig):
        s = int(s)
        if cur_dir == 0:
            if s in (1, -1):
                cur_dir = s
                cur_price = px
                cur_time = t
        else:
            if s == cur_dir:
                continue
            # 方向が変わった/0になった → クローズ
            profit = (px - cur_price) * cur_dir * lot * contract_size  # type: ignore[arg-type]
            out.append(Trade(cur_time, cur_price, t, px, cur_dir, profit))  # type: ignore[arg-type]
            cur_dir = 0
            cur_price = None
            cur_time = None
            # 新規に建て直す（0でなければ）
            if s in (1, -1):
                cur_dir = s
                cur_price = px
                cur_time = t

    # 終端でオープン中なら、最終値でクローズしてしまう
    if cur_dir != 0 and cur_price is not None and cur_time is not None:
        px = close.iloc[-1]
        t = times.iloc[-1]
        profit = (px - cur_price) * cur_dir * lot * contract_size
        out.append(Trade(cur_time, cur_price, t, px, cur_dir, profit))

    return out


def equity_from_trades(
    df: pd.DataFrame, trades: list[Trade], capital: float
) -> pd.Series:
    """
    トレード配列からエクイティ曲線を作る（逐次加算）。
    """
    eq = pd.Series(capital, index=pd.to_datetime(df["time"]))
    cum = capital
    i = 0
    for tr in trades:
        # 成立時刻で損益を反映
        while i < len(eq.index) and eq.index[i] <= tr.exit_time:
            if eq.index[i] == tr.exit_time:
                cum += tr.profit_jpy
            eq.iloc[i] = cum
            i += 1
    # 以降もラスト値で埋める
    while i < len(eq.index):
        eq.iloc[i] = cum
        i += 1
    return eq


def equity_from_trade_df(
    df_ohlcv: pd.DataFrame, trades_df: pd.DataFrame, capital: float
) -> pd.Series:
    """
    trades_df 形式（DataFrame）から全バーに展開したエクイティ曲線を作る。
    必須カラム: exit_time, pnl
    任意: entry_time, entry_price, exit_price, direction（無くてもOK）
    """
    if trades_df is None or trades_df.empty:
        # 取引なしならフラット
        idx = pd.to_datetime(df_ohlcv["time"])
        return pd.Series(capital, index=idx)

    td = trades_df.copy()

    # 時刻カラムを時系列化（存在するものだけ）
    for col in ("entry_time", "exit_time"):
        if col in td.columns:
            td[col] = pd.to_datetime(td[col], errors="coerce")

    if "exit_time" not in td.columns:
        raise ValueError("trades_df に exit_time 列が必要です。")

    # pnl は数値化
    td["pnl"] = pd.to_numeric(td.get("pnl", 0.0), errors="coerce").fillna(0.0)

    # exit_time でグルーピングして、同時決済があれば合算
    pnl_by_exit = td.groupby("exit_time")["pnl"].sum().sort_index()

    # 全バーへ展開：exit_time のバーでのみ損益を加算、以降は前値でFFill
    idx = pd.to_datetime(df_ohlcv["time"])
    eq = pd.Series(capital, index=idx)
    cum = capital
    i = 0
    exit_times = pnl_by_exit.index.to_list()
    k = 0

    while i < len(idx):
        t = idx[i]
        # このバーの exit_time に決済があればすべて加算
        while k < len(exit_times) and exit_times[k] <= t:
            cum += float(pnl_by_exit.iloc[k])
            k += 1
        eq.iloc[i] = cum
        i += 1

    return eq


def to_equity(close: pd.Series, capital: float = 100000.0) -> pd.DataFrame:
    close = close.astype(float)
    ret = close.pct_change().fillna(0.0)
    eq = (1.0 + ret).cumprod() * capital
    return pd.DataFrame({"time": close.index, "equity": eq.values})


def _max_consecutive(x: pd.Series, val: int) -> int:
    # 最大連続カウント（val=1を数える）
    c = 0
    m = 0
    for v in x:
        if v == val:
            c += 1
            m = max(m, c)
        else:
            c = 0
    return m


def _dd_duration_max(eq: pd.Series) -> int:
    """ドローダウン期間の最大日数を算出。時系列がintならスキップする。"""
    peak = -np.inf
    last_peak_time: pd.Timestamp | None = None
    max_days = 0
    for t, v in eq.items():
        # t が datetime でない場合は飛ばす
        if not hasattr(t, "to_pydatetime") and not hasattr(t, "year"):
            continue
        if v > peak:
            peak = v
            last_peak_time = pd.Timestamp(t)
        elif last_peak_time is not None:
            d = (pd.Timestamp(t) - last_peak_time).days
            max_days = max(max_days, d)
    return int(max_days)


def metrics_from_equity(eq: pd.Series) -> dict:
    ret = eq.pct_change().fillna(0.0)
    total = eq.iloc[-1] / eq.iloc[0] - 1.0
    dd = (eq / eq.cummax() - 1.0).min()
    sharpe = (ret.mean() / (ret.std() + 1e-12)) * np.sqrt(
        252 * 24 * 12
    )  # M5相当の便宜スケール
    return {
        "start_equity": float(eq.iloc[0]),
        "end_equity": float(eq.iloc[-1]),
        "total_return": float(total),
        "max_drawdown": float(dd),
        "sharpe_like": float(sharpe),
        "bars": int(len(eq)),
        "max_dd_days": _dd_duration_max(eq),
    }


def monthly_returns_from_equity(eq_df: pd.DataFrame) -> pd.DataFrame:
    df = eq_df.copy()
    df = df.set_index(pd.to_datetime(df["time"]))
    m = df["equity"].resample("ME").last().pct_change().dropna()
    out = m.to_frame(name="m_return")
    out["year"] = out.index.year
    out["month"] = out.index.month
    return out.reset_index(drop=True)


def trades_from_buyhold(df: pd.DataFrame, capital: float) -> pd.DataFrame:
    # テンプレ：開始→終了の単一トレード（将来は戦略で複数トレードに差し替え）
    if df.empty:
        return pd.DataFrame(
            columns=[
                "entry_time",
                "exit_time",
                "pnl",
                "holding_bars",
                "holding_days",
                "win",
            ]
        )
    entry = df["time"].iloc[0]
    exit_ = df["time"].iloc[-1]
    close = df["close"].astype(float)
    ret = (close.iloc[-1] / close.iloc[0]) - 1.0
    pnl = capital * ret
    holding_bars = len(df)
    holding_days = (pd.Timestamp(exit_) - pd.Timestamp(entry)).days
    win = int(pnl > 0)
    return pd.DataFrame(
        [
            {
                "entry_time": entry,
                "exit_time": exit_,
                "pnl": float(pnl),
                "holding_bars": int(holding_bars),
                "holding_days": int(holding_days),
                "win": win,
            }
        ]
    )


def trade_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "profit_factor": 0.0,
            "avg_holding_bars": 0.0,
            "avg_holding_days": 0.0,
            "max_consec_win": 0,
            "max_consec_loss": 0,
        }
    wins = trades["pnl"] > 0
    sum_win = trades.loc[wins, "pnl"].sum()
    sum_loss_abs = (-trades.loc[~wins, "pnl"]).clip(lower=0).sum()
    pf = (
        float(sum_win / sum_loss_abs)
        if sum_loss_abs > 0
        else float("inf") if sum_win > 0 else 0.0
    )

    seq = wins.astype(int)
    consec_win = _max_consecutive(seq, 1)
    consec_loss = _max_consecutive(1 - seq, 1)

    return {
        "trades": int(len(trades)),
        "win_rate": float(wins.mean()) if len(trades) else 0.0,
        "avg_pnl": float(trades["pnl"].mean()) if len(trades) else 0.0,
        "profit_factor": pf,
        "avg_holding_bars": (
            float(trades["holding_bars"].mean()) if len(trades) else 0.0
        ),
        "avg_holding_days": (
            float(trades["holding_days"].mean()) if len(trades) else 0.0
        ),
        "max_consec_win": int(consec_win),
        "max_consec_loss": int(consec_loss),
    }


def slice_period(
    df: pd.DataFrame, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """
    指定期間で DataFrame をスライスする。
    start / end のどちらか、または両方が None の場合は、その条件をスキップする。
    両方 None の場合は全期間を返す。
    """
    if start is None and end is None:
        return df.reset_index(drop=True)

    m = pd.Series(True, index=df.index)
    if start is not None:
        m &= df["time"] >= pd.Timestamp(start)
    if end is not None:
        m &= df["time"] <= pd.Timestamp(end)
    return df.loc[m].reset_index(drop=True)


def run_backtest(
    data_csv: Path,
    start: str | None,
    end: str | None,
    capital: float,
    out_dir: Path,
) -> Path:
    print("[bt] start", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[bt] read_csv {data_csv}", flush=True)
    df = pd.read_csv(data_csv, parse_dates=["time"])

    tag_start = start or "ALL"
    tag_end = end or "ALL"
    print(f"[bt] slice {tag_start} .. {tag_end}", flush=True)
    df = slice_period(df, start, end)
    if df.empty:
        raise RuntimeError("No data in the requested period.")
    close = df["close"]
    close.index = df["time"]

    print("[bt] equity compute", flush=True)
    eq_df = to_equity(close, capital)
    eq_df["signal"] = 0  # Buy&Holdなのでシグナル無し
    eq_csv = out_dir / "equity_curve.csv"
    print(f"[bt] write equity {eq_csv}", flush=True)
    eq_df.to_csv(eq_csv, index=False)

    # 月次損益
    mr = monthly_returns_from_equity(eq_df)
    mr.to_csv(out_dir / "monthly_returns.csv", index=False)

    # 仮トレード（Buy&Hold）
    trades = trades_from_buyhold(df, capital)
    trades.to_csv(out_dir / "trades.csv", index=False)

    # メトリクス
    base = metrics_from_equity(eq_df["equity"])
    tmet = trade_metrics(trades)
    base.update(tmet)
    (out_dir / "metrics.json").write_text(
        json.dumps(base, ensure_ascii=False, indent=2)
    )

    print("[bt] done", flush=True)
    return eq_csv


def run_wfo(
    data_csv: Path,
    start: str | None,
    end: str | None,
    capital: float,
    out_dir: Path,
    train_ratio: float = 0.7,
) -> Path:
    print("[wfo] start", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[wfo] read_csv {data_csv}", flush=True)
    df = pd.read_csv(data_csv, parse_dates=["time"])

    tag_start = start or "ALL"
    tag_end = end or "ALL"
    print(f"[wfo] slice {tag_start} .. {tag_end}", flush=True)
    df = slice_period(df, start, end)
    if df.empty:
        raise RuntimeError("No data in the requested period.")
    n = len(df)
    n_tr = max(10, int(n * train_ratio))
    df_tr = df.iloc[:n_tr].reset_index(drop=True)
    df_ts = df.iloc[n_tr:].reset_index(drop=True)

    def _one(d: pd.DataFrame, name: str) -> dict:
        print(f"[wfo] equity compute {name}", flush=True)
        feat = build_features(d, params={})

        # --- 必須列の補完 ---
        if "time" not in feat.columns:
            feat["time"] = pd.to_datetime(d["time"]).reset_index(drop=True)
        if "close" not in feat.columns:
            feat["close"] = pd.Series(d["close"].astype(float)).reset_index(drop=True)

        try:
            kind, payload, threshold, params = load_active_model()
            print(f"[wfo] using model: {payload} threshold={threshold}", flush=True)

            # 予測 → シグナル
            feat["signal"] = predict_signals(kind, payload, feat, threshold, params)
            signal_series = feat["signal"].astype(int).reset_index(drop=True)

            # ログで“出てるか”チェック
            nz = int((signal_series != 0).sum())
            print(f"[wfo] signals nonzero={nz} / {len(signal_series)}", flush=True)
            (out_dir / f"signals_{name}.csv").write_text(
                pd.DataFrame({"time": feat["time"], "signal": signal_series}).to_csv(
                    index=False
                )
            )

            # トレード生成
            trades = trades_from_signals(feat, capital, params)

            # エクイティ展開（DataFrame でも list[Trade] でもOKにする）
            if isinstance(trades, pd.DataFrame):
                eq_series = equity_from_trade_df(feat, trades, capital)
            else:
                eq_series = equity_from_trades(feat, trades, capital)

            eq_df = pd.DataFrame(
                {
                    "time": eq_series.index,
                    "equity": eq_series.values,
                    "signal": signal_series.values,
                }
            )
        except Exception as e:
            print(f"[wfo] AI model not used ({e}) -> fallback to buy&hold", flush=True)
            close = d["close"]
            close.index = d["time"]
            eq_df = to_equity(close, capital)
            eq_df["signal"] = 0
            trades = trades_from_buyhold(d, capital)

        p = out_dir / f"equity_{name}.csv"
        print(f"[wfo] write {p}", flush=True)
        eq_df.to_csv(p, index=False)
        trades.to_csv(out_dir / f"trades_{name}.csv", index=False)

        mr = monthly_returns_from_equity(eq_df)
        mr.to_csv(out_dir / f"monthly_returns_{name}.csv", index=False)
        m = metrics_from_equity(eq_df["equity"])
        m.update(trade_metrics(trades))
        return m

    m_tr = _one(df_tr, "train")
    m_ts = _one(df_ts, "test")

    # 可視化用に test をメインへコピー
    (out_dir / "equity_curve.csv").write_text((out_dir / "equity_test.csv").read_text())
    (out_dir / "metrics_wfo.json").write_text(
        json.dumps({"train": m_tr, "test": m_ts}, ensure_ascii=False, indent=2)
    )

    print("[wfo] done", flush=True)
    return out_dir / "equity_curve.csv"


def _normalize_dates_from_args(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> tuple[str | None, str | None]:
    """
    --start-date / --end-date を優先しつつ、
    旧 --start / --end も互換用としてサポートする。

    返り値は YYYY-MM-DD 形式の文字列か None。
    """
    raw_start = getattr(args, "start_date", None) or getattr(args, "start", None)
    raw_end = getattr(args, "end_date", None) or getattr(args, "end", None)

    def _norm(x: str | None) -> str | None:
        if x is None or x == "":
            return None
        try:
            ts = pd.to_datetime(x)
        except Exception:
            parser.error(f"invalid date format: {x!r} (expected YYYY-MM-DD)")
        return ts.strftime("%Y-%m-%d")

    start_str = _norm(raw_start)
    end_str = _norm(raw_end)

    if start_str is not None and end_str is not None:
        if pd.Timestamp(start_str) > pd.Timestamp(end_str):
            parser.error("start date must be <= end date")

    return start_str, end_str


def _build_period_tag(start: str | None, end: str | None) -> str:
    """
    ログ用の期間タグを生成する。
    例:
      start=2024-07-01, end=2024-12-31 → '2024-07-01_to_2024-12-31'
      start=None,       end=2024-12-31 → 'ALL_to_2024-12-31'
      start=2024-07-01, end=None       → '2024-07-01_to_ALL'
      start=None,       end=None       → 'ALL_to_ALL'
    """
    s = start or "ALL"
    e = end or "ALL"
    return f"{s}_to_{e}"


def _mirror_latest_run(period_dir: Path, base_dir: Path) -> None:
    """
    期間付きフォルダに出力されたファイルを、ベースディレクトリ
    (logs/backtest/{symbol}/{timeframe}) にもコピーして、
    GUI や他ツール向けの「最新結果」として見えるようにする。
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    if not period_dir.exists():
        return
    for f in period_dir.glob("*"):
        if f.is_file():
            target = base_dir / f.name
            target.write_bytes(f.read_bytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="バックテスト対象のCSVファイルパス")
    # 旧オプション（互換用）
    ap.add_argument("--start", help="(legacy) 開始日付 YYYY-MM-DD", required=False)
    ap.add_argument("--end", help="(legacy) 終了日付 YYYY-MM-DD", required=False)
    # 新オプション（推奨）
    ap.add_argument("--start-date", help="開始日付 YYYY-MM-DD", required=False)
    ap.add_argument("--end-date", help="終了日付 YYYY-MM-DD", required=False)

    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--mode", choices=["bt", "wfo"], default="bt")
    ap.add_argument("--symbol", default="USDJPY")
    ap.add_argument("--timeframe", default="M5")
    ap.add_argument("--layout", choices=["flat", "per-symbol"], default="per-symbol")
    ap.add_argument("--train-ratio", type=float, default=0.7)
    args = ap.parse_args()

    csv = Path(args.csv).resolve()
    base_dir = LOG_DIR / args.symbol / args.timeframe
    base_dir.mkdir(parents=True, exist_ok=True)

    # 日付引数の正規化（YYYY-MM-DD or None）
    start_str, end_str = _normalize_dates_from_args(args, ap)
    period_tag = _build_period_tag(start_str, end_str)
    period_dir = base_dir / f"backtest_{period_tag}"
    period_dir.mkdir(parents=True, exist_ok=True)

    print(f"[main] mode={args.mode} csv={csv}", flush=True)
    print(f"[main] period={period_tag}", flush=True)
    if args.mode == "bt":
        p = run_backtest(csv, start_str, end_str, args.capital, period_dir)
    else:
        p = run_wfo(
            csv,
            start_str,
            end_str,
            args.capital,
            period_dir,
            train_ratio=args.train_ratio,
        )

    # 期間付きフォルダの内容を「最新結果」としてベースディレクトリへミラー
    _mirror_latest_run(period_dir, base_dir)

    print(str(p), flush=True)


if __name__ == "__main__":
    main()
