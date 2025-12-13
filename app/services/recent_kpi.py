# app/services/recent_kpi.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence, Union

from app.services.decision_log import load_recent_decisions

import math

try:
    import pandas as pd
    from pandas import DataFrame, Series
except ImportError:  # pandas 未インストール環境向けの保険
    pd = None
    DataFrame = object  # type: ignore
    Series = object  # type: ignore


Number = Union[int, float]


@dataclass
class RecentKpiResult:
    """
    直近 N トレードの簡易 KPI 集計結果。

    単位はすべて「pnl の単位」（= 通貨 or 円）に揃える。
    """

    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: Optional[float]  # 0.0–1.0, データ不足などの場合は None

    gross_profit: float        # 勝ちトレードの合計損益（>=0）
    gross_loss: float          # 負けトレードの合計損益（<=0）
    profit_factor: Optional[float]  # gross_profit / abs(gross_loss)

    net_profit: float          # 総損益（= gross_profit + gross_loss）

    max_drawdown: float        # 最大ドローダウン（>0: 金額）
    max_drawdown_ratio: Optional[float]  # 開始残高が与えられた場合の割合（0.1 で 10%）

    best_win_streak: int       # 連勝の最大値
    best_loss_streak: int      # 連敗の最大値


def _extract_pnl_series(
    trades: Union["DataFrame", Sequence[Mapping[str, Number]]],
    profit_field: str,
) -> Sequence[float]:
    """
    汎用的に「pnl の列」を取り出すヘルパー。

    - pandas.DataFrame なら該当列を float にして NaN を除外
    - list[dict] 的な構造なら profit_field キーで取り出す
    """
    if pd is not None and isinstance(trades, pd.DataFrame):
        if profit_field not in trades.columns:
            raise KeyError(f"profit_field '{profit_field}' not in DataFrame columns")
        series = trades[profit_field].astype(float)
        series = series.dropna()
        return series.to_list()

    pnl_list: list[float] = []
    for i, t in enumerate(trades):
        if profit_field not in t:
            raise KeyError(f"profit_field '{profit_field}' not in trade[{i}] keys")
        pnl_list.append(float(t[profit_field]))  # type: ignore[arg-type]
    return pnl_list

def compute_kpi_from_trades(
    trades: Union["DataFrame", Sequence[Mapping[str, Number]]],
    *,
    profit_field: str = "pnl",
    starting_equity: Optional[float] = None,
) -> RecentKpiResult:
    """
    直近 N トレードの KPI を計算するメイン関数。

    Parameters
    ----------
    trades:
        - pandas.DataFrame または
        - dict のシーケンス（各要素が 1 トレード）を想定。
        profit_field で指定したキー/列に損益（pnl）が入っていること。
    profit_field:
        1 トレードあたりの損益を表す列名 / キー名。
        例: "pnl", "profit"
    starting_equity:
        最大 DD を「残高ベース」で見たい場合の開始残高。
        None の場合は、「0 からスタートした累積損益」に対する DD を返す。
    """
    pnl_list = _extract_pnl_series(trades, profit_field)
    n_trades = len(pnl_list)

    if n_trades == 0:
        # 取引がない場合は全部ゼロ/None で返す
        return RecentKpiResult(
            n_trades=0,
            n_wins=0,
            n_losses=0,
            win_rate=None,
            gross_profit=0.0,
            gross_loss=0.0,
            profit_factor=None,
            net_profit=0.0,
            max_drawdown=0.0,
            max_drawdown_ratio=None,
            best_win_streak=0,
            best_loss_streak=0,
        )

    # --- 勝ち / 負け / 引き分け 判定 ---
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p < 0]
    n_wins = len(wins)
    n_losses = len(losses)

    # 引き分け（pnl == 0）は win_rate / PF の分母からは外す
    n_effective = n_wins + n_losses

    if n_effective > 0:
        win_rate = n_wins / n_effective
    else:
        win_rate = None

    gross_profit = float(sum(wins)) if wins else 0.0
    gross_loss = float(sum(losses)) if losses else 0.0  # <=0
    net_profit = gross_profit + gross_loss

    if gross_loss < 0.0:
        profit_factor = gross_profit / abs(gross_loss)
    else:
        # 負けトレードが無い場合は PF 無限大とみなす / None とするかは好み。
        # ここでは None にして GUI 側で "∞" 表示などに委ねる。
        profit_factor = None

    # --- 最大ドローダウン ---
    # 累積損益から DD を計算する。starting_equity があればそれをベースにする。
    equity_series: list[float] = []
    cumulative = 0.0
    base = starting_equity or 0.0

    for p in pnl_list:
        cumulative += p
        equity_series.append(base + cumulative)

    max_equity = equity_series[0]
    max_dd = 0.0  # 正の値（下方向の幅）
    for eq in equity_series:
        if eq > max_equity:
            max_equity = eq
        dd = max_equity - eq  # max_equity >= eq のとき dd >= 0
        if dd > max_dd:
            max_dd = dd

    if starting_equity is not None and starting_equity > 0:
        max_dd_ratio: Optional[float] = max_dd / float(starting_equity)
    else:
        max_dd_ratio = None

    # --- 連勝 / 連敗 ストリーク ---
    best_win_streak = 0
    best_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0

    for p in pnl_list:
        if p > 0:
            current_win_streak += 1
            current_loss_streak = 0
        elif p < 0:
            current_loss_streak += 1
            current_win_streak = 0
        else:
            # 引き分けはどちらのストリークも中断させる
            current_win_streak = 0
            current_loss_streak = 0

        best_win_streak = max(best_win_streak, current_win_streak)
        best_loss_streak = max(best_loss_streak, current_loss_streak)

    return RecentKpiResult(
        n_trades=n_trades,
        n_wins=n_wins,
        n_losses=n_losses,
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        net_profit=net_profit,
        max_drawdown=max_dd,
        max_drawdown_ratio=max_dd_ratio,
        best_win_streak=best_win_streak,
        best_loss_streak=best_loss_streak,
    )

