# app/core/backtest/backtest_engine.py
from __future__ import annotations

import json


def _enrich_active_model_meta(meta: dict, model_obj=None) -> dict:
    """
    Ensure active_model.json has:
      - expected_features: list[str] (must be non-empty)
      - feature_hash: sha256("\n".join(expected_features))
    Best-effort from model_obj; fallback to meta["feature_order"]/meta["features"].
    """
    import hashlib

    exp = meta.get("expected_features") or []
    # best-effort from model
    if (not exp) and model_obj is not None:
        try:
            if hasattr(model_obj, "feature_name_"):
                exp = list(getattr(model_obj, "feature_name_"))
            elif hasattr(model_obj, "feature_names_in_"):
                exp = list(getattr(model_obj, "feature_names_in_"))
            elif hasattr(model_obj, "booster_") and hasattr(model_obj.booster_, "feature_name"):
                exp = list(model_obj.booster_.feature_name())
            elif hasattr(model_obj, "booster") and callable(getattr(model_obj, "booster", None)):
                b = model_obj.booster()
                if hasattr(b, "feature_name"):
                    exp = list(b.feature_name())
        except Exception:
            pass

    # fallback to meta itself
    if not exp:
        exp = meta.get("feature_order") or meta.get("features") or []

    # normalize
    if not isinstance(exp, list) or not exp or not all(isinstance(x, str) and x for x in exp):
        raise RuntimeError("[active_model] expected_features is empty -> cannot promote/swap model safely")

    meta["expected_features"] = list(exp)
    meta["feature_hash"] = hashlib.sha256("\n".join(meta["expected_features"]).encode("utf-8")).hexdigest()
    return meta

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import csv

import numpy as np
import pandas as pd

from app.core.backtest.simulated_execution import SimulatedExecution
from app.core.trade.decision_logic import decide_signal
from app.core.filter.strategy_filter_engine import StrategyFilterEngine
from app.services.filter_service import evaluate_entry
from app.services.profile_stats_service import get_profile_stats_service
from app.strategies.ai_strategy import (
    build_features,
    get_active_model_meta,
    validate_feature_order_fail_fast,
    load_active_model,
    _predict_proba_generic,
    _load_model_generic,
    _load_scaler_if_any,
    _ensure_feature_order,
)

# region agent log
# Debug mode NDJSON logger (no secrets)
import time as _time

_DEBUG_LOG_PATH = r"d:\fxbot\.cursor\debug.log"
_DEBUG_SESSION_ID = "debug-session"
_DEBUG_RUN_ID = "obs1"


def _dbg(hypothesisId: str, location: str, message: str, data: dict) -> None:
    try:
        payload = {
            "sessionId": _DEBUG_SESSION_ID,
            "runId": _DEBUG_RUN_ID,
            "hypothesisId": str(hypothesisId),
            "location": str(location),
            "message": str(message),
            "data": data or {},
            "timestamp": int(_time.time() * 1000),
        }
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # never break runtime due to debug logging
        pass

# endregion agent log


class ProbOut:
    """
    AISvc.ProbOut の代替（ai_service 依存を避けるため）
    """
    def __init__(self, p_buy: float, p_sell: float, p_skip: float = 0.0) -> None:
        self.p_buy = float(p_buy)
        self.p_sell = float(p_sell)
        self.p_skip = float(p_skip)


