# app/core/strategy_filter_engine.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple


@dataclass
class FilterConfig:
    """フィルタエンジンの設定"""

    # 連敗回避: この回数以上連敗したらエントリー停止（0 以下なら無効）
    losing_streak_limit: int = 0


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

    def __init__(self, config: FilterConfig | None = None):
        """初期化

        Parameters
        ----------
        config : FilterConfig, optional
            フィルタ設定。None の場合はデフォルト設定を使用
        """
        self.config = config or FilterConfig()

    def evaluate(self, ctx: Dict, filter_level: int) -> Tuple[bool, List[str]]:
        """エントリー可否を評価する

        【評価順序（v5.1 仕様に固定）】
        以下の順序で評価を行い、すべてのフィルタを評価してから結果を返す。
        複数の理由を記録するため、NG の場合でもすべてのフィルタを評価する。
        ① 取引時間帯（level >= 1）
        ② ATR（level >= 2）
        ③ ボラティリティ（level >= 3）
        ④ トレンド強度（level >= 3）
        ⑤ 連敗回避（level >= 3）
        ⑥ プロファイル自動切替（level >= 3、結果には影響しない）

        注: decisions.jsonl に複数の理由を記録するため、
        最初の NG で即座に返さず、すべてのフィルタを評価してから返す。

        【filter_level による制御（v5 仕様）】
        - level 0: フィルタ無し（常に通過）
        - level 1: Basic（時間帯のみ）
        - level 2: Pro（時間帯＋ATR）
        - level 3: Expert（全フィルタ：時間帯＋ATR＋ボラティリティ＋トレンド＋連敗回避＋プロファイル自動切替）

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
            False のとき NG になった理由の一覧（例: ["time_window", "atr"]）
            複数のフィルタが NG の場合は、すべての理由が含まれる
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

        # ③ ボラティリティ（level >= 3）
        if filter_level >= 3:
            if not self._check_volatility(ctx):
                reasons.append("volatility")

        # ④ トレンド強度（level >= 3）
        if filter_level >= 3:
            if not self._check_trend(ctx):
                reasons.append("trend")

        # ⑤ 連敗回避フィルタ（level >= 3）
        if filter_level >= 3:
            if not self._check_losing_streak(ctx, reasons):
                # _check_losing_streak 内で既に reasons に追加済み
                pass

        # ⑥ プロファイル自動切替（結果には影響させない）
        if filter_level >= 3:
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

    def _check_losing_streak(self, ctx: Dict, reasons: List[str]) -> bool:
        """
        連敗回避フィルタ:
        - config.losing_streak_limit > 0 のときだけ有効
        - entry_context["consecutive_losses"] が limit 以上なら NG
        - NG の場合は reasons に "losing_streak" を追加
        """
        limit = getattr(self.config, "losing_streak_limit", 0)
        if not limit or limit <= 0:
            # 0 以下なら機能自体を無効として扱う
            return True

        raw_value = ctx.get("consecutive_losses")
        if raw_value is None:
            # 情報が来ていない場合はブロックしない
            return True

        try:
            losses = int(raw_value)
        except (TypeError, ValueError):
            # 変換できない値が来た場合もブロックはしない（安全側：他フィルタに任せる）
            return True

        if losses >= limit:
            reasons.append("losing_streak")
            return False

        return True

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
