# app/services/execution_service.py
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.trade.decision_logic import decide_signal
from app.core.filter.strategy_filter_engine import StrategyFilterEngine
from app.services.filter_service import evaluate_entry, _get_engine, extract_profile_switch
from app.services.profile_stats_service import get_profile_stats_service
from app.services.ai_service import get_ai_service, get_model_metrics
from app.services.loss_streak_service import get_consecutive_losses
from app.core.strategy_profile import get_profile
from app.services.edition_guard import filter_level, EditionGuard
from core.utils.timeutil import now_jst_iso

# プロジェクトルート = app/services/ から 2 つ上
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = _PROJECT_ROOT / "logs" / "decisions"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _symbol_to_filename(symbol: str) -> str:
    """シンボル名を安全なファイル名に変換"""
    import re
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", symbol)
    return safe.strip("_") or "UNKNOWN"


def _normalize_filter_reasons(reasons: Any) -> list[str]:
    """
    filter_reasons を必ず list[str] に正規化する（v5.1 仕様）

    Parameters
    ----------
    reasons : Any
        None, str, list[str], tuple[str] など任意の型

    Returns
    -------
    list[str]
        正規化された理由のリスト
    """
    if reasons is None:
        return []
    if isinstance(reasons, str):
        return [reasons]
    if isinstance(reasons, (list, tuple)):
        return [str(r) for r in reasons if r is not None]
    # その他の型は空リストに
    return []


