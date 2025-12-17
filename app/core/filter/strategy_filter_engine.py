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

    # --- プロファイル自動切替用の閾値 ---
    # プロファイル評価に必要な最小トレード数
    profile_switch_min_trades: int = 30
    # 現在プロファイルとの必要勝率差（例: 0.05 = 5%）
    profile_switch_winrate_gap: float = 0.05
    # 候補プロファイルの最低勝率（これ未満は切り替え対象にしない）
    profile_switch_min_winrate: float = 0.50


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

        # ⑤ 連敗回避フィルタ（level >= 3、T-21 本番仕様）
        # 評価順: 時間帯 → ATR → ボラ → トレンド → 連敗回避（5番目）
        if filter_level >= 3:
            if not self._check_losing_streak(ctx, reasons):
                # _check_losing_streak 内で既に reasons に "losing_streak" を追加済み
                pass

        # ⑥ プロファイル自動切替（結果には影響させない、情報のみ記録）
        if filter_level >= 3:
            self._check_profile_autoswitch(ctx, reasons)

        ok = len(reasons) == 0
        return ok, reasons

    # ============================================================
    # 個別フィルタ（ここは v0 ロジック。閾値は後で profile/config に逃がせる設計）
    # ============================================================

    def _check_time_window(self, ctx: Dict) -> bool:
        """取引時間帯フィルタ

        ctx["timestamp"]: datetime
        ctx["time_window"]: {"start": int, "end": int} を受け取れれば優先
        time_window が未設定/空/不正なら「ウィンドウ制限なし（pass）」とする
        """
        ts: datetime | None = ctx.get("timestamp")
        if ts is None or not isinstance(ts, datetime):
            # 時刻不明な場合は安全のため NG にしておく
            return False

        window = ctx.get("time_window")
        # time_window が未設定/空/不正なら「ウィンドウ制限なし（pass）」
        if not window or not isinstance(window, dict):
            return True

        # start/end が None または空の場合は pass
        start_hour_raw = window.get("start")
        end_hour_raw = window.get("end")
        if start_hour_raw is None or end_hour_raw is None:
            return True

        # start/end を int に変換（失敗したら pass）
        try:
            start_hour = int(start_hour_raw)
            end_hour = int(end_hour_raw)
        except (TypeError, ValueError):
            return True

        # start == end の場合は pass（終日制限なしとみなす）
        if start_hour == end_hour:
            return True

        hour = ts.hour
        return start_hour <= hour <= end_hour

    def _check_atr(self, ctx: Dict) -> bool:
        """ATR フィルタ

        ctx["atr"]: float
        ctx["atr_band"]: {"min": float, "max": float} を優先利用
        無ければ 0.02〜5.0 をデフォルトとする

        値が None/NaN の場合は pass（未設定扱い、全落ち回避）
        """
        atr_raw = ctx.get("atr")
        # atr が None/NaN の場合は pass（未設定扱い）
        if atr_raw is None:
            return True

        # NaN チェック（float変換後）
        try:
            atr = float(atr_raw)
            import math
            if math.isnan(atr):
                return True
        except (TypeError, ValueError):
            return True

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

        値が None/NaN の場合は pass（未設定扱い、全落ち回避）
        """
        vol_raw = ctx.get("volatility")
        # volatility が None/NaN の場合は pass（未設定扱い）
        if vol_raw is None:
            return True

        # NaN チェック（float変換後）
        try:
            vol = float(vol_raw)
            import math
            if math.isnan(vol):
                return True
        except (TypeError, ValueError):
            return True

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

        値が None/NaN の場合は pass（未設定扱い、全落ち回避）
        """
        strength_raw = ctx.get("trend_strength")
        # trend_strength が None/NaN の場合は pass（未設定扱い）
        if strength_raw is None:
            return True

        # NaN チェック（float変換後）
        try:
            strength = float(strength_raw)
            import math
            if math.isnan(strength):
                return True
        except (TypeError, ValueError):
            return True

        band = ctx.get("trend_band") or {}
        min_t = band.get("min", -0.8)
        max_t = band.get("max", 0.8)

        return min_t <= strength <= max_t

    def _check_losing_streak(self, ctx: Dict, reasons: List[str]) -> bool:
        """
        連敗回避フィルタ（T-21 本番仕様）:

        【動作仕様】
        1. config.losing_streak_limit が 0 以下ならフィルタ無効（常に True を返す）
        2. consecutive_losses >= limit なら NG（False を返し、reasons に "losing_streak" を追加）
        3. consecutive_losses が None または変換できない場合はブロックしない（安全側：他フィルタに任せる）

        【評価順序】
        evaluate() の評価順（時間帯 → ATR → ボラ → トレンド → 連敗回避）の 5 番目に正式追加

        Parameters
        ----------
        ctx : dict
            EntryContext 相当の辞書。必須キー: "consecutive_losses" (int | None)
        reasons : list[str]
            NG の場合に理由を追加するリスト（in-place で更新）

        Returns
        -------
        bool
            True: フィルタ通過（エントリー許可）
            False: フィルタNG（エントリー不可、reasons に "losing_streak" が追加済み）
        """
        # 1. config.losing_streak_limit が 0 以下ならフィルタ無効
        limit = getattr(self.config, "losing_streak_limit", 0)
        if not limit or limit <= 0:
            # 0 以下なら機能自体を無効として扱う（常に通過）
            return True

        # 2. consecutive_losses >= limit なら NG & 理由 "losing_streak"
        raw_value = ctx.get("consecutive_losses")
        if raw_value is None:
            # 情報が来ていない場合はブロックしない（安全側：他フィルタに任せる）
            return True

        try:
            losses = int(raw_value)
        except (TypeError, ValueError):
            # 変換できない値が来た場合もブロックはしない（安全側：他フィルタに任せる）
            return True

        # 連敗数が limit 以上なら NG
        if losses >= limit:
            reasons.append("losing_streak")
            return False

        # 連敗数が limit 未満なら通過
        return True

    def _check_profile_autoswitch(self, ctx: dict, reasons: list[str]) -> None:
        """
        profile_stats から最適プロファイルを推奨し、
        必要なら 'profile_switch:from->to' を reasons に追加する。

        - filter_level < 3 のときは何もしない
        - config.yaml の filters.profile_auto_switch.enabled が false のときは何もしない
        - ExecutionService 側で actual switch を行う前提
        """
        filter_level = ctx.get("filter_level", 0)
        if filter_level < 3:
            return

        # 設定を読み込む
        try:
            from app.core.config_loader import load_config
            config = load_config()
            filters_cfg = config.get("filters", {})
            switch_cfg = filters_cfg.get("profile_auto_switch", {})
            enabled = switch_cfg.get("enabled", False)
            if not enabled:
                return

            # 閾値を設定から取得（デフォルト値あり）
            min_trades = switch_cfg.get("min_trades", 30)
            winrate_gap_min = switch_cfg.get("winrate_gap_min", 0.05)
            pf_min = switch_cfg.get("pf_min", 1.05)
        except Exception:
            # 設定読み込み失敗時は機能を無効化
            return

        stats = ctx.get("profile_stats") or {}
        current = stats.get("current_profile")
        profiles = stats.get("profiles") or {}

        if not current or current not in profiles:
            # current_profile 不明なら何もしない
            return

        cur = profiles.get(current, {})
        cur_wr = float(cur.get("winrate", 0.0))
        cur_pf = float(cur.get("pf", 0.0))

        best_name = current
        best_score = cur_wr  # シンプルに winrate をスコアとする

        for name, p in profiles.items():
            trades = int(p.get("trades", 0))
            wr = float(p.get("winrate", 0.0))
            pf = float(p.get("pf", 0.0))

            if name == current:
                continue

            if trades < min_trades:
                continue

            if wr - cur_wr < winrate_gap_min:
                continue

            if pf < pf_min:
                continue

            # より高い winrate のものを選ぶ
            if wr > best_score:
                best_name = name
                best_score = wr

        if best_name != current:
            reasons.append(f"profile_switch:{current}->{best_name}")
