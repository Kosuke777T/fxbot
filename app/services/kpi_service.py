# app/services/kpi_service.py

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import math

import numpy as np
import pandas as pd
from loguru import logger

from app.core.strategy_profile import get_profile


@dataclass
class MonthlyKPI:
    year: int
    month: int
    # CSV は「%」単位なので、そのまま % の値を入れる（例: 3.0 = +3%）
    return_pct: float
    dd_pct: Optional[float] = None


@dataclass
class BacktestKpiSummary:
    n_months: int
    avg_return_pct: float          # 平均月次リターン（%）
    median_return_pct: float       # 中央値（%）
    win_ratio: float               # プラス月の比率（0.0〜1.0）
    max_dd_pct: Optional[float]    # 最悪月次DD（% / マイナス方向）
    target_monthly_return_pct: float  # プロファイルの目標（%）
    target_hit_ratio: float        # 目標以上を達成した月の比率（0.0〜1.0）
    months: List[MonthlyKPI]


def load_backtest_kpi_summary() -> BacktestKpiSummary:
    """
    アクティブな StrategyProfile から monthly_returns.csv を読み込み、
    月次KPIを集計して返す。

    想定する CSV 形式:
        year,month,return_pct,dd_pct
        2024,7,-7.0,-7.12
        2024,8,-2.5,-6.12
        ...

    return_pct / dd_pct は「%」単位とみなす。
    """
    profile = get_profile()
    csv_path: Path = profile.monthly_returns_path

    logger.info(f"[KPI] loading monthly_returns.csv from: {csv_path}")

    if not csv_path.exists():
        raise FileNotFoundError(f"monthly_returns.csv not found: {csv_path}")

    df = pd.read_csv(csv_path)

    if "return_pct" not in df.columns:
        raise ValueError("monthly_returns.csv に 'return_pct' 列がありません。")

    has_dd = "dd_pct" in df.columns

    if df.empty:
        raise ValueError("monthly_returns.csv に行がありません。")

    # NaN を落としておく（全部NaNなら ValueError にする）
    df = df.dropna(subset=["return_pct"])
    if df.empty:
        raise ValueError("monthly_returns.csv の return_pct が全て NaN です。")

    # 「%」単位 → 内部では小数にして扱う
    raw_ret = df["return_pct"].astype(float).to_numpy()
    returns = raw_ret / 100.0  # 例: 3.0(%) -> 0.03

    # dd_pct もあれば同様に処理
    if has_dd:
        raw_dd = df["dd_pct"].astype(float).to_numpy()
        dd_vals = raw_dd / 100.0
    else:
        dd_vals = None

    n_months = returns.size

    avg_ret = float(np.mean(returns))
    median_ret = float(np.median(returns))
    win_ratio = float(np.mean(returns > 0.0))

    max_dd_pct: Optional[float]
    if dd_vals is not None and dd_vals.size > 0:
        # 最も悪い（=最も小さい）月次DD
        max_dd = float(dd_vals.min())  # 例: -0.0712
        max_dd_pct = max_dd * 100.0
    else:
        max_dd_pct = None

    # プロファイルに設定されたターゲット（小数, 例: 0.03 = 3%）
    target = float(profile.target_monthly_return)
    target_monthly_return_pct = target * 100.0

    target_hit_ratio = float(np.mean(returns >= target))

    months: List[MonthlyKPI] = []
    for _, row in df.iterrows():
        year = int(row["year"])
        month = int(row["month"])
        r_pct = float(row["return_pct"])
        dd_pct_val: Optional[float] = None
        if has_dd:
            try:
                v = float(row["dd_pct"])
                if not math.isnan(v):
                    dd_pct_val = v
            except Exception:
                dd_pct_val = None

        months.append(
            MonthlyKPI(
                year=year,
                month=month,
                return_pct=r_pct,
                dd_pct=dd_pct_val,
            )
        )

    summary = BacktestKpiSummary(
        n_months=n_months,
        avg_return_pct=avg_ret * 100.0,        # 小数 → %
        median_return_pct=median_ret * 100.0,
        win_ratio=win_ratio,
        max_dd_pct=max_dd_pct,
        target_monthly_return_pct=target_monthly_return_pct,
        target_hit_ratio=target_hit_ratio,
        months=months,
    )

    logger.info(
        "[KPI] n_months={n}, avg={avg:.2f}%, median={med:.2f}%, win_ratio={wr:.1%}, "
        "max_dd={dd}, target={tgt:.2f}%, hit_ratio={hit:.1%}",
        n=summary.n_months,
        avg=summary.avg_return_pct,
        med=summary.median_return_pct,
        wr=summary.win_ratio,
        dd=summary.max_dd_pct,
        tgt=summary.target_monthly_return_pct,
        hit=summary.target_hit_ratio,
    )

    return summary
