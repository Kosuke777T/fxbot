from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, List
import csv
import threading


@dataclass
class ProfileStat:
    """プロファイルごとの簡易統計（v1）

    v1 では backtests/{profile}/monthly_returns.csv の
    「最後の1行（最新月）」だけを使う。
    """
    profile: str
    year_month: str
    return_pct: float
    max_dd_pct: float
    total_trades: int
    pf: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "year_month": self.year_month,
            "return_pct": self.return_pct,
            "max_dd_pct": self.max_dd_pct,
            "total_trades": self.total_trades,
            "pf": self.pf,
            # 将来: winrate, dd_flag などを追加してもよい
        }


class ProfileStatsService:
    """バックテスト結果からプロファイル統計を読み込むサービス。

    - データソース:
      backtests/{profile}/monthly_returns.csv

    - 仕様書 v5 の公式フォーマットを前提とする:
      year_month, return_pct, max_dd_pct, total_trades, pf
    """

    # v1 のデフォルト対象プロファイル
    _DEFAULT_PROFILES: List[str] = [
        "michibiki_std",
        "michibiki_aggr",
    ]

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        # services/ → app/ → プロジェクトルート(fxbot)
        # プロジェクトルートを base_dir にする
        if base_dir is None:
            # profile_stats_service.py → services → app → fxbot
            base_dir = Path(__file__).resolve().parents[2]
        self._base_dir = base_dir
        self._cache: Dict[str, ProfileStat] = {}
        self._lock = threading.Lock()

    # -----------------------
    # 内部ヘルパ
    # -----------------------
    def _backtest_csv_path(self, profile: str) -> Path:
        return self._base_dir / "backtests" / profile / "monthly_returns.csv"

    def _load_latest_row(self, profile: str) -> Optional[ProfileStat]:
        """指定プロファイルの monthly_returns.csv から
        「最後の行（最新月）」を読み込んで ProfileStat に変換する。
        """
        path = self._backtest_csv_path(profile)
        if not path.exists():
            return None

        last_row: Optional[dict] = None
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                last_row = row

        if not last_row:
            return None

        def _f(name: str, default: float = 0.0) -> float:
            v = last_row.get(name)
            if v in (None, ""):
                return default
            try:
                return float(v)
            except ValueError:
                return default

        year_month = last_row.get("year_month") or ""
        return_pct = _f("return_pct", 0.0)
        max_dd_pct = _f("max_dd_pct", 0.0)
        total_trades = int(_f("total_trades", 0.0))
        pf = _f("pf", 0.0)

        return ProfileStat(
            profile=profile,
            year_month=year_month,
            return_pct=return_pct,
            max_dd_pct=max_dd_pct,
            total_trades=total_trades,
            pf=pf,
        )

    # -----------------------
    # 公開 API
    # -----------------------
    def get_profile_stats(
        self,
        profiles: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """プロファイル名のリストを受け取り、
        {profile_name: {...}} 形式で dict を返す。

        - profiles が None の場合はデフォルト
        - monthly_returns.csv が無いプロファイルは無視
        """
        if profiles is None:
            profiles = list(self._DEFAULT_PROFILES)

        results: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            for name in profiles:
                stat = self._cache.get(name)
                if stat is None:
                    stat = self._load_latest_row(name)
                    if stat:
                        self._cache[name] = stat
                if stat:
                    results[name] = stat.to_dict()

        return results


# シングルトンっぽく使うためのヘルパ
_profile_stats_service: Optional[ProfileStatsService] = None


def get_profile_stats_service() -> ProfileStatsService:
    global _profile_stats_service
    if _profile_stats_service is None:
        _profile_stats_service = ProfileStatsService()
    return _profile_stats_service

