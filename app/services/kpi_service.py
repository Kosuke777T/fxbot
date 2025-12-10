from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# BacktestRun が出力する標準パス
# backtests/{profile}/monthly_returns.csv を読む
BACKTEST_ROOT = Path("backtests")

# KPI 仕様の「月3%」目標値
TARGET_MONTHLY_RETURN = 0.03


@dataclass
class KpiMonthlyRecord:
    year_month: str
    return_pct: float
    max_dd_pct: float
    total_trades: int
    pf: float


@dataclass
class KpiDashboard:
    profile: str
    has_backtest: bool
    current_month: Optional[str]
    current_month_return: float
    current_month_progress: float  # 0.0〜2.0（=0〜200%）でクリップ
    monthly: List[KpiMonthlyRecord]


class KPIService:
    """バックテスト結果を元に KPI ダッシュボード用のデータを作るサービス."""

    def __init__(
        self,
        backtest_root: Optional[Path] = None,
        base_dir: Optional[Path] = None,
    ) -> None:
        """
        backtest_root:
            - 明示的に backtests ルートディレクトリを指定したい場合に使用
            - 例: KPIService(backtest_root=Path("backtests"))

        base_dir:
            - 旧仕様互換用。
            - 例: KPIService(base_dir=Path(".")) のような呼び出しをサポートする。
            - base_dir が指定された場合は base_dir / "backtests" を backtest_root とみなす。
        """
        if backtest_root is not None:
            self.backtest_root = Path(backtest_root)
        elif base_dir is not None:
            self.backtest_root = Path(base_dir) / "backtests"
        else:
            self.backtest_root = BACKTEST_ROOT
        # monthly_returns の簡易キャッシュ（必要なら）
        self._monthly_cache: dict[str, pd.DataFrame] = {}

    # 仕様書で書いてある標準メソッド名
    # load_backtest_kpi_summary(profile) -> dict
    def load_backtest_kpi_summary(self, profile: str) -> Dict[str, Any]:
        dashboard = self.compute_monthly_dashboard(profile)

        # GUI から扱いやすいように dict 化
        return {
            "profile": dashboard.profile,
            "has_backtest": dashboard.has_backtest,
            "current_month": dashboard.current_month,
            "current_month_return": dashboard.current_month_return,
            "current_month_progress": dashboard.current_month_progress,
            "monthly": [
                {
                    "year_month": m.year_month,
                    "return_pct": m.return_pct,
                    "max_dd_pct": m.max_dd_pct,
                    "total_trades": m.total_trades,
                    "pf": m.pf,
                }
                for m in dashboard.monthly
            ],
        }

    # 仕様書 v5 の「compute_monthly_dashboard(profile)」実体
    def compute_monthly_dashboard(self, profile: str) -> KpiDashboard:
        df = self._load_monthly_returns(profile)

        if df.empty:
            # まだバックテストしていない場合
            return KpiDashboard(
                profile=profile,
                has_backtest=False,
                current_month=None,
                current_month_return=0.0,
                current_month_progress=0.0,
                monthly=[],
            )

        # 直近 12 ヶ月だけを KPI の対象にする
        df_12 = df.tail(12).copy()

        # 現在の年月キー（例: 2025-12）
        now = datetime.now()
        ym_now = f"{now.year:04d}-{now.month:02d}"

        row_now = df_12.loc[df_12["year_month"] == ym_now]
        if row_now.empty:
            # 今月分がまだ無ければ、最後の行を「最新」と見なす
            row = df_12.iloc[-1]
        else:
            row = row_now.iloc[0]

        current_month = str(row["year_month"])
        current_return = float(row["return_pct"])
        progress = self.compute_target_progress(current_return)

        monthly_records = [
            KpiMonthlyRecord(
                year_month=str(r["year_month"]),
                return_pct=float(r["return_pct"]),
                max_dd_pct=float(r["max_dd_pct"]),
                total_trades=int(r["total_trades"]),
                pf=float(r["pf"]),
            )
            for _, r in df_12.iterrows()
        ]

        return KpiDashboard(
            profile=profile,
            has_backtest=True,
            current_month=current_month,
            current_month_return=current_return,
            current_month_progress=progress,
            monthly=monthly_records,
        )

    def compute_target_progress(
        self,
        return_pct: float,
        target: float = TARGET_MONTHLY_RETURN,
    ) -> float:
        """月3%に対する進捗率（0.0〜2.0=0〜200%）を返す。"""

        if target <= 0:
            return 0.0

        raw = return_pct / target
        # 仕様上 0〜200% を想定しているので 0.0〜2.0 にクリップ
        # （ゲージ側で ×100 してパーセント表示）
        return max(0.0, min(2.0, raw))

    # --- 内部関数 ---

    def _load_monthly_returns(self, profile: str) -> pd.DataFrame:
        """BacktestRun が出力した monthly_returns.csv を読み込む。"""

        csv_path = self.backtest_root / profile / "monthly_returns.csv"

        if not csv_path.exists():
            # まだバックテストしていない場合は空 DataFrame
            return pd.DataFrame(
                columns=[
                    "year_month",
                    "return_pct",
                    "max_dd_pct",
                    "total_trades",
                    "pf",
                ]
            )

        df = pd.read_csv(csv_path)

        # 型を一応そろえておく（念のため）
        df["year_month"] = df["year_month"].astype(str)
        df["return_pct"] = df["return_pct"].astype(float)
        df["max_dd_pct"] = df["max_dd_pct"].astype(float)
        df["total_trades"] = df["total_trades"].astype(int)
        df["pf"] = df["pf"].astype(float)

        return df

    def load_monthly_returns(self, profile: str) -> pd.DataFrame:
        """
        指定プロファイルの monthly_returns.csv を読み込んで返す。
        必須フォーマット:
          year_month, return_pct, max_dd_pct, total_trades, pf
        """
        return self._load_monthly_returns(profile)

    def refresh_monthly_returns(self, profile: str) -> pd.DataFrame:
        """
        BacktestRun が monthly_returns.csv を更新した後に呼び出す前提。
        キャッシュを捨てて最新の monthly_returns を返す。
        """
        # キャッシュを使っている場合は破棄
        if hasattr(self, "_monthly_cache"):
            self._monthly_cache.pop(profile, None)

        df = self.load_monthly_returns(profile)

        # ここで KPI 用の派生データを更新してもよい
        # （例：self._kpi_summary[profile] = self._build_kpi_summary(df) など）

        return df
