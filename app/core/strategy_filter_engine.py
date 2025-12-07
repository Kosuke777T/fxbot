# app/core/strategy_filter_engine.py
"""
v5.1 フィルタエンジン

- コア層に属する
- EditionGuard には依存しない（filter_level は呼び出し側で管理）
- evaluate() は v5.1 仕様通り bool を返す
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Dict, Optional


@dataclass
class FilterConfig:
    # 時間帯
    start_hour: int = 0    # 取引開始時刻（含む）
    end_hour: int = 24     # 取引終了時刻（除く）

    # ATR / ボラティリティ
    min_atr: Optional[float] = None
    max_atr: Optional[float] = None

    low_vol_threshold: Optional[float] = None
    high_vol_threshold: Optional[float] = None

    # トレンド強度（絶対値で判定）
    min_trend_strength: Optional[float] = None

    # 連敗回避
    max_consecutive_losses: Optional[int] = None

    # プロファイル自動切替（STEP1 ではフラグだけ。実装は後続ステップ）
    enable_profile_autoswitch: bool = False


@dataclass
class EntryContext:
    timestamp: datetime
    atr: Optional[float] = None
    volatility: Optional[float] = None
    trend_strength: Optional[float] = None
    consecutive_losses: int = 0
    profile_stats: Dict[str, Any] = field(default_factory=dict)


class StrategyFilterEngine:
    """
    v5.1 フィルタエンジン

    - コア層に属する
    - EditionGuard には依存しない（filter_level は呼び出し側で管理）
    - evaluate() は v5.1 仕様通り bool を返す
    """

    def __init__(self, config: Optional[FilterConfig] = None) -> None:
        self.config = config or FilterConfig()
        # evaluate() 実行時の「落ちた理由」を簡易に保持
        self._last_reasons: list[str] = []

    @property
    def last_reasons(self) -> list[str]:
        """直近の evaluate() で NG になった理由一覧（ログ用）"""
        return list(self._last_reasons)

    # --- 公開API ---

    def evaluate(self, context: Dict[str, Any], filter_level: int = 3) -> bool:
        """
        エントリー可否を判定する。

        Parameters
        ----------
        context : dict
            Strategy / ExecutionService から渡されるエントリー情報。
            EntryContext 互換の dict を想定。
        filter_level : int
            0〜3。EditionGuard から渡される想定。
            0 = フィルタ無し, 1 = 時間帯のみ, 2 = 時間帯+ATR, 3 = 全フィルタ
        """
        self._last_reasons = []

        if filter_level <= 0:
            return True  # フィルタ無効

        ctx = self._normalize_context(context)

        # ① 時間帯
        if not self._check_time(ctx):
            self._last_reasons.append("time_window")
            return False

        # ② ATR（filter_level >= 2 のときだけ）
        if filter_level >= 2:
            if not self._check_atr(ctx):
                self._last_reasons.append("atr")
                return False

        # ③ ボラティリティ（filter_level >= 3 のとき）
        if filter_level >= 3:
            if not self._check_volatility(ctx):
                self._last_reasons.append("volatility")
                return False

            # ④ トレンド強度
            if not self._check_trend(ctx):
                self._last_reasons.append("trend")
                return False

            # ⑤ 連敗回避
            if not self._check_consecutive_losses(ctx):
                self._last_reasons.append("consecutive_losses")
                return False

            # ⑥ プロファイル自動切替（判定そのものは変えない。STEP1では True を返すだけ）
            self._apply_profile_autoswitch(ctx)

        return True

    # --- 内部ヘルパー ---

    def _normalize_context(self, context: Dict[str, Any]) -> EntryContext:
        """dict から EntryContext を構築するヘルパー。"""
        ts = context.get("timestamp")
        if not isinstance(ts, datetime):
            raise TypeError("EntryContext.timestamp must be datetime")

        return EntryContext(
            timestamp=ts,
            atr=context.get("atr"),
            volatility=context.get("volatility"),
            trend_strength=context.get("trend_strength"),
            consecutive_losses=int(context.get("consecutive_losses") or 0),
            profile_stats=context.get("profile_stats") or {},
        )

    def _check_time(self, ctx: EntryContext) -> bool:
        h = ctx.timestamp.hour
        start = self.config.start_hour
        end = self.config.end_hour
        if start == 0 and end == 24:
            return True
        if start <= end:
            return start <= h < end
        # 23〜5時などのまたぎに対応
        return h >= start or h < end

    def _check_atr(self, ctx: EntryContext) -> bool:
        atr = ctx.atr
        if atr is None:
            return True  # スキップ（後で方針を決める）
        if self.config.min_atr is not None and atr < self.config.min_atr:
            return False
        if self.config.max_atr is not None and atr > self.config.max_atr:
            return False
        return True

    def _check_volatility(self, ctx: EntryContext) -> bool:
        vol = ctx.volatility
        if vol is None:
            return True

        low = self.config.low_vol_threshold
        high = self.config.high_vol_threshold

        # low / high の解釈は運用ルールに合わせて後で微調整可能
        if low is not None and vol < low:
            return False
        if high is not None and vol > high:
            return False
        return True

    def _check_trend(self, ctx: EntryContext) -> bool:
        strength = ctx.trend_strength
        if strength is None:
            return True
        if self.config.min_trend_strength is None:
            return True
        return abs(strength) >= self.config.min_trend_strength

    def _check_consecutive_losses(self, ctx: EntryContext) -> bool:
        if self.config.max_consecutive_losses is None:
            return True
        return ctx.consecutive_losses <= self.config.max_consecutive_losses

    def _apply_profile_autoswitch(self, ctx: EntryContext) -> None:
        """
        プロファイル自動切替のフック。

        STEP1 では「なにもしない」。
        T-22 で実装するときのためにフックだけ用意しておく。
        """
        if not self.config.enable_profile_autoswitch:
            return

        # NOTE: 実際の切替ロジックは app.services 側から呼び出す想定。
        # コア層では何も実装しない。
        return

