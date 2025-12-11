# tools/backtest_run.py
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

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

# === monthly_returns.csv を生成するユーティリティ ===
import pandas as pd


def iter_with_progress(df: pd.DataFrame, step: int = 5, use_iterrows: bool = False):
    """
    バックテスト用の行イテレータ。
    df を順に返しながら、step (%) ごとに [bt_progress] ログを出す。

    例: step=5 のとき → 5, 10, 15, ..., 100

    Parameters
    ----------
    df : pd.DataFrame
        対象のDataFrame
    step : int
        進捗を出力する間隔（%）
    use_iterrows : bool
        True の場合は iterrows() を使用、False の場合は itertuples() を使用
    """
    n = len(df)
    if n <= 0:
        return
    last_pct = -1
    if use_iterrows:
        for i, (idx, row) in enumerate(df.iterrows()):
            pct = int(100 * (i + 1) / n)
            if pct != last_pct and pct % step == 0:
                print(f"[bt_progress] {pct}", flush=True)
                last_pct = pct
            yield idx, row
    else:
        for i, row in enumerate(df.itertuples()):
            pct = int(100 * (i + 1) / n)
            if pct != last_pct and pct % step == 0:
                print(f"[bt_progress] {pct}", flush=True)
                last_pct = pct
            yield i, row


def _print_progress(pct: int) -> None:
    """バックテスト進捗を [bt_progress] 形式で出力するヘルパー"""
    pct = max(0, min(100, int(pct)))
    print(f"[bt_progress] {pct}", flush=True)


def _month_dd(equity: pd.Series) -> float:
    """月内最大ドローダウンを計算する（v5.1 仕様）"""
    if equity.empty:
        return 0.0
    dd = (equity / equity.cummax() - 1.0).min()
    return float(dd * 100.0)  # ％に変換（-5.4 なら -5.4%）


def compute_monthly_returns(
    equity_csv_path: str | Path,
    out_path: str | Path,
) -> Path:
    """
    equity.csv から v5.1 仕様の monthly_returns.csv を作成する。

    - equity_csv_path: equity.csv のパス（time, equity を含む想定）
    - out_path: monthly_returns.csv の出力パス

    出力カラム:
        year_month, return_pct, max_dd_pct, total_trades, pf
    """
    equity_csv_path = Path(equity_csv_path)
    out_path = Path(out_path)

    # === equity 読み込み ===
    df_eq = pd.read_csv(equity_csv_path)

    if "time" not in df_eq.columns:
        raise ValueError("equity.csv に 'time' 列がありません。")

    # equity 列の名前が違う場合のフォールバック
    if "equity" not in df_eq.columns:
        for cand in ("equity_curve", "balance", "equity_value"):
            if cand in df_eq.columns:
                df_eq["equity"] = df_eq[cand]
                break
        else:
            raise ValueError("equity.csv に 'equity' 系の列が見つかりません。")

    df_eq["time"] = pd.to_datetime(df_eq["time"])
    df_eq = df_eq.sort_values("time").reset_index(drop=True)

    # index を time にした Series（値は口座残高や評価額）
    eq_series = df_eq.set_index("time")["equity"].astype(float)

    # 対象となる年月の一覧（Period[M]）
    months = eq_series.index.to_period("M").unique()

    # === trades.csv を読み込み（あれば） ===
    trades_csv = equity_csv_path.with_name("trades.csv")
    trades_df: pd.DataFrame | None = None

    if trades_csv.exists():
        tdf = pd.read_csv(trades_csv)

        # entry_time 列の標準化
        if "entry_time" in tdf.columns:
            tdf["entry_time"] = pd.to_datetime(tdf["entry_time"])
        elif "open_time" in tdf.columns:
            tdf["entry_time"] = pd.to_datetime(tdf["open_time"])
        else:
            # 月次トレード数・PFを計算できないので NaT にしておく
            tdf["entry_time"] = pd.NaT

        # pnl 列の標準化
        if "pnl" not in tdf.columns:
            if "profit" in tdf.columns:
                tdf["pnl"] = tdf["profit"].astype(float)
            else:
                tdf["pnl"] = 0.0

        trades_df = tdf

    rows: list[dict] = []

    for p in months:
        # 当該月の equity 部分
        mask_month = eq_series.index.to_period("M") == p
        sub_eq = eq_series[mask_month]
        if sub_eq.empty:
            continue

        year_month = f"{p.year}-{p.month:02d}"

        # 月次リターン: 期首→期末のリターン
        return_pct = float(sub_eq.iloc[-1] / sub_eq.iloc[0] - 1.0)

        # 月内最大DD（v5.1 仕様に合わせて _month_dd を利用）
        max_dd_pct = float(_month_dd(sub_eq))

        # default 値
        total_trades = 0
        pf = 0.0

        # トレード情報がある場合のみ total_trades, pf を計算
        if trades_df is not None and trades_df["entry_time"].notna().any():
            mask_trades = trades_df["entry_time"].dt.to_period("M") == p
            m_trades = trades_df[mask_trades]

            total_trades = int(len(m_trades))

            if total_trades > 0:
                pnl = m_trades["pnl"].astype(float)
                gross_profit = float(pnl[pnl > 0].sum())
                gross_loss = float(pnl[pnl < 0].sum())  # マイナス値

                if gross_loss < 0:
                    pf = float(gross_profit / abs(gross_loss)) if gross_profit > 0 else 0.0
                elif gross_profit > 0:
                    # 損失が 0 で利益のみ → PF を無限大扱い
                    pf = float("inf")

        rows.append(
            {
                "year_month": year_month,
                "return_pct": return_pct,
                "max_dd_pct": max_dd_pct,
                "total_trades": total_trades,
                "pf": pf,
            }
        )

    df_monthly = pd.DataFrame(
        rows,
        columns=["year_month", "return_pct", "max_dd_pct", "total_trades", "pf"],
    )

    df_monthly.to_csv(out_path, index=False)
    print(f"[bt] write monthly {out_path}")
    return out_path


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


