"""
app.core.strategy_profile

ミチビキの「戦略プロファイル」を一元管理するモジュール。

- プロファイル ID (profile.name) は
  - config
  - backtests/{profile}/...
  - JobScheduler のコマンド
  などで共通して使うキーとする。

まずは M-A2 対応として「michibiki_std」（ミチビキ標準プロファイル）だけを定義する。
"""

from __future__ import annotations

from dataclasses import dataclass
from core.risk import LotSizingResult, compute_lot_size_from_atr

from pathlib import Path
from typing import Dict


@dataclass(frozen=True)
class StrategyProfile:
    """戦略プロファイル 1 件分の定義。

    将来的には、ここに
    - 使用するモデル名
    - 特徴量セット名
    - 追加フィルタ設定
    などを足していく。
    """

    # プロファイル ID（フォルダ名 / 設定キーに使う）
    name: str

    # 人間が読む用の説明
    description: str

    # 取引対象
    symbol: str  # 例: "USDJPY" / "USDJPY-"
    timeframe: str  # 例: "M5", "M15"

    # KPI 目標
    target_monthly_return: float  # 例: 0.03 (= +3%)
    max_monthly_dd: float  # 例: -0.20 (= -20%)

    # ATR ベース戦略の主要パラメータ
    atr_period: int
    atr_mult_entry: float
    atr_mult_sl: float

    # Walk-Forward 窓
    wf_train_months: int
    wf_test_months: int

    @property
    def backtest_root(self) -> Path:
        """バックテスト結果を置くルートフォルダを返す。

        例:
            backtests/michibiki_std
        """
        return Path("backtests") / self.name

    @property
    def monthly_returns_path(self) -> Path:
        """monthly_returns.csv の標準パス。

        M-A1 で決めた「backtests/{profile}/monthly_returns.csv」と整合させる。
        """
        return self.backtest_root / "monthly_returns.csv"

    def compute_lot_size_from_atr(
        self,
        *,
        equity: float,
        atr: float,
        tick_value: float,
        tick_size: float,
        expected_trades_per_month: int = 40,
        worst_case_trades_for_dd: int = 10,
        avg_r_multiple: float = 0.6,
        min_lot: float = 0.01,
        max_lot: float = 1.0,
    ) -> LotSizingResult:
        """
        このプロファイルに設定された target_monthly_return / max_monthly_dd / atr_mult_sl を使用して
        推奨ロットを計算するヘルパーメソッド。

        Parameters
        ----------
        equity:
            現在の口座残高 or 有効証拠金。
        atr:
            現在の ATR 値（価格単位）。
        tick_value, tick_size:
            MT5 の symbol_info(...).trade_tick_value / trade_tick_size 等から取得した値。
        expected_trades_per_month, worst_case_trades_for_dd, avg_r_multiple, min_lot, max_lot:
            ロット計算の前提パラメータ。必要なら外から上書き可能。

        Returns
        -------
        LotSizingResult
        """
        return compute_lot_size_from_atr(
            equity=equity,
            atr=atr,
            atr_mult_sl=self.atr_mult_sl,
            target_monthly_return=self.target_monthly_return,
            max_monthly_dd=self.max_monthly_dd,
            tick_value=tick_value,
            tick_size=tick_size,
            expected_trades_per_month=expected_trades_per_month,
            worst_case_trades_for_dd=worst_case_trades_for_dd,
            avg_r_multiple=avg_r_multiple,
            min_lot=min_lot,
            max_lot=max_lot,
        )


# ==== ミチビキ標準プロファイル (M-A2) ======================================


MICHIBIKI_STD = StrategyProfile(
    name="michibiki_std",
    description="ミチビキ標準プロファイル v1（USDJPY M5 / ATRベース / 月次3％目標）",
    symbol="USDJPY",
    timeframe="M5",
    # KPI 目標
    target_monthly_return=0.03,  # +3%/月を狙う
    max_monthly_dd=-0.20,  # -20% 以内に収めたい
    # ATR戦略パラメータ（暫定値：あとでWFOでチューニング）
    atr_period=14,
    atr_mult_entry=1.5,
    atr_mult_sl=3.0,
    # Walk-Forward 窓（12ヶ月訓練 -> 1ヶ月テスト を基本とする）
    wf_train_months=12,
    wf_test_months=1,
)


# 今後プロファイルを増やす場合はここに追加していく
_PROFILES: Dict[str, StrategyProfile] = {
    MICHIBIKI_STD.name: MICHIBIKI_STD,
}


def get_profile(name: str = "michibiki_std") -> StrategyProfile:
    """プロファイル ID から StrategyProfile を取得する。

    未定義の名前が来た場合は KeyError ではなく ValueError にして、
    GUI や CLI でメッセージを出しやすくしておく。
    """
    try:
        return _PROFILES[name]
    except KeyError as exc:  # pragma: no cover - 単純なエラーパス
        known = ", ".join(sorted(_PROFILES.keys()))
        raise ValueError(f"未知のプロファイル名です: {name!r} (known: {known})") from exc


def list_profiles() -> Dict[str, StrategyProfile]:
    """定義済みプロファイル一覧を dict で返す（読み取り専用想定）。"""
    return dict(_PROFILES)
