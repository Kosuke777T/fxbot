from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, List
import csv
import threading
import json
from datetime import datetime, timezone, timedelta


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


@dataclass
class ProfileStatsConfig:
    base_dir: Path = Path(".")

    @property
    def stats_dir(self) -> Path:
        return self.base_dir / "logs" / "profile_stats"


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

    # -----------------------
    # トレードベースの統計更新（新規追加）
    # -----------------------
    def _path(self, symbol: str) -> Path:
        """symbol: 'USDJPY-' を前提"""
        stats_dir = self._base_dir / "logs" / "profile_stats"
        stats_dir.mkdir(parents=True, exist_ok=True)
        return stats_dir / f"profile_stats_{symbol}.json"

    def load(self, symbol: str) -> dict[str, Any]:
        """プロファイル統計を読み込む"""
        path = self._path(symbol)
        if not path.exists():
            return {
                "symbol": symbol,
                "updated_at": None,
                "current_profile": None,
                "profiles": {},
            }
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def save(self, symbol: str, stats: dict[str, Any]) -> None:
        """プロファイル統計を保存する"""
        path = self._path(symbol)
        stats["symbol"] = symbol
        with path.open("w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

    def update_from_trade(
        self,
        symbol: str,
        profile_name: str,
        pnl: float,
    ) -> dict[str, Any]:
        """
        決済トレード1件から profile_stats を更新する。
        """
        stats = self.load(symbol)
        profiles = stats.setdefault("profiles", {})
        p = profiles.setdefault(
            profile_name,
            {
                "total_trades": 0,
                "win_trades": 0,
                "loss_trades": 0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "winrate": 0.0,
                "pf": 0.0,
            },
        )

        p["total_trades"] += 1
        if pnl > 0:
            p["win_trades"] += 1
            p["gross_profit"] += float(pnl)
        elif pnl < 0:
            p["loss_trades"] += 1
            p["gross_loss"] += float(pnl)

        # 派生値
        if p["total_trades"] > 0:
            p["winrate"] = p["win_trades"] / p["total_trades"]
        if p["gross_loss"] < 0:
            p["pf"] = p["gross_profit"] / abs(p["gross_loss"])
        else:
            p["pf"] = 0.0

        # current_profile は ExecutionService 側で更新する前提
        stats["updated_at"] = datetime.now(
            timezone(timedelta(hours=9))
        ).isoformat()

        self.save(symbol, stats)
        return stats

    def set_current_profile(self, symbol: str, profile_name: str) -> dict[str, Any]:
        """
        現在選択されているプロファイル名を更新する。

        - ExecutionService から呼ばれることを想定
        - updated_at を JST 現在時刻で更新
        """
        stats = self.load(symbol)
        stats["current_profile"] = profile_name
        stats["updated_at"] = datetime.now(
            timezone(timedelta(hours=9))
        ).isoformat()
        self.save(symbol, stats)
        return stats

    def get_summary_for_filter(self, symbol: str) -> dict[str, Any]:
        """
        フィルタエンジンに渡す軽量サマリを返す。
        """
        stats = self.load(symbol)
        profiles = stats.get("profiles", {})
        return {
            "current_profile": stats.get("current_profile"),
            "profiles": {
                name: {
                    "winrate": p.get("winrate", 0.0),
                    "pf": p.get("pf", 0.0),
                    "trades": p.get("total_trades", 0),
                }
                for name, p in profiles.items()
            },
        }


# シングルトンっぽく使うためのヘルパ
_profile_stats_service: Optional[ProfileStatsService] = None


def get_profile_stats_service() -> ProfileStatsService:
    global _profile_stats_service
    if _profile_stats_service is None:
        _profile_stats_service = ProfileStatsService()
    return _profile_stats_service

