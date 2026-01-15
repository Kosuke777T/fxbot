# app/services/execution_service.py
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 注意: 将来 decision_logic が肥大化した場合、
# services/decision_service.py 的な薄いラッパを挟む余地あり
from app.core.trade.decision_logic import decide_signal
from app.core.filter.strategy_filter_engine import StrategyFilterEngine
from app.services.filter_service import evaluate_entry, _get_engine, extract_profile_switch
from app.services.profile_stats_service import get_profile_stats_service
from app.services.ai_service import get_ai_service, get_model_metrics
from app.services.loss_streak_service import get_consecutive_losses
from app.core.strategy_profile import get_profile
from app.services.edition_guard import filter_level, EditionGuard
from app.services import trade_state
from core.utils.timeutil import now_jst_iso
from app.core import market

# プロジェクトルート = app/services/ から 2 つ上
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# AI判断ログ（ExecutionService 専用）
# 仕様書 v5.1 の「logs/decisions_*.jsonl」を拡張した実装として、
# シンボルごとに JSONL を出力する:
#   logs/decisions_{symbol}.jsonl
# 例: USDJPY- → logs/decisions_USDJPY-.jsonl
LOG_DIR = _PROJECT_ROOT / "logs" / "decisions"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _symbol_to_filename(symbol: str) -> str:
    """シンボル名を安全なファイル名に変換"""
    import re
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", symbol)
    return safe.strip("_") or "UNKNOWN"


def _compute_features_hash(features: Dict[str, float]) -> str:
    """
    features dictから安定ハッシュを生成する。

    Parameters
    ----------
    features : Dict[str, float]
        特徴量の辞書

    Returns
    -------
    str
        ハッシュ値（先頭10文字）
    """
    if not features:
        return ""
    try:
        # sort_keys=Trueで安定したハッシュを生成
        features_json = json.dumps(features, sort_keys=True, ensure_ascii=False)
        hash_obj = hashlib.sha1(features_json.encode("utf-8"))
        return hash_obj.hexdigest()[:10]
    except Exception:
        return ""


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


