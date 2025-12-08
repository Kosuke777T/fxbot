# app/core/strategy_filter_engine.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple


class StrategyFilterEngine:
    """ミチビキ v5.1 フィルタエンジン（コア層）

    - EditionGuard には依存しない
    - filter_level は services 層から引数として渡される
    - 評価順序は v5.1 の仕様に固定
      ① 取引時間帯
      ② ATR
      ③ ボラティリティ帯
      ④ トレンド強度
      ⑤ 連敗回避
      ⑥ プロファイル自動切替
    """

    def evaluate(self, ctx: Dict, filter_level: int) -> Tuple[bool, List[str]]:
        """エントリー可否を評価する

        Parameters
        ----------
        ctx : dict
            EntryContext 相当の辞書
        filter_level : int
            EditionGuard から渡される 0〜3

        Returns
        -------
        ok : bool
            True のときエントリー許可
        reasons : list[str]
            False のとき NG になった理由の一覧
        """
        reasons: List[str] = []

        # level 0 → フィルタ無し（常に通過）
        if filter_level <= 0:
            return True, []

        # ① 取引時間帯（level >= 1）
        if filter_level >= 1:
            if not self._check_time_window(ctx):
                reasons.append("time_window")

        # ② ATR（level >= 2）
        if filter_level >= 2:
            if not self._check_atr(ctx):
                reasons.append("atr")

        # ③〜⑤ Expert（level >= 3）
        if filter_level >= 3:
            if not self._check_volatility(ctx):
                reasons.append("volatility")

            if not self._check_trend(ctx):
                reasons.append("trend")

            if not self._check_loss_streak(ctx):
                reasons.append("loss_streak")

            # ⑥ プロファイル自動切替（結果には影響させない）
            self._auto_switch_profile(ctx)

        ok = len(reasons) == 0
        return ok, reasons

    # ============================================================
    # 個別フィルタ（ここは v0 ロジック。閾値は後で profile/config に逃がせる設計）
    # ============================================================

    def _check_time_window(self, ctx: Dict) -> bool:
        """取引時間帯フィルタ

        ctx["timestamp"]: datetime
        ctx["time_window"]: {"start": int, "end": int} を受け取れれば優先
        なければ 8〜22 時をデフォルトとする
        """
        ts: datetime | None = ctx.get("timestamp")
        if ts is None or not isinstance(ts, datetime):
            # 時刻不明な場合は安全のため NG にしておく
            return False

        window = ctx.get("time_window") or {}
        start_hour = int(window.get("start", 8))
        end_hour = int(window.get("end", 22))

        hour = ts.hour
        return start_hour <= hour <= end_hour

    def _check_atr(self, ctx: Dict) -> bool:
        """ATR フィルタ

        ctx["atr"]: float
        ctx["atr_band"]: {"min": float, "max": float} を優先利用
        無ければ 0.02〜5.0 をデフォルトとする
        """
        atr = float(ctx.get("atr") or 0.0)
        band = ctx.get("atr_band") or {}
        min_atr = float(band.get("min", 0.02))
        max_atr = float(band.get("max", 5.0))

        # 0 以下はそもそも論外
        if atr <= 0:
            return False

        return min_atr <= atr <= max_atr

    def _check_volatility(self, ctx: Dict) -> bool:
        """ボラティリティ帯フィルタ

        ctx["volatility"]: float
        ctx["vol_band"]: {"min": float, "max": float} を優先利用
        v0 では min=0.3, max=None というイメージ
        """
        vol = float(ctx.get("volatility") or 0.0)
        band = ctx.get("vol_band") or {}
        min_vol = band.get("min", 0.3)
        max_vol = band.get("max")  # None なら上限なし

        if vol <= 0:
            return False

        if max_vol is None:
            return vol >= min_vol

        return min_vol <= vol <= max_vol

    def _check_trend(self, ctx: Dict) -> bool:
        """トレンド強度フィルタ

        ctx["trend_strength"]: float
        ctx["trend_band"]: {"min": float, "max": float} を優先利用
        v0 では -0.8〜0.8 を許容
        """
        strength = float(ctx.get("trend_strength") or 0.0)
        band = ctx.get("trend_band") or {}
        min_t = band.get("min", -0.8)
        max_t = band.get("max", 0.8)

        return min_t <= strength <= max_t

    def _check_loss_streak(self, ctx: Dict) -> bool:
        """連敗回避フィルタ

        ctx["consecutive_losses"]: int
        ctx["max_loss_streak"]: int を優先利用
        v0 では 3 連敗でストップ
        """
        streak = int(ctx.get("consecutive_losses") or 0)
        max_streak = int(ctx.get("max_loss_streak") or 3)

        return streak < max_streak

    def _auto_switch_profile(self, ctx: Dict) -> None:
        """プロファイル自動切替

        Expert 用。ここでは v0 のダミー実装。

        ctx["profile_stats"] などを解析して
        「どのプロファイルが優位か」を判定する予定だが、
        コア層なので、ここでは「フックだけ用意して何もしない」。
        """
        # ここで何か値を返すとレイヤーを侵食するので、何も返さない。
        _stats = ctx.get("profile_stats") or {}
        _ = _stats  # いずれ使う。今は警告避け。
        return
