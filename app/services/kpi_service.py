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
        """
        バックテストKPIサマリを読み込む（仕様書 v5.1 準拠）。

        Parameters
        ----------
        profile : str
            プロファイル名（例: "michibiki_std"）

        Returns
        -------
        Dict[str, Any]
            {
                "profile": str,
                "has_backtest": bool,
                "current_month": str | None,
                "current_month_return": float,
                "current_month_progress": float,
                "monthly": List[Dict],
                # 追加統計（過去12ヶ月）
                "avg_return_pct": float,
                "max_dd_pct": float,
                "win_rate": float,
                "avg_pf": float,
            }
        """
        try:
            dashboard = self.compute_monthly_dashboard(profile)

            # 過去12ヶ月の統計を計算
            monthly_list = dashboard.monthly
            avg_return_pct = 0.0
            max_dd_pct = 0.0
            win_rate = 0.0
            avg_pf = 0.0

            if monthly_list:
                returns = [m.return_pct for m in monthly_list]
                dds = [m.max_dd_pct for m in monthly_list]
                pfs = [m.pf for m in monthly_list if m.pf > 0]

                avg_return_pct = sum(returns) / len(returns) if returns else 0.0
                max_dd_pct = min(dds) if dds else 0.0
                win_rate = sum(1 for r in returns if r > 0) / len(returns) if returns else 0.0
                avg_pf = sum(pfs) / len(pfs) if pfs else 0.0

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
                    for m in monthly_list
                ],
                # 追加統計（過去12ヶ月）
                "avg_return_pct": avg_return_pct,
                "max_dd_pct": max_dd_pct,
                "win_rate": win_rate,
                "avg_pf": avg_pf,
            }
        except Exception as e:
            # 例外はすべて握り、安全な dict を返す（仕様書 v5.1 のポリシー）
            print(f"[KPIService] load_backtest_kpi_summary error: {e}")
            return {
                "profile": profile,
                "has_backtest": False,
                "current_month": None,
                "current_month_return": 0.0,
                "current_month_progress": 0.0,
                "monthly": [],
                "avg_return_pct": 0.0,
                "max_dd_pct": 0.0,
                "win_rate": 0.0,
                "avg_pf": 0.0,
            }

    # 仕様書 v5 の「compute_monthly_dashboard(profile)」実体
    def compute_monthly_dashboard(self, profile: str) -> KpiDashboard:
        """
        月次ダッシュボードデータを計算する（仕様書 v5.1 準拠）。

        Parameters
        ----------
        profile : str
            プロファイル名

        Returns
        -------
        KpiDashboard
            ダッシュボードデータ（例外時は空データを返す）
        """
        try:
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
        except Exception as e:
            # 例外はすべて握り、安全なデータを返す（仕様書 v5.1 のポリシー）
            print(f"[KPIService] compute_monthly_dashboard error: {e}")
            return KpiDashboard(
                profile=profile,
                has_backtest=False,
                current_month=None,
                current_month_return=0.0,
                current_month_progress=0.0,
                monthly=[],
            )

    def compute_target_progress(
        self,
        return_pct: float,
        target: float = TARGET_MONTHLY_RETURN,
    ) -> float:
        """
        月3%に対する進捗率（0.0〜2.0=0〜200%）を返す。

        Parameters
        ----------
        return_pct : float
            月次リターン（小数形式、例: 0.03 = 3%）
        target : float, optional
            目標リターン（デフォルト: 0.03 = 3%）

        Returns
        -------
        float
            進捗率（0.0〜2.0）。None や NaN の場合は 0.0 を返す。
        """
        try:
            # None や NaN の場合は 0.0 を返す
            if return_pct is None:
                return 0.0

            import math
            if math.isnan(return_pct):
                return 0.0

            return_pct = float(return_pct)
            target = float(target)

            if target <= 0:
                return 0.0

            raw = return_pct / target
            # 仕様上 0〜200% を想定しているので 0.0〜2.0 にクリップ
            # （ゲージ側で ×100 してパーセント表示）
            return max(0.0, min(2.0, raw))
        except (TypeError, ValueError, ZeroDivisionError):
            # 例外はすべて握り、安全な値を返す（仕様書 v5.1 のポリシー）
            return 0.0

    def compute_trade_stats(self, profile: str) -> dict:
        """
        バックテスト or 実運用のトレード結果から
        勝率・PF・平均RRなどを算出する。

        現段階ではバックテスト側の
        backtests/{profile}/trades.csv を主な情報源とする。
        将来、実運用ログ（decisionsや専用トレードログ）が
        整備されたらここに統合する。
        """
        try:
            import math

            trades_path = self.backtest_root / profile / "trades.csv"
            if not trades_path.exists():
                # トレード情報が無い場合は安全なデフォルト
                return {
                    "win_rate": 0.0,
                    "pf": 0.0,
                    "avg_rr": 0.0,
                    "total_trades": 0,
                }

            df = pd.read_csv(trades_path)

            # 必須カラムチェック
            if "pnl" not in df.columns:
                return {
                    "win_rate": 0.0,
                    "pf": 0.0,
                    "avg_rr": 0.0,
                    "total_trades": 0,
                    "error": "trades.csv に pnl 列がありません",
                }

            # RR 列は任意
            rr_col = "rr" if "rr" in df.columns else None

            # NaN を落とす（安全側）
            df = df.copy()
            df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce")
            df = df.dropna(subset=["pnl"])

            if df.empty:
                return {
                    "win_rate": 0.0,
                    "pf": 0.0,
                    "avg_rr": 0.0,
                    "total_trades": 0,
                }

            wins = df[df["pnl"] > 0]["pnl"]
            losses = df[df["pnl"] < 0]["pnl"]

            total_trades = len(df)
            win_rate = len(wins) / total_trades if total_trades > 0 else 0.0

            if len(losses) > 0:
                pf = wins.sum() / abs(losses.sum()) if abs(losses.sum()) > 0 else math.inf
            else:
                # 負けが一度もない場合は PF を大きな値で扱う
                pf = math.inf

            if rr_col is not None:
                df_rr = pd.to_numeric(df[rr_col], errors="coerce").dropna()
                avg_rr = float(df_rr.mean()) if not df_rr.empty else 0.0
            else:
                avg_rr = 0.0

            return {
                "win_rate": float(win_rate),
                "pf": float(pf),
                "avg_rr": float(avg_rr),
                "total_trades": int(total_trades),
            }

        except Exception as e:
            # GUIには例外を渡さず、安全なデフォルト＋エラー文字列だけ返す
            return {
                "win_rate": 0.0,
                "pf": 0.0,
                "avg_rr": 0.0,
                "total_trades": 0,
                "error": str(e),
            }

    # --- 内部関数 ---

    def _load_monthly_returns(self, profile: str) -> pd.DataFrame:
        """
        BacktestRun が出力した monthly_returns.csv を読み込む。

        Parameters
        ----------
        profile : str
            プロファイル名

        Returns
        -------
        pd.DataFrame
            monthly_returns.csv の内容。ファイルが存在しない場合は空 DataFrame。
            例外時も空 DataFrame を返す。
        """
        try:
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
            df["return_pct"] = pd.to_numeric(df["return_pct"], errors="coerce").fillna(0.0).astype(float)
            df["max_dd_pct"] = pd.to_numeric(df["max_dd_pct"], errors="coerce").fillna(0.0).astype(float)
            df["total_trades"] = pd.to_numeric(df["total_trades"], errors="coerce").fillna(0).astype(int)
            df["pf"] = pd.to_numeric(df["pf"], errors="coerce").fillna(0.0).astype(float)

            return df
        except Exception as e:
            # 例外はすべて握り、空 DataFrame を返す（仕様書 v5.1 のポリシー）
            print(f"[KPIService] _load_monthly_returns error: {e}")
            return pd.DataFrame(
                columns=[
                    "year_month",
                    "return_pct",
                    "max_dd_pct",
                    "total_trades",
                    "pf",
                ]
            )

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