def _build_decision_context(
    prob_buy: Optional[float],
    prob_sell: Optional[float],
    strategy_name: str,
    best_threshold: float,
    filters_dict: Dict[str, Any],
    decision_detail: Dict[str, Any],
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """
    decision_context を構築する（判断材料を分離）

    Parameters
    ----------
    prob_buy : Optional[float]
        BUY確率
    prob_sell : Optional[float]
        SELL確率
    strategy_name : str
        戦略名
    best_threshold : float
        ベストしきい値
    filters_dict : Dict[str, Any]
        フィルタ情報
    decision_detail : Dict[str, Any]
        決定詳細
    meta : Dict[str, Any]
        メタ情報

    Returns
    -------
    Dict[str, Any]
        decision_context 辞書
    """
    return {
        "ai": {
            "prob_buy": prob_buy,
            "prob_sell": prob_sell,
            "model_name": strategy_name,
            "threshold": best_threshold,
        },
        "filters": {
            "filter_pass": filters_dict.get("filter_pass"),
            "filter_reasons": filters_dict.get("filter_reasons", []),
            "spread": filters_dict.get("spread"),
            "adx": filters_dict.get("adx"),
            "min_adx": filters_dict.get("min_adx"),
            "atr_pct": filters_dict.get("atr_pct"),
            "volatility": filters_dict.get("volatility"),
            "filter_level": filters_dict.get("filter_level"),
        },
        "decision": {
            "action": decision_detail.get("action"),
            "side": decision_detail.get("side"),
            "reason": decision_detail.get("reason"),
            "blocked_reason": decision_detail.get("blocked_reason"),
        },
        "meta": meta or {},
    }


def _ensure_decision_detail_minimum(dd: dict, decision: str, signal=None, ai_margin: float = 0.03) -> dict:
    """
    decision_detail に必要な最小限のキーが含まれるように補完する。

    Parameters
    ----------
    dd : dict
        decision_detail 辞書（既存の値は保持される）
    decision : str
        決定アクション（"ENTRY", "SKIP", "EXIT_SIMULATED" など）
    signal : SignalDecision, optional
        SignalDecision オブジェクト（prob_buy/prob_sell/threshold の取得に使用）
    ai_margin : float, optional
        AI判定のマージン（デフォルト: 0.03）

    Returns
    -------
    dict
        補完された decision_detail 辞書
    """
    # action / side
    dd.setdefault("action", decision)
    dd.setdefault("side", getattr(signal, "side", None) if signal is not None else None)

    # prob_buy / prob_sell
    if "prob_buy" not in dd or dd.get("prob_buy") is None:
        conf = getattr(signal, "confidence", None) if signal is not None else None
        if conf is not None:
            conf = float(conf)
            side = dd.get("side")
            # confidence が BUY側確率っぽい前提（既存実装に合わせる）
            dd["prob_buy"] = conf if side == "BUY" else 1.0 - conf

    if "prob_sell" not in dd or dd.get("prob_sell") is None:
        if dd.get("prob_buy") is not None:
            dd["prob_sell"] = 1.0 - float(dd["prob_buy"])

    # threshold
    if "threshold" not in dd or dd.get("threshold") is None:
        bt = getattr(signal, "best_threshold", None) if signal is not None else None
        if bt is not None:
            dd["threshold"] = float(bt)

    # ai_margin
    dd.setdefault("ai_margin", float(ai_margin))

    return dd


def _compute_size_decision_v1(
    *,
    stability: dict | None,
    condition_confidence: str | None,
    upside_potential: str | None,
) -> dict[str, object]:
    """
    T-44-4 (sizing) 仕様固定: size_decision を “段階語彙のみ” で確定する（services-only）。

    入力として使ってよい既存情報（新規生成禁止）:
    - condition_confidence: "LOW" | "MID" | "HIGH"
    - upside_potential:     "LOW" | "MID" | "HIGH"
    - stability.stable:     bool
      stability.score / stability.reasons は評価に使わない（観測用のまま）

    サイズ決定ルール（仕様固定・推測禁止）:
      1) stability.stable == False
         → multiplier = 0.5 / reason="unstable_state"
      2) stability.stable == True の場合のみ
         - condition_confidence=HIGH かつ upside_potential=HIGH
             → 1.5 / "high_confidence_high_upside"
         - condition_confidence=LOW または upside_potential=LOW
             → 0.5 / "low_confidence_or_low_upside"
         - 上記以外（MID/MID, 未設定, 想定外値 等）
             → 1.0 / "baseline_conditions"

    返り値（公式語彙のみ）:
      {"multiplier": 0.5|1.0|1.5, "reason": str}
    """
    st = stability if isinstance(stability, dict) else {}
    stable = bool(st.get("stable", False))

    if not stable:
        return {"multiplier": 0.5, "reason": "unstable_state"}

    conf = str(condition_confidence or "").upper()
    up = str(upside_potential or "").upper()

    if conf == "HIGH" and up == "HIGH":
        return {"multiplier": 1.5, "reason": "high_confidence_high_upside"}
    if conf == "LOW" or up == "LOW":
        return {"multiplier": 0.5, "reason": "low_confidence_or_low_upside"}
    return {"multiplier": 1.0, "reason": "baseline_conditions"}


def _features_hash_from_record(record: dict) -> str | None:
    """
    record 辞書から features を抽出してハッシュを生成する。

    Parameters
    ----------
    record : dict
        decisions ログレコード

    Returns
    -------
    str | None
        ハッシュ値（先頭10文字）、features が見つからない場合は None
    """
    feats = None
    if isinstance(record.get("features"), dict):
        feats = record.get("features")
    else:
        ec = record.get("entry_context")
        if isinstance(ec, dict) and isinstance(ec.get("features"), dict):
            feats = ec.get("features")

    if not isinstance(feats, dict) or not feats:
        return None

    # 順序でブレないようにソートしてJSON化 → sha1
    payload = json.dumps(feats, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


class DecisionsLogger:
    """決定ログ専用のロガークラス"""

    @staticmethod
    def log(record: Dict[str, Any]) -> None:
        """
        ExecutionService 用 AI判断ログ (decisions.jsonl) を 1 レコード追記する。

        ファイルパス
        ------------
        logs/decisions_YYYY-MM-DD.jsonl
        例:
            symbol = "USDJPY-"
            → logs/decisions_2025-12-29.jsonl  # 例（JST日付）
        レコード形式 (v5.1 ExecutionService 版・標準形)
        ----------------------------------------------
        ExecutionService から書き込まれるレコードは、基本的に次の構造を持つ::

            {
                "ts_jst": "2025-12-11T19:59:32+09:00",
                "type": "decision",
                "symbol": "USDJPY-",

                "strategy": "LightGBM_clf",   # self.ai_service.model_name など（例示）
                "prob_buy": 0.5187,           # 実際の値（固定値ではない、例示）
                "prob_sell": 0.4813,          # 実際の値（固定値ではない、例示）

                "filter_pass": false,
                "filter_reasons": ["time_window", "atr", "volatility"],

                "filters": {
                    "timestamp": "...",            # EntryContext.timestamp
                    "atr": 0.0002,
                    "volatility": 0.5,
                    "trend_strength": 0.3,
                    "consecutive_losses": 0,
                    "profile_stats": {...},
                    "filter_level": 3,
                    "filter_reasons": ["time_window", "atr", "volatility"],
                    "blocked_reason": "time_window"   # or None
                },

                "meta": {},                        # 予備フィールド

                "decision": "SKIP",                # "ENTRY" or "SKIP"

                "decision_detail": {
                    "action": "SKIP",              # decision と同じ
                    "side": "BUY",                 # 信号が指した方向（ENTRY/ SKIP 共通）

                    "signal": {
                        "side": "BUY",
                        "confidence": 0.5187,      # prob_buy or prob_sell
                        "best_threshold": 0.45,
                        "pass_threshold": true,    # しきい値を満たしたか
                        "reason": "threshold_ok"   # または "below_threshold" など
                    },

                    # フィルタ結果（上位と同じ値をミラーする）
                    "filter_pass": false,
                    "filter_reasons": ["time_window", "atr", "volatility"]
                }
            }

        備考
        ----
        - BacktestEngine が出力する decisions.jsonl とキー構造を揃えることを目的とする。
        - 古いバージョンのログには追加キー (ai, model, cb など) が混在するが、
          v5.1 以降は上記のフィールドを標準とする。
        """
        # execution_stub の _write_decision_log を使用（validate_runtime を含む）
        from app.services.execution_stub import _write_decision_log
        symbol = record.get("symbol", "UNKNOWN")
        # features_hash が無い場合は自動計算して埋める
        if "features_hash" not in record or not record.get("features_hash"):
            h = _features_hash_from_record(record)
            if h:
                record["features_hash"] = h
        # _write_decision_log を呼ぶ（validate_runtime が含まれる）
        _write_decision_log(symbol, record)


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
        # 擬似ポジション（dry_run モード用）
        self._sim_pos: Optional[Dict[str, Any]] = None

    def _build_entry_context(
        self,
        symbol: str,
        features: Dict[str, float],
        timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        EntryContext を構築する

        Parameters
        ----------
        symbol : str
            シンボル名
        features : dict
            特徴量の辞書
        timestamp : datetime, optional
            タイムスタンプ（指定されない場合は現在時刻）

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

        # タイムスタンプの処理（フィルタは datetime オブジェクトを期待）
        if timestamp is None:
            timestamp = datetime.now()

        # EntryContext を作成
        entry_context = {
            "timestamp": timestamp,  # datetime オブジェクトとして保持（フィルタで使用）
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
        dry_run: bool = False,
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
        dry_run : bool, optional
            True の場合、MT5発注を行わず擬似ポジションを保持する

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
        result = self.execute_entry(features, symbol=symbol, dry_run=dry_run)
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
                    self.profile_stats_service.set_current_profile(symbol, to_profile)

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

    def execute_entry(
        self,
        features: Dict[str, float],
        *,
        symbol: Optional[str] = None,
        dry_run: bool = False,
        timestamp: Optional[datetime] = None,
        suppress_metrics: bool = False,
    ) -> Dict[str, Any]:
        """
        売買判断 → フィルタ判定 → decisions.jsonl 出力まで一貫処理

        Parameters
        ----------
        features : dict
            特徴量の辞書
        symbol : str, optional
            シンボル名（指定されない場合は設定から取得）
        dry_run : bool, optional
            True の場合、MT5発注を行わず擬似ポジションを保持する

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

        # ProbOut オブジェクトから確率を取得（確率は pred から取得）
        # 固定値は設定しない（デバッグ時のみに限定）
        prob_buy = getattr(pred, "p_buy", None)
        prob_sell = getattr(pred, "p_sell", None)

        # 確率が取得できない場合はエラーログを出してSKIPで返す（固定値は設定しない）
        if prob_buy is None or prob_sell is None:
            log = logging.getLogger(__name__)
            log.error(
                "[ExecutionService] AI予測結果から確率を取得できませんでした: "
                "prob_buy=%s, prob_sell=%s, pred=%s",
                prob_buy, prob_sell, type(pred).__name__
            )
            # SKIPでログを出して終了（固定BUYにしない）
            ts_str = now_jst_iso()
            try:
                strategy_name = getattr(self.ai_service, "model_name", getattr(self.ai_service, "calibrator_name", "unknown"))
            except Exception:
                strategy_name = "unknown"

            # features_hash を生成（入力featuresが同一かを判定するため）
            features_hash_failed = _compute_features_hash(features) if features else ""

            decision_detail_failed = {
                "action": "SKIP",
                "side": None,
                "prob_buy": None,
                "prob_sell": None,
                "threshold": None,
                "ai_margin": None,
                "cooldown_sec": None,
                "blocked_reason": "ai_prediction_failed",
                "signal": None,
                "filter_pass": False,
                "filter_reasons": ["ai_prediction_failed"],
            }
            decision_detail_failed = _ensure_decision_detail_minimum(
                decision_detail_failed,
                decision="SKIP",
                signal=None,
                ai_margin=0.03,
            )

            # decision_context を構築
            decision_context = _build_decision_context(
                prob_buy=None,
                prob_sell=None,
                strategy_name=strategy_name,
                best_threshold=0.52,  # フォールバック値
                filters_dict={},
                decision_detail=decision_detail_failed,
                meta={},
            )

            record = {
                "timestamp": ts_str,
                "ts_jst": ts_str,
                "type": "decision",
                "symbol": symbol,
                "strategy": strategy_name,
                "prob_buy": None,  # 後方互換のため残す
                "prob_sell": None,  # 後方互換のため残す
                # 入力特徴量のハッシュ（同一入力判定用）
                "features_hash": features_hash_failed,
                "filter_pass": False,  # 後方互換のため残す
                "filter_reasons": ["ai_prediction_failed"],  # 後方互換のため残す
                "decision": "SKIP",  # 後方互換のため残す
                "side": None,  # 後方互換のため残す
                "filters": {},  # 後方互換のため残す
                "meta": {},  # 後方互換のため残す
                "decision_detail": decision_detail_failed,  # 後方互換のため残す
                "decision_context": decision_context,  # 新規追加：判断材料を分離
            }
            # ---- runtime normalization (decision log) ----
            # build_runtime() を使用して live/demo で統一（v2）
            # 既存 runtime があれば runtime_detail に退避
            _prev_rt = record.get("runtime")
            runtime = trade_state.build_runtime(
                symbol,
                market=market,
                ts_str=ts_str,  # JST ISO形式に正規化済み
                mode="live",  # ExecutionService は live モード
                source="mt5",  # ExecutionService は mt5 ソース
            )
            record["runtime"] = runtime
            if _prev_rt and isinstance(_prev_rt, dict):
                record["runtime_detail"] = _prev_rt
            # ---------------------------------------------
            DecisionsLogger.log(record)
            return {"ok": False, "reasons": ["ai_prediction_failed"]}

        # best_threshold を取得
        try:
            model_metrics = get_model_metrics()
            best_threshold = float(model_metrics.get("best_threshold", 0.52))
        except Exception:
            best_threshold = 0.52  # フォールバック

        # decide_signal を使用してシグナル判定
        # 注意: signal は意思決定結果（side/confidence/threshold判定）であり、
        # 確率（prob_buy/prob_sell）は pred から取得する
        signal = decide_signal(
            prob_buy=prob_buy,
            prob_sell=prob_sell,
            best_threshold=best_threshold,
        )

        # --- 2) EntryContext を構築（ProfileStats を含む） ---
        if timestamp is None:
            timestamp = datetime.now()
        entry_context = self._build_entry_context(symbol, features, timestamp=timestamp)

        # EditionGuard から filter_level を取得（1箇所で取得して使い回す）
        guard = EditionGuard()
        current_filter_level = guard.filter_level()

        # --- 3) フィルタ評価 ---
        # StrategyFilterEngine を使用してフィルタ評価
        # 注意: 現在の実装では Tuple[bool, List[str]] を返すが、
        # v5.1 仕様では bool のみを返す設計の可能性があるため、将来の変更に注意
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
            # filter_level（v5.1 仕様）- current_filter_level を使い回す
            "filter_level": current_filter_level,
            # filter 結果（v5.1 仕様）
            "filter_pass": ok,
            "filter_reasons": normalized_reasons,
        }

        # --- EntryContext を filters に統合 ---
        ctx = entry_context or {}
        # timestamp は datetime オブジェクトの可能性があるため、ISO形式に変換
        ts_val = ctx.get("timestamp")
        if isinstance(ts_val, datetime):
            filters_dict["timestamp"] = ts_val.isoformat()
        else:
            filters_dict["timestamp"] = ts_val
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

        # SignalDecision から decision_detail を生成（core層で確定）
        # ai_margin は固定値 0.03（ai_service.py の build_decision_from_probs と一致）
        ai_margin = 0.03
        cooldown_sec = None  # 現在は未実装
        decision_detail = signal.to_decision_detail(
            action=decision,
            ai_margin=ai_margin,
            cooldown_sec=cooldown_sec,
            blocked_reason=blocked_reason,
        )

        # 既存の signal / filter_pass / filter_reasons も保持（後方互換性）
        signal_detail = {
            "side": getattr(signal, "side", None),
            "confidence": getattr(signal, "confidence", None),
            "best_threshold": getattr(signal, "best_threshold", None),
            "pass_threshold": getattr(signal, "pass_threshold", None),
            "reason": getattr(signal, "reason", None),
        }
        decision_detail["signal"] = signal_detail
        decision_detail["filter_pass"] = ok
        decision_detail["filter_reasons"] = normalized_reasons
        # reason を blocked_reason に揃える（filter_reasons と一致させる）
        decision_detail["reason"] = blocked_reason or (normalized_reasons[0] if normalized_reasons else None)

        # --- 3) decisions.jsonl へ統合出力（v5.1 仕様に準拠） ---
        # logging 用のタイムスタンプ（ts_jst と timestamp は同じ値）
        ts_str = now_jst_iso()

        # features_hash を生成（入力featuresが同一かを判定するため）
        features_hash = _compute_features_hash(features) if features else ""

        # --- 4) フィルタでNGの場合ここで終了 ---
        if not ok:
            decision_detail = _ensure_decision_detail_minimum(
                decision_detail if isinstance(decision_detail, dict) else {},
                decision=decision,
                signal=signal,
                ai_margin=0.03,
            )
            # decision_context を構築
            decision_context = _build_decision_context(
                prob_buy=prob_buy,
                prob_sell=prob_sell,
                strategy_name=strategy_name,
                best_threshold=best_threshold,
                filters_dict=filters_dict,
                decision_detail=decision_detail,
                meta=meta_val or {},
            )

            record = {
                # 時刻・識別
                "timestamp": ts_str,
                "ts_jst": ts_str,
                "type": "decision",
                "symbol": symbol,
                # 戦略情報
                "strategy": strategy_name,
                # 確率は pred から取得（signal は意思決定結果のみ）
                "prob_buy": prob_buy,  # 後方互換のため残す
                "prob_sell": prob_sell,  # 後方互換のため残す
                # 入力特徴量のハッシュ（同一入力判定用）
                "features_hash": features_hash,
                # フィルタ結果（トップレベル要約）
                "filter_pass": ok,  # 後方互換のため残す
                "filter_reasons": normalized_reasons,  # 後方互換のため残す
                # 決定内容（トップレベル）
                "decision": decision,  # 後方互換のため残す
                "side": getattr(signal, "side", None),  # 後方互換のため残す
                # 生フィルタ情報 / メタ情報
                "filters": filters_dict,  # 後方互換のため残す
                "meta": meta_val or {},  # 後方互換のため残す
                # 詳細
                "decision_detail": decision_detail,  # 後方互換のため残す
                "decision_context": decision_context,  # 新規追加：判断材料を分離
            }
            # ---- runtime normalization (decision log) ----
            # build_runtime() を使用して live/demo で統一（v2）
            # 既存 runtime があれば runtime_detail に退避
            _prev_rt = record.get("runtime")
            runtime = trade_state.build_runtime(
                symbol,
                market=market,
                ts_str=ts_str,  # JST ISO形式に正規化済み
                mode="live",  # ExecutionService は live モード
                source="mt5",  # ExecutionService は mt5 ソース
            )
            record["runtime"] = runtime
            if _prev_rt and isinstance(_prev_rt, dict):
                record["runtime_detail"] = _prev_rt
            # ---------------------------------------------
            DecisionsLogger.log(record)

            # --- metrics に runtime 情報を追加 ---
            from app.services.metrics import publish_metrics
            # opt-in: 検証実行時などに runtime/metrics_*.json 更新を抑止する
            try:
                _suppress_metrics = bool(
                    suppress_metrics
                    or (isinstance(decision_detail, dict) and decision_detail.get("suppress_metrics") is True)
                    or (isinstance(meta_val, dict) and meta_val.get("suppress_metrics") is True)
                )
            except Exception:
                _suppress_metrics = bool(suppress_metrics)

            runtime_for_metrics = {
                "schema_version": runtime.get("schema_version", 2),
                "symbol": runtime.get("symbol", symbol),
                "mode": runtime.get("mode"),
                "source": runtime.get("source"),
                "timeframe": runtime.get("timeframe"),
                "profile": runtime.get("profile"),
                "ts": runtime.get("ts"),
                "spread_pips": runtime.get("spread_pips", 0.0),
                "open_positions": runtime.get("open_positions", 0),
                "max_positions": runtime.get("max_positions", 1),
            }
            if _suppress_metrics:
                logging.getLogger(__name__).info("[exec] suppress_metrics=True -> skip publish_metrics (filter_ng path)")
            else:
                publish_metrics(
                    {
                        "runtime": runtime_for_metrics,  # 新規追加：runtime 情報
                    },
                    no_metrics=False,
                )

            return {"ok": False, "reasons": reasons}

        # --- Step2-23: condition mining adoption guard (consume in execution_service) ---
        # NOTE:
        # - adoption は ops_snapshot の付帯情報で、ここでは「消費地点を execution_service に固定」する。
        # - 実際の発注ロジックが後で追加されても、ここ（dry_run 分岐の直前）は “発注直前” に相当する。
        # - 既存APIを壊さないため、現時点ではログ/メタへの付与のみ（挙動変更は最小）。
        cm_adoption = None
        try:
            from app.services.condition_mining_facade import get_condition_mining_ops_snapshot

            _cm = get_condition_mining_ops_snapshot(symbol=symbol)
            if isinstance(_cm, dict):
                cm_adoption = _cm.get("adoption")
        except Exception:
            cm_adoption = None

        if isinstance(cm_adoption, dict) and cm_adoption.get("status") == "adopted":
            try:
                cm_payload = {
                    "status": cm_adoption.get("status"),
                    "weight": cm_adoption.get("weight"),
                    "confidence_cap": cm_adoption.get("confidence_cap"),
                    "notes": cm_adoption.get("notes"),
                    "adopted": cm_adoption.get("adopted"),
                    "rejected": cm_adoption.get("rejected"),
                }
            except Exception:
                cm_payload = {"status": "adopted"}
            # decision_detail / meta に加法で載せる（既存フィールドを壊さない）
            try:
                if isinstance(decision_detail, dict) and "cm_adoption" not in decision_detail:
                    decision_detail["cm_adoption"] = cm_payload
            except Exception:
                pass
            try:
                if isinstance(meta_val, dict) and "cm_adoption" not in meta_val:
                    meta_val["cm_adoption"] = cm_payload
            except Exception:
                pass
        # --- /Step2-23 ---

        # --- T-44-4: ENTRY size_decision (services-only / add-only / label-only) ---
        # 方針:
        # - ENTRY可否は触らない（decision/ok を変更しない）
        # - size_decision は services 層で確定し、GUI/core で再計算しない
        # - この Step では “実取引サイズへの反映” はしない（監査ログ用のラベル付けのみ）
        cm_payload_for_size = None
        try:
            if isinstance(decision_detail, dict):
                cm_payload_for_size = decision_detail.get("cm_adoption")
        except Exception:
            cm_payload_for_size = None
        if cm_payload_for_size is None:
            try:
                if isinstance(meta_val, dict):
                    cm_payload_for_size = meta_val.get("cm_adoption")
            except Exception:
                cm_payload_for_size = None

        conf = None
        status = None
        try:
            if isinstance(cm_payload_for_size, dict):
                status = cm_payload_for_size.get("status")
                adopted = cm_payload_for_size.get("adopted")
                if isinstance(adopted, dict):
                    conf = adopted.get("condition_confidence")
        except Exception:
            conf = None
            status = None

        # stability / upside_potential は “既存の services 出力” から読む（推測で作らない）
        # - stability: ops_overview_facade.get_ops_overview() が返す wfo_stability（stable/score/reasons）
        # - upside_potential: ops_history_service.summarize_ops_history() が返す profit_metrics
        wfo_stability = None
        upside_potential = None
        try:
            from app.services.ops_overview_facade import get_ops_overview

            ov = get_ops_overview(include_condition_mining=False)
            if isinstance(ov, dict):
                wfo_stability = ov.get("wfo_stability")
        except Exception:
            wfo_stability = None
        try:
            from app.services.ops_history_service import summarize_ops_history

            _sum = summarize_ops_history(cache_sec=2, include_condition_mining=False)
            if isinstance(_sum, dict):
                pm = _sum.get("profit_metrics")
                if isinstance(pm, dict):
                    upside_potential = pm.get("upside_potential")
        except Exception:
            upside_potential = None

        size_decision = _compute_size_decision_v1(
            stability=(wfo_stability if isinstance(wfo_stability, dict) else None),
            condition_confidence=(str(conf) if conf is not None else None),
            upside_potential=(str(upside_potential) if upside_potential is not None else None),
        )
        mult = float(size_decision.get("multiplier", 1.0) if isinstance(size_decision, dict) else 1.0)
        reason = str(size_decision.get("reason", "baseline_conditions") if isinstance(size_decision, dict) else "baseline_conditions")

        # 監査ログ（破壊検知用）：decision_detail に必ず残す（追加のみ・既存は上書きしない）
        try:
            if not isinstance(decision_detail, dict):
                decision_detail = {}
            decision_detail.setdefault("size_decision", {"multiplier": float(mult), "reason": str(reason)})
        except Exception:
            pass

        # ops_history（ops_service 経由の履歴）で観測できるよう、meta にも add-only で付与
        # - 返り値 dict の meta に含める（ops_service が hist_rec["meta"] に保存するため）
        try:
            if isinstance(meta_val, dict):
                meta_val.setdefault("size_decision", {"multiplier": float(mult), "reason": str(reason)})
        except Exception:
            pass
        # --- /T-44-4 ---

        # --- T-43-4 Step1 (cont): build order_params for audit (services-only) ---
        # 目的:
        # - “発注直前相当”で order_params を必ず生成（将来の実発注ロジックに繋ぐ）
        # - dry_run では decision_detail に保存し、戻り値にも載せて監査できるようにする
        # - entry可否（ok/decision）は変更しない
        order_params = {
            "symbol": symbol,
            "side": getattr(signal, "side", None),
            # size_decision と整合する倍率（必ず一致させる）
            "size_multiplier": float(mult),
            "size_reason": reason,
        }
        # schema固定（追加のみ・既存キー上書き禁止）
        try:
            from app.services.order_params_schema import ensure_order_params_schema

            order_params = ensure_order_params_schema(
                order_params,
                pair=symbol,  # 補助キー（renameではなく追加）
                symbol=symbol,
                mode=("dry_run" if dry_run else "live"),
            )
        except Exception:
            pass
        # lot/qty が存在する場合のみ入れる（現時点では未実装でも将来に備える）
        try:
            if "lot" in locals() and isinstance(lot, (int, float)):
                order_params["lot"] = float(lot)
            if "volume" in locals() and isinstance(volume, (int, float)):
                order_params["volume"] = float(volume)
            if "qty" in locals() and isinstance(qty, (int, float)):
                order_params["qty"] = float(qty)
            if "quantity" in locals() and isinstance(quantity, (int, float)):
                order_params["quantity"] = float(quantity)
        except Exception:
            # order_params は監査情報。ここで落ちないように縮退
            pass

        # decision_detail に保存（追加のみ）
        try:
            if not isinstance(decision_detail, dict):
                decision_detail = {}
            if "order_params" not in decision_detail:
                decision_detail["order_params"] = order_params
        except Exception:
            pass
        # --- /T-43-4 Step1 (cont) ---

        # --- 5) dry_run モードの場合、MT5発注の直前で分岐 ---
        settings = trade_state.get_settings()
        trading_enabled = bool(getattr(settings, "trading_enabled", False))
        dry_run = bool(dry_run) or (not trading_enabled)
        logging.getLogger(__name__).info("[exec] trading_enabled=%s effective_dry_run=%s", trading_enabled, dry_run)
        if dry_run:
            # decision_detail を更新
            decision_detail["action"] = "ENTRY_SIMULATED"
            decision = "ENTRY_SIMULATED"

            # 擬似ポジションを保持
            self._sim_pos = {
                "symbol": symbol,
                "side": signal.side,
                # 確率は pred から取得
                "prob_buy": prob_buy,
                "prob_sell": prob_sell,
                "timestamp": ts_str,
                "features": features,
            }

            # ログ出力
            logger = logging.getLogger(__name__)
            logger.info(
                "[dry_run] Simulated ENTRY: symbol=%s, side=%s, prob_buy=%.4f, prob_sell=%.4f",
                symbol,
                signal.side,
                prob_buy or 0.0,
                prob_sell or 0.0,
            )

            # decisions.jsonl に出力（ENTRY_SIMULATED として）
            decision_detail = _ensure_decision_detail_minimum(
                decision_detail if isinstance(decision_detail, dict) else {},
                decision=decision,
                signal=signal,
                ai_margin=0.03,
            )
            # decision_context を構築
            decision_context = _build_decision_context(
                prob_buy=prob_buy,
                prob_sell=prob_sell,
                strategy_name=strategy_name,
                best_threshold=best_threshold,
                filters_dict=filters_dict,
                decision_detail=decision_detail,
                meta=meta_val or {},
            )

            DecisionsLogger.log({
                "timestamp": ts_str,
                "ts_jst": ts_str,
                "type": "decision",
                "symbol": symbol,
                "strategy": strategy_name,  # 後方互換のため残す
                # 確率は pred から取得
                "prob_buy": prob_buy,  # 後方互換のため残す
                "prob_sell": prob_sell,  # 後方互換のため残す
                # 入力特徴量のハッシュ（同一入力判定用）
                "features_hash": features_hash,
                "filter_pass": ok,  # 後方互換のため残す
                "filter_reasons": normalized_reasons,  # 後方互換のため残す
                "decision": decision,  # 後方互換のため残す
                "side": getattr(signal, "side", None),  # 後方互換のため残す
                "filters": filters_dict,  # 後方互換のため残す
                "meta": meta_val or {},  # 後方互換のため残す
                "decision_detail": decision_detail,  # 後方互換のため残す
                "decision_context": decision_context,  # 新規追加：判断材料を分離
            })

            # --- T-45-4: ENTRY decision -> TradeService execution bridge (add-only) ---
            # dry_run/live で同じ決定を渡し、実発注の有無は TradeService 側で分岐する。
            try:
                if str(decision).upper().startswith("ENTRY"):
                    from app.services import trade_service

                    features_payload: dict = dict(features) if isinstance(features, dict) else {}
                    if isinstance(decision_detail, dict) and isinstance(decision_detail.get("size_decision"), dict):
                        features_payload.setdefault("size_decision", decision_detail.get("size_decision"))
                    payload = {
                        "action": "ENTRY",
                        "side": getattr(signal, "side", None),
                        "decision_detail": decision_detail,
                        "meta": meta_val or {},
                        "signal": {
                            "side": getattr(signal, "side", None),
                            "atr_for_lot": (features.get("atr_for_lot") if isinstance(features, dict) else None),
                            "features": features_payload,
                        },
                    }
                    trade_service.execute_decision(payload, symbol=symbol, service=None, dry_run=True)
            except Exception as e:
                logging.getLogger(__name__).info(
                    "[exec][entry_bridge] skip execute_decision (dry_run) due to %s: %s",
                    type(e).__name__,
                    e,
                )
            # --- /T-45-4 ---

            return {
                "ok": True,
                "reasons": [],
                # 確率は pred から取得
                "prob_buy": prob_buy,
                "prob_sell": prob_sell,
                "signal": signal,
                "dry_run": True,
                "simulated": True,
                # ops_history 観測用（add-only）: size_decision は services 層で確定済み
                "meta": meta_val or {},
                # 監査用: 発注パラメータ（size_decision と整合する）
                "order_params": order_params,
            }

        # 通常モードの場合、decision_detail は "ENTRY" のまま
        decision_detail = _ensure_decision_detail_minimum(
            decision_detail if isinstance(decision_detail, dict) else {},
            decision=decision,
            signal=signal,
            ai_margin=0.03,
        )
        record = {
            # 時刻・識別
            "timestamp": ts_str,
            "ts_jst": ts_str,
            "type": "decision",
            "symbol": symbol,
            # 戦略情報
            "strategy": strategy_name,
            # 確率は pred から取得（signal は意思決定結果のみ）
            "prob_buy": prob_buy,
            "prob_sell": prob_sell,
            # 入力特徴量のハッシュ（同一入力判定用）
            "features_hash": features_hash,
            # フィルタ結果（トップレベル要約）
            "filter_pass": ok,
            "filter_reasons": normalized_reasons,
            # 決定内容（トップレベル）
            "decision": decision,
            "side": getattr(signal, "side", None),
            # 生フィルタ情報 / メタ情報
            "filters": filters_dict,
            "meta": meta_val or {},
            # 詳細
            "decision_detail": decision_detail,
        }
        # ---- runtime normalization (decision log) ----
        # build_runtime() を使用して live/demo で統一（v2）
        # 既存 runtime があれば runtime_detail に退避
        _prev_rt = record.get("runtime")
        runtime = trade_state.build_runtime(
            symbol,
            market=market,
            ts_str=ts_str,  # JST ISO形式に正規化済み
            mode="live",  # ExecutionService は live モード
            source="mt5",  # ExecutionService は mt5 ソース
        )
        record["runtime"] = runtime
        if _prev_rt and isinstance(_prev_rt, dict):
            record["runtime_detail"] = _prev_rt
        # ---------------------------------------------
        DecisionsLogger.log(record)

        # --- T-45-4: ENTRY decision -> TradeService execution bridge (add-only) ---
        # 実行ゲートは TradeService.open_position() 冒頭に集約（ここでは gate しない）。
        try:
            if str(decision).upper() == "ENTRY":
                from app.services import trade_service

                features_payload2: dict = dict(features) if isinstance(features, dict) else {}
                if isinstance(decision_detail, dict) and isinstance(decision_detail.get("size_decision"), dict):
                    features_payload2.setdefault("size_decision", decision_detail.get("size_decision"))
                payload2 = {
                    "action": "ENTRY",
                    "side": getattr(signal, "side", None),
                    "decision_detail": decision_detail,
                    "meta": meta_val or {},
                    "signal": {
                        "side": getattr(signal, "side", None),
                        "atr_for_lot": (features.get("atr_for_lot") if isinstance(features, dict) else None),
                        "features": features_payload2,
                    },
                }
                trade_service.execute_decision(payload2, symbol=symbol, service=None, dry_run=bool(dry_run))
        except Exception as e:
            logging.getLogger(__name__).info(
                "[exec][entry_bridge] skip execute_decision due to %s: %s",
                type(e).__name__,
                e,
            )
        # --- /T-45-4 ---

        # --- metrics に runtime 情報を追加 ---
        from app.services.metrics import publish_metrics
        # opt-in: 検証実行時などに runtime/metrics_*.json 更新を抑止する
        try:
            _suppress_metrics = bool(
                suppress_metrics
                or (isinstance(decision_detail, dict) and decision_detail.get("suppress_metrics") is True)
                or (isinstance(meta_val, dict) and meta_val.get("suppress_metrics") is True)
            )
        except Exception:
            _suppress_metrics = bool(suppress_metrics)

        runtime_for_metrics = {
            "schema_version": runtime.get("schema_version", 2),
            "symbol": runtime.get("symbol", symbol),
            "mode": runtime.get("mode"),
            "source": runtime.get("source"),
            "timeframe": runtime.get("timeframe"),
            "profile": runtime.get("profile"),
            "ts": runtime.get("ts"),
            "spread_pips": runtime.get("spread_pips", 0.0),
            "open_positions": runtime.get("open_positions", 0),
            "max_positions": runtime.get("max_positions", 1),
        }
        if _suppress_metrics:
            logging.getLogger(__name__).info("[exec] suppress_metrics=True -> skip publish_metrics (normal path)")
        else:
            publish_metrics(
                {
                    "runtime": runtime_for_metrics,  # 新規追加：runtime 情報
                },
                no_metrics=False,
            )

        # --- 6) 実際のMT5発注（dry_run=False の場合のみ） ---
        # 発注ロジックはそのまま（TradeService などを呼び出す想定）

        return {
            "ok": True,
            "reasons": [],
            # 確率は pred から取得
            "prob_buy": prob_buy,
            "prob_sell": prob_sell,
            "signal": signal,
            # ops_history 観測用（add-only）: size_decision は services 層で確定済み
            "meta": meta_val or {},
            # 監査用（追加のみ）: 発注パラメータ
            "order_params": order_params,
        }

    def execute_exit(self, symbol: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
        """
        決済監視/クローズ処理

        Parameters
        ----------
        symbol : str, optional
            シンボル名（指定されない場合は設定から取得）
        dry_run : bool, optional
            True の場合、MT5決済を行わず擬似ポジションをクリアする

        Returns
        -------
        dict
            決済結果
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

        logger = logging.getLogger(__name__)

        # dry_run モードの場合、擬似ポジションをクリア
        if dry_run and self._sim_pos:
            sim_pos = self._sim_pos
            self._sim_pos = None

            ts_str = now_jst_iso()

            logger.info(
                "[dry_run] Simulated EXIT: symbol=%s, side=%s",
                sim_pos.get("symbol"),
                sim_pos.get("side"),
            )

            # decisions.jsonl に出力（EXIT_SIMULATED として）
            decision_detail = {
                "action": "EXIT_SIMULATED",
                "side": sim_pos.get("side"),
                "prob_buy": sim_pos.get("prob_buy"),
                "prob_sell": sim_pos.get("prob_sell"),
                "threshold": None,
                "ai_margin": None,
                "cooldown_sec": None,
                "blocked_reason": None,
                "signal": {
                    "side": sim_pos.get("side"),
                    "confidence": sim_pos.get("prob_buy") if sim_pos.get("side") == "BUY" else sim_pos.get("prob_sell"),
                },
                "filter_pass": True,
                "filter_reasons": [],
            }
            decision_detail = _ensure_decision_detail_minimum(
                decision_detail,
                decision="EXIT_SIMULATED",
                signal=None,
                ai_margin=0.03,
            )
            # T-44-3: Exit as Decision (label-only / add-only)
            # Simulated exit is treated as DEFENSE (forced close in dry_run).
            if isinstance(decision_detail, dict):
                decision_detail.setdefault("exit_type", "DEFENSE")
                decision_detail.setdefault("exit_reason", "exit_simulated")

            # decision_context を構築
            decision_context = _build_decision_context(
                prob_buy=sim_pos.get("prob_buy"),
                prob_sell=sim_pos.get("prob_sell"),
                strategy_name="unknown",
                best_threshold=0.52,  # フォールバック値
                filters_dict={},
                decision_detail=decision_detail,
                meta={},
            )

            record = {
                "timestamp": ts_str,
                "ts_jst": ts_str,
                "type": "decision",
                "symbol": symbol,
                "strategy": "unknown",  # 後方互換のため残す
                "prob_buy": sim_pos.get("prob_buy"),  # 後方互換のため残す
                "prob_sell": sim_pos.get("prob_sell"),  # 後方互換のため残す
                # 入力特徴量のハッシュ（EXITの場合は空）
                "features_hash": "",
                "filter_pass": True,  # 後方互換のため残す
                "filter_reasons": [],  # 後方互換のため残す
                "decision": "EXIT_SIMULATED",  # 後方互換のため残す
                "side": sim_pos.get("side"),  # 後方互換のため残す
                "filters": {},  # 後方互換のため残す
                "meta": {},  # 後方互換のため残す
                "decision_detail": decision_detail,  # 後方互換のため残す
                "decision_context": decision_context,  # 新規追加：判断材料を分離
            }
            # ---- runtime normalization (decision log) ----
            # build_runtime() を使用して live/demo で統一（v2）
            # 既存 runtime があれば runtime_detail に退避
            _prev_rt = record.get("runtime")
            runtime = trade_state.build_runtime(
                symbol,
                market=market,
                ts_str=ts_str,  # JST ISO形式に正規化済み
                mode="live",  # ExecutionService は live モード
                source="mt5",  # ExecutionService は mt5 ソース
            )
            record["runtime"] = runtime
            if _prev_rt and isinstance(_prev_rt, dict):
                record["runtime_detail"] = _prev_rt
            # ---------------------------------------------
            DecisionsLogger.log(record)

            return {
                "ok": True,
                "dry_run": True,
                "simulated": True,
                "exited": True,
            }

        # 通常モードの場合、実際のMT5決済処理
        # （TradeService などを呼び出す想定）

        return {
            "ok": True,
            "exited": True,
        }