def monthly_returns_from_equity(
    eq_df: pd.DataFrame,
    trades_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    エクイティ曲線（eq_df）とトレード一覧（trades_df）から、
    月次のリターン・DD・トレード統計をまとめた DataFrame を返す。

    返り値カラム:
        year, month, return_pct, dd_pct, trades, win_rate, pf
    """
    if eq_df is None or eq_df.empty:
        return pd.DataFrame(
            columns=[
                "year",
                "month",
                "return_pct",
                "dd_pct",
                "trades",
                "win_rate",
                "pf",
            ]
        )

    df = eq_df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time")
    df = df.set_index("time")

    # --- 月次のリターン（％） ---
    monthly_last = df["equity"].resample("ME").last()
    monthly_first = df["equity"].resample("ME").first()

    # 0割り防止
    ret_raw = (monthly_last / monthly_first).replace([np.inf, -np.inf], np.nan) - 1.0
    return_pct = (ret_raw * 100.0).rename("return_pct")  # ％に変換

    # --- 月次の最大ドローダウン（％） ---
    dd_pct = df["equity"].resample("ME").apply(_month_dd).rename("dd_pct")

    # ベースとなる DataFrame（year/month 作成）
    out = pd.concat([return_pct, dd_pct], axis=1).dropna(how="all")
    if out.empty:
        return pd.DataFrame(
            columns=[
                "year",
                "month",
                "return_pct",
                "dd_pct",
                "trades",
                "win_rate",
                "pf",
            ]
        )

    out.index = pd.to_datetime(out.index)
    out["year"] = out.index.year
    out["month"] = out.index.month

    # デフォルト値（トレード統計は 0 で初期化）
    out["trades"] = 0
    out["win_rate"] = 0.0
    out["pf"] = 0.0

    # --- トレード統計（月次） ---
    if trades_df is not None and not trades_df.empty and "exit_time" in trades_df.columns:
        td = trades_df.copy()
        td["exit_time"] = pd.to_datetime(td["exit_time"])
        td = td.dropna(subset=["exit_time"])

        if "pnl" in td.columns:
            td["pnl"] = pd.to_numeric(td["pnl"], errors="coerce").fillna(0.0)

            rows = []
            for (y, m), g in td.groupby([td["exit_time"].dt.year, td["exit_time"].dt.month]):
                pnl = g["pnl"].astype(float)
                n_tr = int(len(pnl))
                if n_tr == 0:
                    continue
                win_mask = pnl > 0
                win_rate = float(win_mask.mean() * 100.0)

                sum_win = float(pnl[win_mask].sum())
                sum_loss_abs = float((-pnl[~win_mask]).clip(lower=0).sum())

                if sum_loss_abs > 0:
                    pf = float(sum_win / sum_loss_abs)
                elif sum_win > 0:
                    pf = float("inf")
                else:
                    pf = 0.0

                rows.append(
                    {
                        "year": int(y),
                        "month": int(m),
                        "trades": n_tr,
                        "win_rate": win_rate,
                        "pf": pf,
                    }
                )

            if rows:
                stats = pd.DataFrame(rows)
                out = out.merge(stats, on=["year", "month"], how="left", suffixes=("", "_t"))

                # 欠損を初期値で埋める
                out["trades"] = out["trades_t"].fillna(out["trades"]).astype(int)
                out["win_rate"] = out["win_rate_t"].fillna(out["win_rate"])
                out["pf"] = out["pf_t"].fillna(out["pf"])

                # 一時列を削除
                out = out.drop(columns=[c for c in out.columns if c.endswith("_t")])

    # カラム順を最終仕様に揃える
    out = out[["year", "month", "return_pct", "dd_pct", "trades", "win_rate", "pf"]]
    out = out.reset_index(drop=True)
    return out


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
    profile: str = "michibiki_std",
    symbol: str = "USDJPY",
) -> Path:
    """
    v5.1 準拠のバックテストを実行する

    Parameters
    ----------
    data_csv : Path
        OHLCVデータのCSVファイル
    start : str | None
        開始日（YYYY-MM-DD形式）
    end : str | None
        終了日（YYYY-MM-DD形式）
    capital : float
        初期資本
    out_dir : Path
        出力ディレクトリ
    profile : str
        プロファイル名
    symbol : str
        シンボル名

    Returns
    -------
    Path
        equity_curve.csv のパス
    """
    print("[bt] start (v5.1)", flush=True)
    # _print_progress(0)  # iter_with_progress で5%刻みになるので削除
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[bt] read_csv {data_csv}", flush=True)
    df = pd.read_csv(data_csv, parse_dates=["time"])
    # _print_progress(10)  # iter_with_progress で5%刻みになるので削除

    tag_start = start or "ALL"
    tag_end = end or "ALL"
    print(f"[bt] slice {tag_start} .. {tag_end}", flush=True)
    df = slice_period(df, start, end)
    if df.empty:
        raise RuntimeError("No data in the requested period.")
    # _print_progress(30)  # iter_with_progress で5%刻みになるので削除

    # v5.1 準拠の BacktestEngine を使用
    try:
        from app.core.backtest.backtest_engine import BacktestEngine
        from app.services.edition_guard import EditionGuard

        # EditionGuard から filter_level を取得
        guard = EditionGuard()
        current_filter_level = guard.filter_level()

        print(f"[bt] Initializing BacktestEngine (profile={profile}, filter_level={current_filter_level})", flush=True)
        engine = BacktestEngine(
            profile=profile,
            initial_capital=capital,
            filter_level=current_filter_level,
        )

        print(f"[bt] Running backtest...", flush=True)
        results = engine.run(df, out_dir, symbol=symbol)
        # _print_progress(60)  # iter_with_progress で5%刻みになるので削除

        eq_csv = results["equity_curve"]
        print(f"[bt] Backtest completed. Output: {eq_csv}", flush=True)
        # _print_progress(80)  # iter_with_progress で5%刻みになるので削除

        # メトリクスを計算
        eq_df = pd.read_csv(eq_csv)
        base = metrics_from_equity(pd.Series(eq_df["equity"].values, index=pd.to_datetime(eq_df["time"])))

        trades_df = pd.read_csv(results["trades"]) if results["trades"].exists() else pd.DataFrame()
        if not trades_df.empty:
            tmet = trade_metrics(trades_df)
            base.update(tmet)

        (out_dir / "metrics.json").write_text(
            json.dumps(base, ensure_ascii=False, indent=2)
        )

        # 必須ファイルを必ず出力
        # equity_curve.csv は既に生成済み（results["equity_curve"]）
        eq_csv_path = results.get("equity_curve")
        if eq_csv_path and not eq_csv_path.exists():
            # 念のため再生成
            eq_df.to_csv(eq_csv_path, index=False)
            print(f"[bt] wrote equity_curve.csv: {eq_csv_path}", flush=True)

        # monthly_returns.csv の生成を保証
        monthly_csv = results.get("monthly_returns") or (out_dir / "monthly_returns.csv")
        if not monthly_csv.exists() and eq_csv_path and eq_csv_path.exists():
            compute_monthly_returns(eq_csv_path, monthly_csv)
            print(f"[bt] wrote monthly_returns.csv: {monthly_csv}", flush=True)

        # trades.csv の生成を保証
        trades_csv = results.get("trades") or (out_dir / "trades.csv")
        if not trades_csv.exists():
            # 空のCSVを作成
            pd.DataFrame(columns=["entry_time", "entry_price", "exit_time", "exit_price", "side", "lot", "pnl"]).to_csv(trades_csv, index=False)
            print(f"[bt] wrote empty trades.csv: {trades_csv}", flush=True)

        # decisions.jsonl の生成を保証
        decisions_jsonl = results.get("decisions") or (out_dir / "decisions.jsonl")
        if not decisions_jsonl.exists():
            # 空のファイルを作成
            decisions_jsonl.write_text("", encoding="utf-8")
            print(f"[bt] wrote empty decisions.jsonl: {decisions_jsonl}", flush=True)

        print("[bt] done", flush=True)
        return eq_csv

    except Exception as e:
        print(f"[bt] BacktestEngine failed: {e}, falling back to Buy&Hold", flush=True)
        import traceback
        traceback.print_exc()

        # フォールバック: Buy&Hold
        close = df["close"]
        close.index = df["time"]
        eq_df = to_equity(close, capital)
        eq_df["signal"] = 0
        eq_csv = out_dir / "equity_curve.csv"
        eq_df.to_csv(eq_csv, index=False)
        print(f"[bt] wrote equity_curve.csv: {eq_csv}", flush=True)

        # 必須ファイルを必ず出力
        monthly_path = out_dir / "monthly_returns.csv"
        compute_monthly_returns(eq_csv, monthly_path)
        print(f"[bt] wrote monthly_returns.csv: {monthly_path}", flush=True)

        trades = trades_from_buyhold(df, capital)
        trades_csv = out_dir / "trades.csv"
        trades.to_csv(trades_csv, index=False)
        print(f"[bt] wrote trades.csv: {trades_csv}", flush=True)

        # decisions.jsonl の生成を保証（空ファイル）
        decisions_jsonl = out_dir / "decisions.jsonl"
        decisions_jsonl.write_text("", encoding="utf-8")
        print(f"[bt] wrote empty decisions.jsonl: {decisions_jsonl}", flush=True)

        base = metrics_from_equity(eq_df["equity"])
        tmet = trade_metrics(trades)
        base.update(tmet)
        (out_dir / "metrics.json").write_text(
            json.dumps(base, ensure_ascii=False, indent=2)
        )
        print(f"[bt] wrote metrics.json: {out_dir / 'metrics.json'}", flush=True)

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

        # 月次損益＋トレード統計
        mr = monthly_returns_from_equity(eq_df, trades_df=trades)
        mr.to_csv(out_dir / f"monthly_returns_{name}.csv", index=False)

        m = metrics_from_equity(eq_df["equity"])
        m.update(trade_metrics(trades))
        return m

    m_tr = _one(df_tr, "train")
    m_ts = _one(df_ts, "test")

    # 可視化用に test をメインへコピー
    (out_dir / "equity_curve.csv").write_text((out_dir / "equity_test.csv").read_text())

    # v5.1 仕様の monthly_returns.csv を生成（test 期間の結果を使用）
    equity_test_csv = out_dir / "equity_test.csv"
    monthly_csv = out_dir / "monthly_returns.csv"
    compute_monthly_returns(equity_test_csv, monthly_csv)

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
    期間付きフォルダに出力されたファイルのうち、
    ミチビキ標準で参照するファイルだけを base_dir にミラーする。

    対象:
      - equity_curve.csv
      - trades.csv
      - monthly_returns.csv
      - metrics.json
      - decisions.jsonl

    WFO の train/test 系ファイルはコピーしない。
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    if not period_dir.exists():
        return

    # ミラー対象ファイル名（標準BTの5点セット）
    allowed = {
        "equity_curve.csv",
        "trades.csv",
        "monthly_returns.csv",
        "metrics.json",
        "decisions.jsonl",
    }

    for f in period_dir.glob("*"):
        if not f.is_file():
            continue
        if f.name not in allowed:
            continue
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
    ap.add_argument("--profile", default="michibiki_std", help="プロファイル名（backtests/<profile>/ に出力）")
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
    print(f"[main] profile={args.profile}", flush=True)
    if args.mode == "bt":
        p = run_backtest(csv, start_str, end_str, args.capital, period_dir, profile=args.profile, symbol=args.symbol)
    else:
        p = run_wfo(
            csv,
            start_str,
            end_str,
            args.capital,
            period_dir,
            train_ratio=args.train_ratio,
        )

    # 期間付きフォルダの内容を「最新結果」として backtests/<profile>/ へミラー
    profile_dir = PROJECT_ROOT / "backtests" / args.profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    _mirror_latest_run(period_dir, profile_dir)

    print(str(p), flush=True)


if __name__ == "__main__":
    main()