# === M-A3-5 step23: KPI 統合ロジック ===

import json
from pathlib import Path
import pandas as pd
from datetime import datetime


class KPIService:
    """
    月次KPI（今月の損益％、最大月次DD、月次リターン系列）を一括で返すサービス。
    GUI側は get_kpi(profile) だけ呼べば良い。
    """

    def __init__(self, root: str | Path = None) -> None:
        self.root = Path(root) if root else Path.cwd()

    def _find_latest_monthly_returns(self, profile: str) -> Path | None:
        """
        backtests/{profile}/**/monthly_returns.csv を探索し、最新の1つを返す。
        """
        base = self.root / "logs" / "backtest" / "USDJPY-"  # TODO: symbol固定 → 後で改善
        candidates = list(base.rglob("monthly_returns.csv"))
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    def _load_monthly_returns(self, path: Path) -> pd.DataFrame:
        df = pd.read_csv(path)
        # "year","month","ret_pct","dd_pct" 形式を期待
        return df

    def _load_runtime_metrics(self) -> dict:
        """
        runtime/metrics.json を読み、今月の実運用損益を加算する。
        """
        path = self.root / "runtime" / "metrics.json"
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def get_kpi(self, profile: str = "default") -> dict:
        """
        GUI側が使うメインAPI
        - 今月のリターン（backtest＋live）
        - 最大月次DD
        - 月次リターンの全系列
        """
        out = {
            "monthly_returns": [],
            "current_month_return_pct": 0.0,
            "max_monthly_dd_pct": 0.0,
        }

        # 1. 最新の monthly_returns.csv を探す
        path = self._find_latest_monthly_returns(profile)
        if path is None or not path.exists():
            return out

        df = self._load_monthly_returns(path)
        # 生データはそのまま返しておく（GUI側で柔軟に解釈できるように）
        out["monthly_returns"] = df.to_dict(orient="records")

        # 2. 最大月次DD（カラム名の揺れに対応）
        dd_col = None
        for cand in ["dd_pct", "dd", "max_dd_pct", "max_dd"]:
            if cand in df.columns:
                dd_col = cand
                break

        if dd_col is not None:
            try:
                out["max_monthly_dd_pct"] = float(df[dd_col].min())
            except Exception:
                # 変な値が入っていても落ちないようにする
                pass

        # 3. 今月のバックテストリターン
        now = datetime.now()

        # year/month が無い場合は「全体の最新行」として扱う
        if "year" in df.columns and "month" in df.columns:
            row = df[(df["year"] == now.year) & (df["month"] == now.month)]
        else:
            # 一応「最後の1件」を月次代表として扱う
            row = df.tail(1)

        # リターンカラムの候補を順に探す
        ret_col = None
        for cand in ["ret_pct", "ret", "return_pct", "monthly_ret_pct"]:
            if cand in df.columns:
                ret_col = cand
                break

        if ret_col is not None and len(row):
            try:
                # 今月分が複数行あれば平均しても良いが、とりあえず先頭を採用
                bt_ret = float(row[ret_col].iloc[0])
            except Exception:
                bt_ret = 0.0
        else:
            bt_ret = 0.0

        # 4. runtime/metrics.json の値を加算（ライブ損益）
        rt = self._load_runtime_metrics()
        try:
            live_ret = float(rt.get("monthly_return_pct", 0.0))
        except Exception:
            live_ret = 0.0

        out["current_month_return_pct"] = bt_ret + live_ret
        return out


def compute_recent_kpi_from_decisions(
    limit: Optional[int] = None,
    *,
    profit_field: str = "pnl",
    starting_equity: Optional[float] = None,
) -> RecentKpiResult:
    """
    Read logs/decisions/decisions_*.jsonl, filter trades with numeric pnl, and compute recent KPI.

    Parameters
    ----------
    limit : int | None
        Number of pnl-qualified trades to include from the end of the log. None means all trades.
    profit_field : str
        Column name for the profit/loss value. Defaults to "pnl".
    starting_equity : float | None
        Initial equity used for drawdown ratio. None keeps the ratio None.

    Returns
    -------
    RecentKpiResult
        KPI result even when there are zero pnl trades.
    """
    df = load_recent_decisions(limit=None)

    if df.empty or profit_field not in df.columns:
        return compute_kpi_from_trades(
            df,
            profit_field=profit_field,
            starting_equity=starting_equity,
        )

    pnl = pd.to_numeric(df[profit_field], errors="coerce")
    mask = pnl.notna()
    trades = df.loc[mask].copy()

    if trades.empty:
        return compute_kpi_from_trades(
            trades,
            profit_field=profit_field,
            starting_equity=starting_equity,
        )

    if "ts_jst" in trades.columns:
        trades = trades.sort_values("ts_jst")

    if limit is not None:
        trades = trades.tail(limit)

    return compute_kpi_from_trades(
        trades,
        profit_field=profit_field,
        starting_equity=starting_equity,
    )
