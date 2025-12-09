# app/services/execution_service.py
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.filter_service import evaluate_entry, _get_engine
from app.services.ai_service import get_ai_service
from app.services.loss_streak_service import get_consecutive_losses
from app.core.strategy_profile import get_profile
from app.services.edition_guard import filter_level
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
        ai = get_ai_service()
        pred = ai.predict(features)

        # ProbOut オブジェクトから確率を取得
        prob_buy = float(getattr(pred, "p_buy", 0.0))
        prob_sell = float(getattr(pred, "p_sell", 0.0))

        # 連敗数を取得（プロファイル名・シンボルは実際の変数名に合わせてください）
        profile_obj = get_profile("michibiki_std")
        profile_name = profile_obj.name if hasattr(profile_obj, "name") and profile_obj else "michibiki_std"
        consecutive_losses = get_consecutive_losses(profile_name, symbol)

        # --- 2) フィルタ評価 ---
        # EntryContext を作成（後で filters_dict にマージするため保持）
        entry_context = {
            "timestamp": datetime.now().isoformat(),  # ISO 形式に統一
            "atr": features.get("atr"),
            "volatility": features.get("volatility"),
            "trend_strength": features.get("trend_strength"),
            "consecutive_losses": consecutive_losses,
            "profile_stats": features.get("profile_stats", {}),
        }

        ok, reasons = evaluate_entry(entry_context)

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

        # v5.1 仕様に準拠した decisions.jsonl 出力
        DecisionsLogger.log({
            "ts_jst": now_jst_iso(),
            "type": "decision",
            "symbol": symbol,
            "filter_pass": ok,
            "filter_reasons": normalized_reasons,
            "filters": filters_dict,
            # 追加情報（後方互換性）
            "prob_buy": prob_buy,
            "prob_sell": prob_sell,
        })

        # --- 4) フィルタでNGの場合ここで終了 ---
        if not ok:
            return {"ok": False, "reasons": reasons}

        # --- 5) ここから先は売買判断（既存ロジック） ---
        # 発注ロジックはそのまま

        return {
            "ok": True,
            "reasons": [],
            "prob_buy": prob_buy,
            "prob_sell": prob_sell,
        }