class BacktestEngine:
    """
    v5.1 準拠のバックテストエンジン

    フロー: Strategy → FilterEngine → SimulatedExecution
    """

    def __init__(
        self,
        profile: str = "michibiki_std",
        initial_capital: float = 100000.0,
        contract_size: int = 100000,
        filter_level: int = 3,
        init_position: str = "flat",
        trade_start_ts: Optional[pd.Timestamp] = None,
        exit_policy: Optional[Dict[str, Any]] = None,
    ):
        """
        Parameters
        ----------
        profile : str
            プロファイル名
        initial_capital : float
            初期資本
        contract_size : int
            契約サイズ（JPYペアの場合は100000）
        filter_level : int
            フィルタレベル（0=無効, 1=Basic, 2=Pro, 3=Expert）
        exit_policy : dict, optional
            Exit設計パラメータ:
            - min_holding_bars: int = 0 (最低保有バー数)
            - tp_sl_eval_from_next_bar: bool = False (TP/SLを次バー以降で評価)
            - exit_on_reverse_signal_only: bool = False (逆シグナル時のみexit)
        """
        self.profile = profile
        self.initial_capital = initial_capital
        self.contract_size = contract_size
        self.filter_level = filter_level
        self.init_position = (init_position or "flat").lower()
        self.trade_start_ts = trade_start_ts

        # ExitPolicy（min_holding_bars 解決の優先順位: caller > active_model > default）
        if exit_policy is None:
            exit_policy = {}

        # min_holding_bars の解決（優先順位: caller > active_model > default）
        _min_hold_source = "default"
        _min_hold_value = 0  # デフォルト

        # 1. active_model.json からの取得（SSOT）
        try:
            _am_meta = get_active_model_meta() or {}
            _am_exit_policy = _am_meta.get("exit_policy") or {}
            if "min_holding_bars" in _am_exit_policy:
                _min_hold_value = int(_am_exit_policy["min_holding_bars"])
                _min_hold_source = "active_model"
        except Exception:
            pass  # フォールバック: default を維持

        # 2. caller 明示指定があれば上書き（最優先）
        if "min_holding_bars" in exit_policy:
            _min_hold_value = int(exit_policy["min_holding_bars"])
            _min_hold_source = "cli_override"

        self.exit_policy = {
            "min_holding_bars": _min_hold_value,
            "tp_sl_eval_from_next_bar": exit_policy.get("tp_sl_eval_from_next_bar", False),
            "exit_on_reverse_signal_only": exit_policy.get("exit_on_reverse_signal_only", False),
        }

        self.executor = SimulatedExecution(initial_capital, contract_size)
        self.profile_stats_service = get_profile_stats_service()
        self.filter_engine = StrategyFilterEngine()

        # 連敗カウンタ（バックテスト中に動的に更新）
        self.consecutive_losses = 0

        # decisions.jsonl の記録用
        self.decisions: List[Dict[str, Any]] = []

        # ExitPolicy適用ログ（常に出力、source 付き）
        print(
            f"[exit_policy] min_holding_bars={self.exit_policy['min_holding_bars']} "
            f"source={_min_hold_source}",
            flush=True,
        )

        # モデル情報を取得（active_model.jsonから）
        try:
            meta = get_active_model_meta()
            self.best_threshold = float(meta.get("best_threshold", 0.52))
            # モデルをロード（後で使用）
            self.model_kind, self.model_payload, _, self.model_params = load_active_model()
            self.model = None  # 遅延ロード
            self.scaler = None  # 遅延ロード
            # region agent log
            self._dbg_once = False
            self._dbg_pred_n = 0
            # endregion agent log
            _dbg(
                "A",
                "app/core/backtest/backtest_engine.py:__init__",
                "init loaded model config",
                {
                    "best_threshold": self.best_threshold,
                    "model_kind": self.model_kind,
                    "model_payload_type": type(self.model_payload).__name__,
                    "model_params_keys_n": len(getattr(self, "model_params", {}) or {}),
                    "meta_keys_n": len(meta.keys()) if isinstance(meta, dict) else None,
                    "meta_has_feature_order": bool(isinstance(meta, dict) and (meta.get("feature_order") or meta.get("features"))),
                },
            )
        except Exception as e:
            self.best_threshold = 0.52  # フォールバック
            self.model_kind = None
            self.model_payload = None
            self.model_params = {}
            self.model = None
            self.scaler = None
            # region agent log
            self._dbg_once = False
            self._dbg_pred_n = 0
            # endregion agent log
            _dbg(
                "A",
                "app/core/backtest/backtest_engine.py:__init__",
                "init failed to load model config",
                {
                    "exc_type": type(e).__name__,
                    "exc_msg": str(e)[:300],
                },
            )

    def _ensure_model_loaded(self) -> None:
        """
        モデルとスケーラーを遅延ロードする
        """
        if self.model is None and self.model_kind is not None:
            try:
                # region agent log
                if getattr(self, "_dbg_pred_n", 0) == 0:
                    _dbg(
                        "A",
                        "app/core/backtest/backtest_engine.py:_ensure_model_loaded",
                        "enter",
                        {
                            "model_kind": self.model_kind,
                            "model_payload_type": type(self.model_payload).__name__,
                            "model_is_none": self.model is None,
                            "scaler_is_none": self.scaler is None,
                        },
                    )
                # endregion agent log
                if self.model_kind == "builtin":
                    # builtinモデルは予測時に処理
                    pass
                else:
                    # 外部モデルをロード
                    self.model = _load_model_generic(self.model_payload)
                    # スケーラーをロード
                    self.scaler = _load_scaler_if_any(self.model_params)
                # region agent log
                if getattr(self, "_dbg_pred_n", 0) == 0:
                    _dbg(
                        "A",
                        "app/core/backtest/backtest_engine.py:_ensure_model_loaded",
                        "exit",
                        {
                            "model_loaded": self.model is not None,
                            "scaler_loaded": self.scaler is not None,
                        },
                    )
                # endregion agent log
            except Exception as e:
                print(f"[BacktestEngine] Failed to load model: {e}", flush=True)
                self.model = None
                self.scaler = None
                _dbg(
                    "A",
                    "app/core/backtest/backtest_engine.py:_ensure_model_loaded",
                    "exception",
                    {
                        "exc_type": type(e).__name__,
                        "exc_msg": str(e)[:300],
                    },
                )

    def _predict(self, features_dict: Dict[str, float]) -> ProbOut:
        """
        特徴量辞書から予測確率を取得する（ai_service 依存を避けるため）
        """
        try:
            # region agent log
            if getattr(self, "_dbg_pred_n", 0) < 3:
                _dbg(
                    "A",
                    "app/core/backtest/backtest_engine.py:_predict",
                    "enter",
                    {
                        "model_kind": self.model_kind,
                        "model_is_none": self.model is None,
                        "features_n": len(features_dict or {}),
                        "feature_keys_head": list((features_dict or {}).keys())[:8],
                    },
                )
            # endregion agent log
            self._ensure_model_loaded()
            
            if self.model_kind == "builtin":
                # builtinモデルは未対応（必要に応じて実装）
                return ProbOut(0.0, 0.0, 1.0)
            
            if self.model is None:
                # region agent log
                if getattr(self, "_dbg_pred_n", 0) < 3:
                    _dbg(
                        "A",
                        "app/core/backtest/backtest_engine.py:_predict",
                        "model unavailable -> returning zeros",
                        {},
                    )
                # endregion agent log
                return ProbOut(0.0, 0.0, 1.0)
            
            # 特徴量をDataFrameに変換（1行）
            feat_df = pd.DataFrame([features_dict])
            
            # 特徴量の順序を確保
            try:
                X = _ensure_feature_order(feat_df, self.model_params)
            except Exception as e:
                _dbg(
                    "B",
                    "app/core/backtest/backtest_engine.py:_predict",
                    "ensure_feature_order failed",
                    {
                        "exc_type": type(e).__name__,
                        "exc_msg": str(e)[:300],
                        "feat_cols": list(feat_df.columns)[:50],
                    },
                )
                raise
            
            # スケーラーを適用
            if self.scaler is not None:
                Xv = X.values
                try:
                    # 標準のsklearn系（StandardScaler など）
                    Xv = self.scaler.transform(Xv)
                except AttributeError:
                    # dict / (mean, scale) / ndarray を許容
                    if isinstance(self.scaler, dict) and ("mean" in self.scaler or "scale" in self.scaler):
                        mean = np.asarray(self.scaler.get("mean", np.zeros(Xv.shape[1])))
                        scale = np.asarray(self.scaler.get("scale", np.ones(Xv.shape[1])))
                        Xv = (Xv - mean) / (scale + 1e-12)
                    elif isinstance(self.scaler, (tuple, list)) and len(self.scaler) >= 2:
                        mean = np.asarray(self.scaler[0])
                        scale = np.asarray(self.scaler[1])
                        Xv = (Xv - mean) / (scale + 1e-12)
                    elif isinstance(self.scaler, np.ndarray):
                        mean = self.scaler
                        Xv = (Xv - mean)
                # DataFrameに戻す
                X = pd.DataFrame(Xv, index=X.index, columns=X.columns)
            
            # 予測確率を取得
            proba = _predict_proba_generic(self.model, X)
            
            # 2次元(=確率2列)なら陽性側だけを採用
            if proba.ndim == 2 and proba.shape[1] == 2:
                p_buy = float(proba[0, 1])
            else:
                p_buy = float(proba[0])
            
            # 0〜1 にクリップ
            p_buy = max(0.0, min(1.0, p_buy))
            p_sell = 1.0 - p_buy
            p_skip = 0.0
            # region agent log
            # 予測確率算出直後の観測（最初の3回のみ記録）
            if getattr(self, "_dbg_pred_n", 0) < 3:
                _dbg(
                    "C",
                    "app/core/backtest/backtest_engine.py:_predict",
                    "予測確率算出直後",
                    {
                        "p_buy": p_buy,
                        "p_sell": p_sell,
                        "p_skip": p_skip,
                        "proba_shape": getattr(proba, "shape", None),
                        "proba_is_nan": bool(np.isnan(np.asarray(proba)).any()) if proba is not None else None,
                        "p_buy_is_zero": p_buy == 0.0,
                        "p_sell_is_zero": p_sell == 0.0,
                        "p_buy_is_one": p_buy == 1.0,
                        "p_sell_is_one": p_sell == 1.0,
                    },
                )
                self._dbg_pred_n = int(getattr(self, "_dbg_pred_n", 0)) + 1
            # endregion agent log
            
            return ProbOut(p_buy, p_sell, p_skip)
        except Exception as e:
            print(f"[BacktestEngine] Prediction failed: {e}", flush=True)
            _dbg(
                "A",
                "app/core/backtest/backtest_engine.py:_predict",
                "exception -> returning zeros",
                {
                    "exc_type": type(e).__name__,
                    "exc_msg": str(e)[:300],
                },
            )
            return ProbOut(0.0, 0.0, 1.0)

    def _normalize_filter_ctx(self, filters_ctx: dict | None) -> dict:
        """
        Backtest 用 filters_ctx を v5.1 仕様に揃える:
        - None を {} に置き換え
        - filter_reasons を必ず list に正規化
        """
        if filters_ctx is None:
            filters_ctx = {}
        else:
            filters_ctx = dict(filters_ctx)

        reasons = filters_ctx.get("filter_reasons")

        if reasons is None:
            reasons_list: list[str] = []
        elif isinstance(reasons, str):
            reasons_list = [reasons]
        else:
            # list, tuple, set などを list にする
            reasons_list = list(reasons)

        filters_ctx["filter_reasons"] = reasons_list
        return filters_ctx

    def run(
        self,
        df: pd.DataFrame,
        out_dir: Path,
        symbol: str = "USDJPY-",
    ) -> Dict[str, Any]:
        """
        バックテストを実行する

        Parameters
        ----------
        df : pd.DataFrame
            OHLCVデータ（time, open, high, low, close, volume を含む）
        out_dir : Path
            出力ディレクトリ
        symbol : str
            シンボル名

        Returns
        -------
        dict
            バックテスト結果（equity_curve, trades, decisions のパスなど）
        """
        # --- Step2-18: background band timeline (HOLD/BLOCKED) ---
        timeline_rows = []  # list[dict]: {time, kind, reason}
        _tl_last_kind = None
        _tl_last_reason = None

        # データの準備
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)
        
        # ExitPolicy用：エントリー時のバーインデックスを記録（SimulatedTradeに追加）
        # entry_bar_index を保持するための辞書（trade_id -> bar_index）
        self._entry_bar_indices: Dict[int, int] = {}
        self._next_trade_id = 0

        # 特徴量を構築
        print(f"[BacktestEngine] Building features...", flush=True)
        df_features = build_features(df, params={})

        # 必須列の補完
        if "time" not in df_features.columns:
            df_features["time"] = df["time"]
        if "close" not in df_features.columns:
            df_features["close"] = df["close"].astype(float)

        # Fail-fast: feature_order must match active_model feature_order
        # active_model.json の feature_order のみを使用（推測・補完なし）
        meta = get_active_model_meta() or {}
        feature_order = meta.get("feature_order") or meta.get("features")
        if not feature_order:
            raise RuntimeError("[BacktestEngine] feature_order missing in active_model.json")
        if not isinstance(feature_order, list):
            raise RuntimeError(f"[BacktestEngine] feature_order must be a list, got {type(feature_order)}")
        
        # 検証（validate_feature_order_fail_fast 内で time/close は除外される）
        feature_order = validate_feature_order_fail_fast(
            df_cols=list(df_features.columns),
            expected=list(feature_order),
            context="backtest",
        )
        
        # 整形（time, close を保持しつつ、feature_order の順序で並べる）
        keep_cols = []
        if "time" in df_features.columns:
            keep_cols.append("time")
        if "close" in df_features.columns:
            keep_cols.append("close")
        keep_cols.extend(feature_order)
        df_features = df_features[keep_cols]

        # 各バーを処理
        print(f"[BacktestEngine] Processing {len(df_features)} bars...", flush=True)

        # デバッグカウンタを初期化
        debug_counters = {
            "n_signal_buy": 0,
            "n_signal_sell": 0,
            "n_filter_pass": 0,
            "n_filter_fail": 0,
            "n_entries": 0,
            "n_exits": 0,
            "n_entry_attempts": 0,  # エントリー試行回数（任意）
            "filter_fail_reason": None,  # 最初の1件の失敗理由
            "filter_fail_reason_count": 0,  # 同じ理由の出現回数（optional）
            "entry_block_reason": None,  # 最初の1件のエントリーブロック理由
        }

        # equity_curve.csv のストリーミング追記用（5バーごと）
        equity_csv_path = out_dir / "equity_curve.csv"
        equity_csv_handle = None
        equity_csv_header_written = False
        equity_batch = []  # 5バー分のデータを蓄積

        from tools.backtest_run import iter_with_progress
        for idx, row in iter_with_progress(df_features, step=5, use_iterrows=True):
            timestamp = pd.Timestamp(row["time"])
            price = float(row["close"])

            # flat: start以前は取引を抑止（特徴量更新のみ許可）
            try:
                if (
                    self.init_position == "flat"
                    and self.trade_start_ts is not None
                    and timestamp < self.trade_start_ts
                ):
                    continue
            except Exception:
                pass

            # 特徴量を辞書形式に変換
            features_dict = {col: float(row[col]) for col in df_features.columns if col not in ["time", "close"]}

            # 予測を実行（ai_service 依存を避けるため）
            ai_out = self._predict(features_dict)

            # EntryContext を作成
            entry_context = self._build_entry_context(row, timestamp)

            # FilterEngine.evaluate を呼ぶ
            # filter_level を entry_context に追加
            entry_context["filter_level"] = self.filter_level
            filter_pass, filter_reasons = self.filter_engine.evaluate(entry_context, filter_level=self.filter_level)

            # --- Step2-18: add timeline point when kind changes ---
            try:
                _pass = bool(filter_pass) if 'filter_pass' in locals() else True
            except Exception:
                _pass = True
            kind = 'HOLD' if _pass else 'BLOCKED'
            try:
                _reason = ''
                if 'filter_reasons' in locals():
                    rv = filter_reasons
                    _reason = ';'.join(rv) if isinstance(rv, (list, tuple)) else str(rv)
            except Exception:
                _reason = ''
            if kind != _tl_last_kind or _reason != _tl_last_reason:
                timeline_rows.append({'time': str(timestamp), 'kind': kind, 'reason': _reason})
                _tl_last_kind = kind
                _tl_last_reason = _reason
            # 決定を構築
            decision = self._build_decision(
                ai_out=ai_out,
                filter_pass=filter_pass,
                filter_reasons=filter_reasons,
                entry_context=entry_context,
            )
            # region agent log
            # 決定構築直後の観測（最初の取引可能バーで1回のみ記録）
            if not getattr(self, "_dbg_once", False):
                try:
                    if self.trade_start_ts is None or timestamp >= self.trade_start_ts:
                        action = decision.get("action")
                        side = decision.get("side")
                        _dbg(
                            "E",
                            "app/core/backtest/backtest_engine.py:run",
                            "決定構築直後：最初の取引可能バー",
                            {
                                "bar_index": idx,
                                "timestamp": str(timestamp),
                                "prob_buy": float(getattr(ai_out, "p_buy", 0.0)) if ai_out is not None else None,
                                "prob_sell": float(getattr(ai_out, "p_sell", 0.0)) if ai_out is not None else None,
                                "threshold": float(getattr(self, "best_threshold", 0.0)),
                                "filter_level": int(getattr(self, "filter_level", -1)),
                                "filter_pass": bool(filter_pass),
                                "filter_reasons": filter_reasons if isinstance(filter_reasons, list) else [str(filter_reasons)] if filter_reasons else [],
                                "action": action,
                                "side": side,
                                "signal": decision.get("signal", {}),
                                "will_skip": action != "ENTRY" or side is None,
                            },
                        )
                        self._dbg_once = True
                except Exception:
                    pass
            # endregion agent log

            # シグナルカウンタを更新
            signal_side = decision.get("signal", {}).get("side")
            if signal_side == "BUY":
                debug_counters["n_signal_buy"] += 1
            elif signal_side == "SELL":
                debug_counters["n_signal_sell"] += 1

            # フィルタカウンタを更新
            if filter_pass:
                debug_counters["n_filter_pass"] += 1
            else:
                debug_counters["n_filter_fail"] += 1
                # 最初の1件の失敗理由を記録（ログ爆発を防ぐ）
                if debug_counters["n_filter_fail"] == 1:
                    # filter_reasons が空でない場合は最初の理由を、空の場合は "unknown" を記録
                    if filter_reasons and len(filter_reasons) > 0:
                        debug_counters["filter_fail_reason"] = str(filter_reasons[0])
                    else:
                        debug_counters["filter_fail_reason"] = "unknown"

            # decisions.jsonl に記録
            decision_trace = self._build_decision_trace(
                timestamp=timestamp,
                symbol=symbol,
                ai_out=ai_out,
                decision=decision,
                entry_context=entry_context,
            )
            self.decisions.append(decision_trace)

            # filter_pass = False の場合は見送り
            if not filter_pass:
                continue

            # 既存ポジションのクローズ判定（SL/TP判定 + ExitPolicy適用）
            if self.executor._open_position is not None:
                open_pos = self.executor._open_position
                should_close = False
                close_price = price
                close_reason = None

                # ExitPolicy: min_holding_bars チェック
                entry_bar_idx = getattr(open_pos, "_entry_bar_index", None)
                if entry_bar_idx is not None:
                    holding_bars_count = idx - entry_bar_idx
                    if holding_bars_count < self.exit_policy["min_holding_bars"]:
                        # min_holding_bars 未満の場合は exit を抑制（TP/SL/逆シグナルすべて）
                        should_close = False
                    else:
                        # min_holding_bars を満たした場合のみ exit 判定を実行
                        # SL/TP判定（tp_sl_eval_from_next_bar が True の場合は entry_bar と同じバーでは評価しない）
                        if self.exit_policy["tp_sl_eval_from_next_bar"] and idx == entry_bar_idx:
                            # エントリーと同じバーでは TP/SL を評価しない
                            pass
                        else:
                            # SL/TP判定
                            if open_pos.side == "BUY":
                                if open_pos.sl is not None and price <= open_pos.sl:
                                    if not self.exit_policy["exit_on_reverse_signal_only"]:
                                        should_close = True
                                        close_price = open_pos.sl
                                        close_reason = "SL"
                                elif open_pos.tp is not None and price >= open_pos.tp:
                                    if not self.exit_policy["exit_on_reverse_signal_only"]:
                                        should_close = True
                                        close_price = open_pos.tp
                                        close_reason = "TP"
                            else:  # SELL
                                if open_pos.sl is not None and price >= open_pos.sl:
                                    if not self.exit_policy["exit_on_reverse_signal_only"]:
                                        should_close = True
                                        close_price = open_pos.sl
                                        close_reason = "SL"
                                elif open_pos.tp is not None and price <= open_pos.tp:
                                    if not self.exit_policy["exit_on_reverse_signal_only"]:
                                        should_close = True
                                        close_price = open_pos.tp
                                        close_reason = "TP"

                        # 逆シグナル判定（exit_on_reverse_signal_only が True の場合のみ、または常にチェック）
                        if decision.get("action") == "ENTRY":
                            signal_side = decision.get("side")
                            if signal_side is not None and signal_side != open_pos.side:
                                # 逆シグナル検出
                                should_close = True
                                close_price = price
                                close_reason = "reverse_signal"

                        # 簡易版：次のバーでクローズ（SL/TPが無い場合、かつ exit_on_reverse_signal_only=False の場合のみ）
                        if (
                            not should_close
                            and not self.exit_policy["exit_on_reverse_signal_only"]
                            and idx < len(df_features) - 1
                        ):
                            # 次のバーでクローズ
                            should_close = True
                            close_price = float(df_features.iloc[idx + 1]["close"])
                            close_reason = "next_bar"
                else:
                    # entry_bar_index が記録されていない場合（既存挙動を維持）
                    # SL/TP判定
                    if open_pos.side == "BUY":
                        if open_pos.sl is not None and price <= open_pos.sl:
                            should_close = True
                            close_price = open_pos.sl
                        elif open_pos.tp is not None and price >= open_pos.tp:
                            should_close = True
                            close_price = open_pos.tp
                    else:  # SELL
                        if open_pos.sl is not None and price >= open_pos.sl:
                            should_close = True
                            close_price = open_pos.sl
                        elif open_pos.tp is not None and price <= open_pos.tp:
                            should_close = True
                            close_price = open_pos.tp

                    # 簡易版：次のバーでクローズ（SL/TPが無い場合）
                    if not should_close and idx < len(df_features) - 1:
                        # 次のバーでクローズ
                        should_close = True
                        close_price = float(df_features.iloc[idx + 1]["close"])

                if should_close:
                    closed_trade = self.executor.close_position(close_price, timestamp)

                    if closed_trade:
                        # エグジットカウンタを更新
                        debug_counters["n_exits"] += 1
                        # 連敗カウンタを更新
                        if closed_trade.pnl < 0:
                            self.consecutive_losses += 1
                        else:
                            self.consecutive_losses = 0

            # filter_pass = True の場合のみ SimulatedExecution に渡す
            # エントリー試行カウンタを更新
            debug_counters["n_entry_attempts"] += 1

            # 既存ポジション保有中の場合はブロック
            if self.executor._open_position is not None:
                if debug_counters["entry_block_reason"] is None:
                    debug_counters["entry_block_reason"] = "already_in_position"
                continue

            # decision.action が "ENTRY" でない、または side が None の場合はブロック
            action = decision.get("action")
            side = decision.get("side")
            # region agent log
            # エントリー判定直前の観測（最初のSKIPバーで必ず記録）
            if action != "ENTRY" or side is None:
                entry_block_reason = None
                if action != "ENTRY":
                    entry_block_reason = f"action_not_entry:{action}"
                elif side is None:
                    signal_side = decision.get("signal", {}).get("side")
                    if signal_side is None:
                        entry_block_reason = "signal_none"
                    else:
                        entry_block_reason = f"side_none:signal={signal_side}"
                
                # 最初のSKIPバーで必ず記録（single-shot）
                if debug_counters["entry_block_reason"] is None:
                    debug_counters["entry_block_reason"] = entry_block_reason
                    _dbg(
                        "D",
                        "app/core/backtest/backtest_engine.py:run",
                        "ENTRY判定直前：最初のSKIPバー",
                        {
                            "bar_index": idx,
                            "timestamp": str(timestamp),
                            "action": action,
                            "entry_block_reason": entry_block_reason,
                            "prob_buy": float(getattr(ai_out, "p_buy", 0.0)) if ai_out is not None else None,
                            "prob_sell": float(getattr(ai_out, "p_sell", 0.0)) if ai_out is not None else None,
                            "threshold": float(getattr(self, "best_threshold", 0.0)),
                            "filter_level": int(getattr(self, "filter_level", -1)),
                            "position": "open" if self.executor._open_position is not None else "flat",
                            "strategy_name": self.model_kind or "unknown",
                            "decision_signal_side": decision.get("signal", {}).get("side"),
                            "decision_side": side,
                            "decision_signal_reason": decision.get("signal", {}).get("reason"),
                            "decision_signal_pass_threshold": decision.get("signal", {}).get("pass_threshold"),
                            "decision_signal_confidence": decision.get("signal", {}).get("confidence"),
                            "filter_pass": decision.get("filter_pass"),
                            "filter_reasons": decision.get("filter_reasons", []),
                        },
                    )
            # endregion agent log
            if action != "ENTRY" or side is None:
                if debug_counters["entry_block_reason"] is None:
                    if action != "ENTRY":
                        debug_counters["entry_block_reason"] = f"action_not_entry:{action}"
                    elif side is None:
                        signal_side = decision.get("signal", {}).get("side")
                        if signal_side is None:
                            debug_counters["entry_block_reason"] = "signal_none"
                        else:
                            debug_counters["entry_block_reason"] = f"side_none:signal={signal_side}"
                continue

            # BUY/SELL のチェック（後方互換のため）
            if side not in ("BUY", "SELL"):
                if debug_counters["entry_block_reason"] is None:
                    debug_counters["entry_block_reason"] = f"invalid_side:{side}"
                continue

            lot = decision.get("lot", 0.1)
            atr = entry_context.get("atr")
            sl = decision.get("signal", {}).get("sl")
            tp = decision.get("signal", {}).get("tp")

            # region agent log
            # 実際のポジション開設直前の観測（最初のENTRY試行で記録）
            if debug_counters["n_entries"] == 0:
                _dbg(
                    "F",
                    "app/core/backtest/backtest_engine.py:run",
                    "ポジション開設直前：最初のENTRY試行",
                    {
                        "bar_index": idx,
                        "timestamp": str(timestamp),
                        "action": action,
                        "side": side,
                        "prob_buy": float(getattr(ai_out, "p_buy", 0.0)) if ai_out is not None else None,
                        "prob_sell": float(getattr(ai_out, "p_sell", 0.0)) if ai_out is not None else None,
                        "threshold": float(getattr(self, "best_threshold", 0.0)),
                        "filter_level": int(getattr(self, "filter_level", -1)),
                        "filter_pass": decision.get("filter_pass"),
                        "position_before": "open" if self.executor._open_position is not None else "flat",
                        "lot": lot,
                        "price": price,
                    },
                )
            # endregion agent log

            self.executor.open_position(
                side=side,
                price=price,
                timestamp=timestamp,
                lot=lot,
                atr=atr,
                sl=sl,
                tp=tp,
            )
            # ExitPolicy用：エントリー時のバーインデックスを記録
            if self.executor._open_position is not None:
                self.executor._open_position._entry_bar_index = idx
                self.executor._open_position._entry_trade_id = self._next_trade_id
                self._entry_bar_indices[self._next_trade_id] = idx
                self._next_trade_id += 1
            # エントリーカウンタを更新
            debug_counters["n_entries"] += 1
            
            # region agent log
            # ポジション開設直後の観測（最初のENTRY成功で記録）
            if debug_counters["n_entries"] == 1:
                _dbg(
                    "F",
                    "app/core/backtest/backtest_engine.py:run",
                    "ポジション開設直後：最初のENTRY成功",
                    {
                        "bar_index": idx,
                        "timestamp": str(timestamp),
                        "position_after": "open" if self.executor._open_position is not None else "flat",
                        "n_entries": debug_counters["n_entries"],
                    },
                )
            # endregion agent log

            # ループの末尾：5バーごとにequity_curve.csvに追記（ストリーミング更新）
            # 各バーの処理が一通り終わった後、エントリー有無に依存せず追記
            try:
                # 現在のequityを取得（全履歴再計算禁止：SimulatedExecution.equity を直接使用）
                current_equity = self.executor.equity
                
                # バッチに追加
                equity_batch.append({
                    "time": timestamp,
                    "equity": current_equity,
                    "signal": "HOLD",  # T-52では暫定HOLD固定
                })
                
                # 5バーごとに追記（バー index 基準）
                if (idx + 1) % 5 == 0:
                    if equity_csv_handle is None:
                        equity_csv_handle = open(equity_csv_path, "w", encoding="utf-8", newline="")
                        equity_csv_handle.write("time,equity,signal\n")
                        equity_csv_header_written = True
                    
                    for item in equity_batch:
                        equity_csv_handle.write(f"{item['time']},{item['equity']:.2f},{item['signal']}\n")
                    equity_csv_handle.flush()
                    equity_batch = []
            except Exception as e:
                # 追記失敗でも処理は継続（ログのみ）
                print(f"[BacktestEngine][warn] Failed to append equity_curve.csv: {e!r}", flush=True)

        # 最終バーで強制クローズ
        if self.executor._open_position is not None:
            final_price = float(df_features.iloc[-1]["close"])
            final_timestamp = pd.Timestamp(df_features.iloc[-1]["time"])
            # force_close_all()は戻り値がないので、close_position()を直接呼ぶ
            closed_trade = self.executor.close_position(final_price, final_timestamp)
            if closed_trade:
                debug_counters["n_exits"] += 1

        # 残りのバッチを出力
        if equity_batch:
            try:
                if equity_csv_handle is None:
                    equity_csv_handle = open(equity_csv_path, "w", encoding="utf-8", newline="")
                    equity_csv_handle.write("time,equity,signal\n")
                    equity_csv_header_written = True
                
                for item in equity_batch:
                    equity_csv_handle.write(f"{item['time']},{item['equity']:.2f},{item['signal']}\n")
                equity_csv_handle.flush()
            except Exception as e:
                print(f"[BacktestEngine][warn] Failed to flush remaining equity batch: {e!r}", flush=True)
        
        # equity_curve.csv のファイルハンドルを閉じる
        if equity_csv_handle is not None:
            try:
                equity_csv_handle.close()
            except Exception:
                pass

        # 出力ファイルを生成
        print(f"[BacktestEngine] Generating output files...", flush=True)
        # Step2-18: timeline を outputs へ渡す（CSV出力は _generate_outputs 側で行う）
        self._timeline_rows = timeline_rows
        result = self._generate_outputs(df_features, out_dir, symbol)

        # トレード数をカウント
        trades_df = self.executor.get_trades_df()
        debug_counters["n_trades"] = len(trades_df)

        # 追加カウンタを計算
        debug_counters["n_bars"] = len(df_features)
        debug_counters["n_signals"] = debug_counters["n_signal_buy"] + debug_counters["n_signal_sell"]

        # デバッグカウンタを結果に追加
        result["debug_counters"] = debug_counters

        return result

    def _build_entry_context(self, row: pd.Series, timestamp: pd.Timestamp) -> Dict[str, Any]:
        """
        EntryContext を作成する

        Parameters
        ----------
        row : pd.Series
            特徴量を含む行
        timestamp : pd.Timestamp
            タイムスタンプ

        Returns
        -------
        dict
            EntryContext
        """
        # プロファイル統計を取得
        profile_stats = {}
        try:
            stats = self.profile_stats_service.get_profile_stats([self.profile])
            if self.profile in stats:
                profile_stats = stats[self.profile].to_dict()
        except Exception:
            pass

        return {
            "timestamp": timestamp,
            "atr": float(row.get("atr", 0.0)) if "atr" in row else None,
            "volatility": float(row.get("volatility", 0.0)) if "volatility" in row else None,
            "trend_strength": float(row.get("trend_strength", 0.0)) if "trend_strength" in row else None,
            "consecutive_losses": self.consecutive_losses,
            "profile_stats": profile_stats,
        }

    def _build_decision(
        self,
        ai_out: Any,
        filter_pass: bool,
        filter_reasons: List[str],
        entry_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        決定を構築する

        Parameters
        ----------
        ai_out : Any
            Strategy.predict の出力
        filter_pass : bool
            フィルタ通過フラグ
        filter_reasons : List[str]
            フィルタNGの場合の理由リスト
        entry_context : Dict[str, Any]
            EntryContext

        Returns
        -------
        dict
            決定辞書
        """
        prob_buy = getattr(ai_out, "p_buy", None)
        prob_sell = getattr(ai_out, "p_sell", None)

        # decide_signal を使用してシグナル判定
        signal = decide_signal(
            prob_buy=prob_buy,
            prob_sell=prob_sell,
            best_threshold=self.best_threshold,
        )

        action = "SKIP"
        side = None

        if filter_pass and signal.side:
            action = "ENTRY"
            side = signal.side

        return {
            "action": action,
            "side": side,
            "filter_pass": filter_pass,
            "filter_reasons": filter_reasons,
            "signal": {
                "side": signal.side,
                "confidence": signal.confidence,
                "best_threshold": signal.best_threshold,
                "pass_threshold": signal.pass_threshold,
                "reason": signal.reason,
                "lot": 0.1,  # TODO: 実際のロット計算ロジックに合わせて修正
            },
        }

    def _build_decision_trace(
        self,
        timestamp: pd.Timestamp,
        symbol: str,
        ai_out: Any,
        decision: Dict[str, Any],
        entry_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        decisions.jsonl 用のトレースを構築する

        Parameters
        ----------
        timestamp : pd.Timestamp
            タイムスタンプ
        symbol : str
            シンボル名
        ai_out : Any
            Strategy.predict の出力
        decision : Dict[str, Any]
            決定辞書
        entry_context : Dict[str, Any]
            EntryContext

        Returns
        -------
        dict
            decisions.jsonl 用のレコード
        """
        prob_buy = getattr(ai_out, "p_buy", 0.0)
        prob_sell = getattr(ai_out, "p_sell", 0.0)
        meta = getattr(ai_out, "meta", {})
        if not isinstance(meta, dict):
            meta = {}

        ts_jst = timestamp.strftime("%Y-%m-%d %H:%M:%S")

        # filters_ctx を構築して正規化
        filters_ctx = {
            **entry_context,
            "filter_level": self.filter_level,
            "filter_reasons": decision.get("filter_reasons", []),
        }
        filters_ctx = self._normalize_filter_ctx(filters_ctx)

        # signal情報を取得
        signal_info = decision.get("signal", {})

        # decision_context を構築（判断材料を分離）
        decision_context = {
            "ai": {
                "prob_buy": round(prob_buy, 6),
                "prob_sell": round(prob_sell, 6),
                "model_name": getattr(ai_out, "model_name", "unknown"),
                "threshold": self.best_threshold,
            },
            "filters": {
                "filter_pass": decision.get("filter_pass"),
                "filter_reasons": filters_ctx.get("filter_reasons", []),
                "spread": filters_ctx.get("spread"),
                "adx": filters_ctx.get("adx"),
                "min_adx": filters_ctx.get("min_adx"),
                "atr_pct": filters_ctx.get("atr_pct"),
                "volatility": filters_ctx.get("volatility"),
                "filter_level": filters_ctx.get("filter_level"),
            },
            "decision": {
                "action": decision.get("action", "SKIP"),
                "side": decision.get("side"),
                "reason": decision.get("reason"),
                "blocked_reason": None,  # backtest では通常 None
            },
            "meta": meta,
        }

        # runtime を構築（環境状態のみ）
        from app.services import trade_state
        from core.utils.timeutil import now_jst_iso
        runtime = trade_state.build_runtime(
            symbol,
            ts_str=now_jst_iso(),  # backtest では現在時刻を使用
            spread_pips=filters_ctx.get("spread", 0.0),
            mode="backtest",
            source="backtest",
            timeframe=None,  # backtest では timeframe は未設定
            profile=self.profile,
        )

        return {
            "ts_jst": ts_jst,
            "type": "decision",
            "symbol": symbol,
            "strategy": getattr(ai_out, "model_name", "unknown"),
            "prob_buy": round(prob_buy, 6),  # 後方互換のため残す
            "prob_sell": round(prob_sell, 6),  # 後方互換のため残す
            "filter_pass": decision.get("filter_pass"),  # 後方互換のため残す
            "filter_reasons": filters_ctx.get("filter_reasons", []),  # 後方互換のため残す
            "filters": filters_ctx,  # 後方互換のため残す
            "meta": meta,  # 後方互換のため残す
            "decision": decision.get("action", "SKIP"),  # 後方互換のため残す
            "decision_detail": {  # 後方互換のため残す
                "action": decision.get("action", "SKIP"),
                "side": decision.get("side"),
                "signal": signal_info,
                "filter_pass": decision.get("filter_pass"),
                "filter_reasons": filters_ctx.get("filter_reasons", []),
            },
            "decision_context": decision_context,  # 新規追加：判断材料を分離
            "runtime": runtime,  # 新規追加：環境状態のみ
        }

    def _normalize_for_json(self, obj: Any) -> Any:
        """
        JSON シリアライズ可能な形式に変換する

        Parameters
        ----------
        obj : Any
            変換対象のオブジェクト

        Returns
        -------
        Any
            JSON 可能な形式に変換されたオブジェクト
        """
        import numpy as np
        import datetime

        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    def _normalize_for_json_recursive(self, obj: Any) -> Any:
        """
        JSON シリアライズ可能な形式に再帰的に変換する

        Parameters
        ----------
        obj : Any
            変換対象のオブジェクト（dict, list, その他）

        Returns
        -------
        Any
            JSON 可能な形式に変換されたオブジェクト
        """
        if isinstance(obj, dict):
            return {k: self._normalize_for_json_recursive(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._normalize_for_json_recursive(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self._normalize_for_json_recursive(v) for v in obj)
        return self._normalize_for_json(obj)

    def _validate_outputs(self, outputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        出力ファイルの検証を行う。

        Parameters
        ----------
        outputs : dict
            出力ファイルのパスを含む辞書

        Returns
        -------
        dict
            {"ok": bool, "errors": list[str]}
        """
        errors: List[str] = []

        # 必須ファイルの存在確認
        required_files = ["equity_curve", "trades", "monthly_returns", "decisions"]
        for key in required_files:
            file_path = outputs.get(key)
            if file_path is None:
                errors.append(f"Missing output key: {key}")
                continue

            path = Path(file_path) if not isinstance(file_path, Path) else file_path
            if not path.exists():
                errors.append(f"Output file does not exist: {path}")

        # equity_curve.csv の内容検証（空でない、必須列がある）
        equity_path = outputs.get("equity_curve")
        if equity_path:
            try:
                path = Path(equity_path) if not isinstance(equity_path, Path) else equity_path
                if path.exists():
                    df = pd.read_csv(path)
                    if df.empty:
                        errors.append(f"equity_curve.csv is empty: {path}")
                    elif "time" not in df.columns or "equity" not in df.columns:
                        errors.append(f"equity_curve.csv missing required columns (time, equity): {path}")
            except Exception as e:
                errors.append(f"Failed to validate equity_curve.csv: {e}")

        # monthly_returns.csv 必須列チェック
        monthly_returns_path = outputs.get("monthly_returns")
        if monthly_returns_path:
            mr = Path(monthly_returns_path) if not isinstance(monthly_returns_path, Path) else monthly_returns_path
            if not mr.exists():
                errors.append(f"missing: {mr}")
            else:
                try:
                    df = pd.read_csv(mr)
                    need = ["year_month", "return_pct", "max_dd_pct", "total_trades", "pf"]
                    miss = [c for c in need if c not in df.columns]
                    if miss:
                        errors.append(f"monthly_returns missing columns: {miss}")
                    if len(df) == 0:
                        errors.append("monthly_returns is empty")
                except Exception as e:
                    errors.append(f"failed to read monthly_returns: {e!r}")

        # decisions.jsonl が最低1行dictとして読めるか
        decisions_path = outputs.get("decisions")
        if decisions_path:
            dj = Path(decisions_path) if not isinstance(decisions_path, Path) else decisions_path
            if not dj.exists():
                errors.append(f"missing: {dj}")
            else:
                ok_any = False
                try:
                    with dj.open("r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if not line or not line.startswith("{"):
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue
                            if isinstance(obj, dict):
                                ok_any = True
                                break
                    if not ok_any:
                        errors.append("decisions.jsonl has no readable JSON dict line")
                except Exception as e:
                    errors.append(f"failed to read decisions.jsonl: {e!r}")

        return {
            "ok": len(errors) == 0,
            "errors": errors,
        }

    def _generate_outputs(
        self,
        df_features: pd.DataFrame,
        out_dir: Path,
        symbol: str,
    ) -> Dict[str, Any]:
        """
        出力ファイルを生成する

        Parameters
        ----------
        df_features : pd.DataFrame
            特徴量データ
        out_dir : Path
            出力ディレクトリ
        symbol : str
            シンボル名

        Returns
        -------
        dict
            出力ファイルのパス
        """
        out_dir.mkdir(parents=True, exist_ok=True)

        # トレード履歴を取得
        trades_df = self.executor.get_trades_df()

        # holding_bars と holding_days を計算して追加
        if not trades_df.empty:
            # entry_time と exit_time から holding_bars を計算
            entry_times = pd.to_datetime(trades_df["entry_time"])
            exit_times = pd.to_datetime(trades_df["exit_time"])
            
            # バーインデックスを取得（df_features の time 列と照合）
            timestamps = pd.to_datetime(df_features["time"])
            holding_bars_list = []
            holding_days_list = []
            
            for i, (entry_ts, exit_ts) in enumerate(zip(entry_times, exit_times)):
                # entry と exit のバーインデックスを取得
                entry_idx = timestamps.searchsorted(entry_ts, side="right") - 1
                exit_idx = timestamps.searchsorted(exit_ts, side="right") - 1
                
                # holding_bars = exit_idx - entry_idx（0以上）
                holding_bars = max(0, exit_idx - entry_idx)
                holding_bars_list.append(holding_bars)
                
                # holding_days = (exit_time - entry_time).days
                holding_days = (exit_ts - entry_ts).days
                holding_days_list.append(holding_days)
            
            trades_df = trades_df.copy()
            trades_df["holding_bars"] = holding_bars_list
            trades_df["holding_days"] = holding_days_list

        # エクイティ曲線を生成
        timestamps = pd.to_datetime(df_features["time"])
        prices = df_features["close"].astype(float)
        equity_series = self.executor.get_equity_curve(timestamps, prices)

        equity_df = pd.DataFrame({
            "time": equity_series.index,
            "equity": equity_series.values,
        })

        # ファイル出力（ストリーミング追記済みの場合はスキップ）
        equity_csv = out_dir / "equity_curve.csv"
        if equity_csv.exists() and equity_csv.stat().st_size > 0:
            print(f"[BacktestEngine] equity_curve.csv exists -> keep streaming output", flush=True)
        else:
            equity_df.to_csv(equity_csv, index=False)
            print(f"[BacktestEngine] Wrote {equity_csv}", flush=True)

        # --- Step2-18: write next_action_timeline.csv (run folder) ---
        tl_path = out_dir / 'next_action_timeline.csv'
        try:
            rows = getattr(self, '_timeline_rows', None) or []
            with tl_path.open('w', encoding='utf-8', newline='') as f:
                w = csv.writer(f)
                w.writerow(['time', 'kind', 'reason'])
                for row in rows:
                    w.writerow([row.get('time',''), row.get('kind',''), row.get('reason','')])
            print(f"[BacktestEngine] Wrote {tl_path}", flush=True)
        except Exception as e:
            print(f"[BacktestEngine][warn] could not write next_action_timeline.csv: {e!r}", flush=True)

        trades_csv = out_dir / "trades.csv"
        if not trades_df.empty:
            trades_df.to_csv(trades_csv, index=False)
            print(f"[BacktestEngine] Wrote {trades_csv}", flush=True)
        else:
            # 空のCSVを作成
            pd.DataFrame(columns=["entry_time", "entry_price", "exit_time", "exit_price", "side", "lot", "pnl"]).to_csv(trades_csv, index=False)

        # monthly_returns.csv を生成
        from tools.backtest_run import compute_monthly_returns
        monthly_csv = out_dir / "monthly_returns.csv"
        compute_monthly_returns(equity_csv, monthly_csv)
        print(f"[BacktestEngine] Wrote {monthly_csv}", flush=True)

        # decisions.jsonl を出力
        decisions_jsonl = out_dir / "decisions.jsonl"
        print(f"[BacktestEngine] _generate_outputs symbol(arg)={symbol!r}")
        if self.decisions:
            print(f"[BacktestEngine] decisions[0] type={type(self.decisions[0])} keys={list(self.decisions[0].keys())[:5] if isinstance(self.decisions[0], dict) else 'N/A'}")
        # decisions.jsonl の最終整形：symbol は run() 引数を絶対優先（運用ログと整合させる）
        # （生成側が USDJPY を入れても成果物は USDJPY- に統一される）
        for rec in self.decisions:
            if isinstance(rec, dict):
                rec["symbol"] = symbol
        if self.decisions and isinstance(self.decisions[0], dict):
            print(f"[BacktestEngine] decisions[0].symbol(after)={self.decisions[0].get('symbol')!r}")
        with open(decisions_jsonl, "w", encoding="utf-8") as f:
            for decision in self.decisions:
                normalized = self._normalize_for_json_recursive(decision)
                # --- ensure action field for condition mining ---
                if isinstance(normalized, dict) and ('action' not in normalized):
                    fp = normalized.get('filter_pass', None)
                    if fp is True:
                        normalized['action'] = 'ENTRY'
                    elif fp is False:
                        normalized['action'] = 'BLOCKED'
                    else:
                        normalized['action'] = 'HOLD'
                # --- end action ---
                f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
        print(f"[BacktestEngine] Wrote {decisions_jsonl}", flush=True)

        # --- 集約 decisions.jsonl を更新（M5直下） ---
        # 期間dir配下の decisions.jsonl が正なので、それを M5直下へ上書きして整合性を保つ
        agg_decisions_jsonl = out_dir.parent / "decisions.jsonl"
        try:
            with open(agg_decisions_jsonl, "w", encoding="utf-8") as f:
                for decision in self.decisions:
                    normalized = self._normalize_for_json_recursive(decision)
                    f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
            print(f"[BacktestEngine] Wrote {agg_decisions_jsonl}", flush=True)
        except Exception as e:
            print(f"[BacktestEngine][warn] could not update aggregate decisions.jsonl: {e!r}", flush=True)

        result = {
            "equity_curve": equity_csv,
            "trades": trades_csv,
            "monthly_returns": monthly_csv,
            "decisions": decisions_jsonl,
        }

        # 出力ファイルの検証
        validation_result = self._validate_outputs(result)
        result["output_ok"] = validation_result["ok"]
        result["output_errors"] = validation_result["errors"]

        return result

