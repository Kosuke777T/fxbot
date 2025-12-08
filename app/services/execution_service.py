# app/services/execution_service.py
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.filter_service import evaluate_entry
from app.services.ai_service import get_ai_service
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

        # --- 2) フィルタ評価 ---
        ok, reasons = evaluate_entry({
            "timestamp": datetime.now(),
            "atr": features.get("atr"),
            "volatility": features.get("volatility"),
            "trend_strength": features.get("trend_strength"),
            "consecutive_losses": features.get("consecutive_losses", 0),
            "profile_stats": features.get("profile_stats", {}),
        })

        # --- 3) decisions.jsonl へ統合出力 ---
        DecisionsLogger.log({
            "ts_jst": now_jst_iso(),
            "type": "decision",
            "symbol": symbol,
            "prob_buy": prob_buy,
            "prob_sell": prob_sell,
            "filter_pass": ok,
            "filter_reasons": reasons,  # STEP8 の重要ポイント
            "filters": {
                "atr": features.get("atr"),
                "volatility": features.get("volatility"),
                "trend_strength": features.get("trend_strength"),
                "consecutive_losses": features.get("consecutive_losses", 0),
            }
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