class DecisionsLogger:
    """決定ログ専用のロガークラス"""

    @staticmethod
    def log(record: Dict[str, Any]) -> None:
        """
        decisions.jsonl に 1 レコードを書き込む

        Parameters
        ----------
        record : dict
            decisions.jsonl に書き込むレコード
            必須キー: ts_jst, type, symbol
        """
        symbol = record.get("symbol", "UNKNOWN")
        fname = LOG_DIR / f"decisions_{_symbol_to_filename(symbol)}.jsonl"
        with open(fname, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")


class ExecutionService:
    """
    Live 用の実行サービス：
    - AI予測 → フィルタ評価 → decisions.jsonl 出力まで一貫処理
    - 売買判断と発注ロジックを含む
    """

    def __init__(self):
        """初期化"""
        self.profile_stats_service = get_profile_stats_service()
        self.filter_engine = StrategyFilterEngine()
        self.ai_service = get_ai_service()

    def _build_entry_context(
        self,
        symbol: str,
        features: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        EntryContext を構築する

        Parameters
        ----------
        symbol : str
            シンボル名
        features : dict
            特徴量の辞書

        Returns
        -------
        dict
            EntryContext
        """
        # EditionGuard から filter_level を取得
        guard = EditionGuard()
        current_filter_level = guard.filter_level()

        # 連敗数を取得
        profile_obj = get_profile("michibiki_std")
        profile_name = profile_obj.name if hasattr(profile_obj, "name") and profile_obj else "michibiki_std"
        consecutive_losses = get_consecutive_losses(profile_name, symbol)

        # EntryContext を作成
        entry_context = {
            "timestamp": datetime.now().isoformat(),  # ISO 形式に統一
            "atr": features.get("atr"),
            "volatility": features.get("volatility"),
            "trend_strength": features.get("trend_strength"),
            "consecutive_losses": consecutive_losses,
            "filter_level": current_filter_level,
        }

        # --- ProfileStats の追加 ---
        try:
            profile_stats = self.profile_stats_service.load(symbol)
        except Exception:
            profile_stats = {}

        entry_context["profile_stats"] = profile_stats

        return entry_context

    def process_tick(
        self,
        symbol: str,
        price: float,
        timestamp: datetime,
        features: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        1ティック分の処理をまとめて行うヘルパー。
        - 特徴量の構築（簡易版 or 別サービスに委譲）
        - execute_entry によるフィルタ＆発注
        - 結果 dict を返す

        Parameters
        ----------
        symbol : str
            シンボル名
        price : float
            現在価格
        timestamp : datetime
            タイムスタンプ
        features : dict, optional
            特徴量の辞書。指定されない場合は簡易版を構築

        Returns
        -------
        dict
            execute_entry の戻り値
        """
        # 1) 特徴量の準備
        if features is None:
            # 簡易版：最低限の特徴量を構築
            features = {
                "price": float(price),
                # TODO: 実装済みの FeatureBuilder があるならそちらを呼ぶ
                # 現時点では簡易版として price のみ
                "atr": None,
                "volatility": None,
                "trend_strength": None,
            }
        else:
            # 既存の特徴量を使用（price が含まれていない場合は追加）
            features = dict(features)
            if "price" not in features:
                features["price"] = float(price)

        # 2) execute_entry に委譲
        result = self.execute_entry(features, symbol=symbol)
        return result

    def _apply_profile_autoswitch(self, symbol: str, reasons: list[str]) -> None:
        """
        フィルタ結果の reasons からプロファイル自動切替指示を読み取り、
        ProfileStatsService に反映する。

        Parameters
        ----------
        symbol : str
            シンボル名
        reasons : list[str]
            フィルタエンジンから返された理由のリスト
        """
        if not reasons:
            return

        # profile_switch が含まれていれば apply_switch を呼ぶ
        for r in reasons:
            if r.startswith("profile_switch:"):
                try:
                    # "profile_switch:from->to" をパース
                    body = r.split("profile_switch:", 1)[1]
                    if "->" not in body:
                        continue
                    from_profile, to_profile = body.split("->", 1)

                    # ProfileStatsService に反映
                    stats = self.profile_stats_service.load(symbol)
                    stats["current_profile"] = to_profile
                    self.profile_stats_service.save(symbol, stats)

                    logger = logging.getLogger(__name__)
                    logger.info(
                        "Profile auto-switch applied: %s -> %s (symbol=%s)",
                        from_profile,
                        to_profile,
                        symbol,
                    )
                except Exception as e:
                    logger = logging.getLogger(__name__)
                    logger.warning(
                        "Failed to apply profile switch: %s (reason=%s)",
                        e,
                        r,
                    )

    def execute_entry(self, features: Dict[str, float], *, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        売買判断 → フィルタ判定 → decisions.jsonl 出力まで一貫処理

        Parameters
        ----------
        features : dict
            特徴量の辞書
        symbol : str, optional
            シンボル名（指定されない場合は設定から取得）

        Returns
        -------
        dict
            {
                "ok": bool,  # フィルタでOKならTrue
                "reasons": list[str],  # フィルタNGの場合の理由リスト
                "prob_buy": float,
                "prob_sell": float,
                ...
            }
        """
        # シンボルの取得
        if not symbol:
            try:
                from app.core.config_loader import load_config
                cfg = load_config()
                runtime_cfg = cfg.get("runtime", {}) if isinstance(cfg, dict) else {}
                symbol = runtime_cfg.get("symbol", "USDJPY-")
            except Exception:
                symbol = "USDJPY-"

        # --- 1) モデル予測 ---
        pred = self.ai_service.predict(features)

        # ProbOut オブジェクトから確率を取得
        prob_buy = getattr(pred, "p_buy", None)
        prob_sell = getattr(pred, "p_sell", None)

        # best_threshold を取得
        try:
            model_metrics = get_model_metrics()
            best_threshold = float(model_metrics.get("best_threshold", 0.52))
        except Exception:
            best_threshold = 0.52  # フォールバック

        # decide_signal を使用してシグナル判定
        signal = decide_signal(
            prob_buy=prob_buy,
            prob_sell=prob_sell,
            best_threshold=best_threshold,
        )

        # --- 2) EntryContext を構築（ProfileStats を含む） ---
        entry_context = self._build_entry_context(symbol, features)

        # EditionGuard から filter_level を取得
        guard = EditionGuard()
        current_filter_level = guard.filter_level()

        # --- 3) フィルタ評価 ---
        # StrategyFilterEngine を使用してフィルタ評価
        ok, reasons = self.filter_engine.evaluate(entry_context, filter_level=current_filter_level)

        # ★ここで profile 自動切替を反映
        self._apply_profile_autoswitch(symbol, reasons)

        # losing_streak_limit を取得
        try:
            losing_streak_limit_val = getattr(_get_engine().config, "losing_streak_limit", None)
        except Exception:
            losing_streak_limit_val = None

        # --- 3) decisions.jsonl へ統合出力（v5.1 仕様に準拠） ---
        # filter_reasons を正規化
        normalized_reasons = _normalize_filter_reasons(reasons)

        # EntryContext の全フィールドを含む filters_dict を構築
        filters_dict = {
            # filter_level（v5.1 仕様）
            "filter_level": filter_level(),
            # filter 結果（v5.1 仕様）
            "filter_pass": ok,
            "filter_reasons": normalized_reasons,
        }

        # --- EntryContext を filters に統合 ---
        ctx = entry_context or {}
        filters_dict["timestamp"] = ctx.get("timestamp")
        filters_dict["atr"] = ctx.get("atr")
        filters_dict["volatility"] = ctx.get("volatility")
        filters_dict["trend_strength"] = ctx.get("trend_strength")
        filters_dict["consecutive_losses"] = ctx.get("consecutive_losses")
        filters_dict["profile_stats"] = ctx.get("profile_stats")

        # losing_streak_limit を追加（設定されている場合）
        if losing_streak_limit_val is not None:
            filters_dict["losing_streak_limit"] = losing_streak_limit_val

        # blocked の理由（最初の理由 or None）を抽出（v5.1 仕様）
        blocked_reason = None
        if not ok and normalized_reasons:
            blocked_reason = normalized_reasons[0]
        filters_dict["blocked_reason"] = blocked_reason

        # v5.1 仕様に準拠した decisions.jsonl 出力（統一形式）
        # strategy 名を取得（AI サービスから取得、なければデフォルト）
        try:
            strategy_name = getattr(self.ai_service, "model_name", getattr(self.ai_service, "calibrator_name", "unknown"))
        except Exception:
            strategy_name = "unknown"

        # meta を取得（AI 予測結果から取得、なければ空 dict）
        meta_val = {}
        try:
            # ここでは AI 予測結果の meta を取得できないため、空 dict を返す
            # 将来的に AI 予測結果を保持する場合は、ここで取得する
            pass
        except Exception:
            pass

        # 決定アクションを判定
        if not signal.side or not ok:
            decision = "SKIP"
        else:
            decision = "ENTRY"

        # BacktestEngine と完全一致する decision_detail を構築
        decision_detail = {
            "action": decision,
            "side": signal.side,
            "signal": {
                "side": signal.side,
                "confidence": signal.confidence,
                "best_threshold": signal.best_threshold,
                "pass_threshold": signal.pass_threshold,
                "reason": signal.reason,
            },
            "filter_pass": ok,
            "filter_reasons": filters_dict.get("filter_reasons", []),
        }

        DecisionsLogger.log({
            "ts_jst": now_jst_iso(),
            "type": "decision",
            "symbol": symbol,
            "strategy": strategy_name,
            "prob_buy": signal.prob_buy,
            "prob_sell": signal.prob_sell,
            "filter_pass": ok,
            "filter_reasons": list(normalized_reasons or []),  # 必ず list に正規化
            "filters": filters_dict,  # EntryContext + filter結果を含む
            "meta": meta_val or {},  # 必ず dict
            "decision": decision,
            "decision_detail": decision_detail,
        })

        # --- 4) フィルタでNGの場合ここで終了 ---
        if not ok:
            return {"ok": False, "reasons": reasons}

        # --- 5) ここから先は売買判断（既存ロジック） ---
        # 発注ロジックはそのまま

        return {
            "ok": True,
            "reasons": [],
            "prob_buy": signal.prob_buy,
            "prob_sell": signal.prob_sell,
            "signal": signal,
        }

