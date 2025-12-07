# app/services/kpi_service.py

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional


BACKTESTS_DIR = Path("backtests")
DEFAULT_TARGET = 0.03  # 月次3%


@dataclass
class MonthlyRecord:
    year_month: str
    return_pct: float
    max_dd_pct: float
    total_trades: int
    pf: float


class KPIService:
    """ミチビキ v5.1 用 KPI サービス

    - monthly_returns.csv を読む
    - 月次3%ダッシュボード用の dict を返す
    """

    @classmethod
    def load_monthly_returns(cls, profile: str) -> List[MonthlyRecord]:
        csv_path = BACKTESTS_DIR / profile / "monthly_returns.csv"

        if not csv_path.exists():
            return []

        records: List[MonthlyRecord] = []

        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    records.append(
                        MonthlyRecord(
                            year_month=row.get("year_month", ""),
                            return_pct=float(row.get("return_pct", 0.0)),
                            max_dd_pct=float(row.get("max_dd_pct", 0.0)),
                            total_trades=int(row.get("total_trades", 0)),
                            pf=float(row.get("pf", 0.0)),
                        )
                    )
                except Exception:
                    # 1行壊れてても全体は止めない
                    continue
        return records

    @classmethod
    def compute_monthly_dashboard(
        cls,
        profile: str,
        target: float = DEFAULT_TARGET,
        months_window: int = 12,
    ) -> Dict:
        """AIタブのKPIダッシュボード用データを返す

        戻り値の例:
        {
          "has_data": True/False,
          "months": ["2025-01", ...],
          "returns": [0.012, ...],
          "target": 0.03,
          "current_month_return": 0.015,
          "progress_pct": 50.0,
          "max_dd_pct": -0.12,
          "avg_pf": 1.23,
        }
        """
        records = cls.load_monthly_returns(profile)
        if not records:
            return {
                "has_data": False,
                "months": [],
                "returns": [],
                "target": target,
                "current_month_return": 0.0,
                "progress_pct": 0.0,
                "max_dd_pct": None,
                "avg_pf": None,
            }

        # 新しい順に並んでいたとしても念のためソート
        records = sorted(records, key=lambda r: r.year_month)
        if months_window and len(records) > months_window:
            records = records[-months_window:]

        months = [r.year_month for r in records]
        returns = [r.return_pct for r in records]

        # 最大DD（最小値）
        max_dd_pct = min(r.max_dd_pct for r in records)

        # PF 平均（0は除外）
        pf_vals = [r.pf for r in records if r.pf > 0]
        avg_pf = sum(pf_vals) / len(pf_vals) if pf_vals else None

        # 今月の year_month
        now_ym = datetime.now().strftime("%Y-%m")
        current_rec: Optional[MonthlyRecord] = None
        for r in records:
            if r.year_month == now_ym:
                current_rec = r
                break

        current_return = current_rec.return_pct if current_rec else 0.0
        progress_pct = (current_return / target * 100.0) if target > 0 else 0.0

        return {
            "has_data": True,
            "months": months,
            "returns": returns,
            "target": target,
            "current_month_return": current_return,
            "progress_pct": progress_pct,
            "max_dd_pct": max_dd_pct,
            "avg_pf": avg_pf,
        }

    @classmethod
    def compute_target_progress(cls, current_return: float, target: float = DEFAULT_TARGET) -> float:
        """単体で進捗だけ欲しい場合のヘルパー"""
        if target <= 0:
            return 0.0
        return (current_return / target) * 100.0


# === 互換性のための関数（既存コード用） ===

def load_backtest_kpi_summary(profile: str = "michibiki_std"):
    """
    既存コードとの互換性のための関数。
    
    BacktestKpiSummary 形式のデータを返す（旧仕様互換）。
    """
    from dataclasses import dataclass
    from typing import List, Optional
    from app.core.strategy_profile import get_profile
    
    @dataclass
    class MonthlyKPI:
        year: int
        month: int
        return_pct: float
        dd_pct: Optional[float] = None
    
    @dataclass
    class BacktestKpiSummary:
        n_months: int
        avg_return_pct: float
        median_return_pct: float
        win_ratio: float
        max_dd_pct: Optional[float]
        target_monthly_return_pct: float
        target_hit_ratio: float
        months: List[MonthlyKPI]
    
    try:
        profile_obj = get_profile(profile)
        target_monthly_return_pct = profile_obj.target_monthly_return * 100.0
    except Exception:
        target_monthly_return_pct = 3.0  # デフォルト3%
    
    records = KPIService.load_monthly_returns(profile)
    if not records:
        return BacktestKpiSummary(
            n_months=0,
            avg_return_pct=0.0,
            median_return_pct=0.0,
            win_ratio=0.0,
            max_dd_pct=None,
            target_monthly_return_pct=target_monthly_return_pct,
            target_hit_ratio=0.0,
            months=[],
        )
    
    # year_month から year, month を抽出
    months_kpi: List[MonthlyKPI] = []
    returns_pct: List[float] = []
    dd_pcts: List[float] = []
    
    for r in records:
        try:
            year, month = map(int, r.year_month.split("-"))
            months_kpi.append(
                MonthlyKPI(
                    year=year,
                    month=month,
                    return_pct=r.return_pct,
                    dd_pct=r.max_dd_pct if r.max_dd_pct != 0.0 else None,
                )
            )
            returns_pct.append(r.return_pct)
            dd_pcts.append(r.max_dd_pct)
        except Exception:
            continue
    
    if not returns_pct:
        return BacktestKpiSummary(
            n_months=0,
            avg_return_pct=0.0,
            median_return_pct=0.0,
            win_ratio=0.0,
            max_dd_pct=None,
            target_monthly_return_pct=target_monthly_return_pct,
            target_hit_ratio=0.0,
            months=[],
        )
    
    # 統計計算
    import statistics
    
    avg_return_pct = statistics.mean(returns_pct)
    median_return_pct = statistics.median(returns_pct)
    win_ratio = sum(1 for r in returns_pct if r > 0) / len(returns_pct)
    max_dd_pct = min(dd_pcts) if dd_pcts else None
    target_hit_ratio = sum(1 for r in returns_pct if r >= target_monthly_return_pct / 100.0) / len(returns_pct)
    
    return BacktestKpiSummary(
        n_months=len(returns_pct),
        avg_return_pct=avg_return_pct,
        median_return_pct=median_return_pct,
        win_ratio=win_ratio,
        max_dd_pct=max_dd_pct,
        target_monthly_return_pct=target_monthly_return_pct,
        target_hit_ratio=target_hit_ratio,
        months=months_kpi,
    )
