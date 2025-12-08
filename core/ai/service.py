from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import glob
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from core.ai.loader import ModelWrapper, load_lgb_clf


def _read_active_model_path(default: str = "models/LightGBM_clf.pkl") -> str:
    """Resolve model path from active_model.json (target_path -> source_path -> default)."""
    meta_path = Path("models") / "active_model.json"
    if meta_path.exists():
        try:
            with meta_path.open("r", encoding="utf-8") as fh:
                meta = json.load(fh)
            return meta.get("target_path") or meta.get("source_path") or default
        except Exception as exc:
            logger.warning(f"failed to read active_model.json: {exc}")
    return default


def _as_2d_frame(X: Any) -> pd.DataFrame:
    """任意XをLightGBMに渡せる2D DataFrameに整形する。"""
    if isinstance(X, pd.DataFrame):
        return X.copy()
    if isinstance(X, dict):
        return pd.DataFrame([X])
    if isinstance(X, (list, tuple, np.ndarray)):
        arr = np.asarray(X, dtype=float)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        return pd.DataFrame(arr)
    return pd.DataFrame([[X]])

class _ProbOut:
    __slots__ = ("p_buy", "p_sell", "p_skip", "meta", "model_name", "version", "features_hash")

    def __init__(
        self,
        p_buy: float,
        p_sell: float,
        *,
        model_name: str = "(unknown)",
        version: str = "na",
        features_hash: str = "",
    ) -> None:
        buy_val = float(p_buy)
        sell_val = float(p_sell)
        self.p_buy = buy_val
        self.p_sell = sell_val
        self.p_skip = float(min(buy_val, sell_val))
        self.meta = "BUY" if buy_val >= sell_val else "SELL"
        self.model_name = model_name
        self.version = version
        self.features_hash = features_hash

    def model_dump(self) -> Dict[str, float | str]:
        return {
            "model_name": self.model_name,
            "version": self.version,
            "features_hash": self.features_hash,
            "p_buy": self.p_buy,
            "p_sell": self.p_sell,
            "p_skip": self.p_skip,
            "meta": self.meta,
        }


ProbOut = _ProbOut  # backward compatibility for external imports

__all__ = ["AISvc", "ProbOut", "_as_2d_frame", "_read_active_model_path"]


@dataclass
class AISvc:
    threshold: float = 0.52
    model: Optional[object] = None
    model_name: str = "unknown"
    calibrator_name: str = "none"
    is_dummy: bool = False
    expected_features: Optional[list[str]] = None

    def __post_init__(self) -> None:
        self._initialize_model()

    def _resolve_model_path(self) -> str:
        """
        優先順：
          1) self.model_path（あれば）
          2) runtime/active_model.json の "path"
          3) 既定: models/LightGBM_clf.pkl
        """
        if getattr(self, "model_path", None):
            return str(self.model_path)

        try:
            p = Path("runtime/active_model.json")
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                m = d.get("path")
                if m:
                    return str(m)
        except Exception:
            pass

        return "models/LightGBM_clf.pkl"

    def _initialize_model(self, model_path: str | None = None):
        from core.ai.loader import ModelWrapper
        from pathlib import Path

        model_path = model_path or self._resolve_model_path()

        # 何が返っても最終的に推定器へ到達できるよう ModelWrapper で統一
        bundle = load_lgb_clf(model_path)
        self.model = ModelWrapper(bundle)

        # 表示名
        self.model_name = getattr(self.model, "model_name", None) or Path(model_path).name or "(unknown)"
        print(f"[AISvc] loaded model: {self.model_name}")

        # 期待特徴量のロード（既存メソッド）
        self._load_expected_features()

    def _load_expected_features(self) -> None:
        """最新レポートの features を expected_features として保持"""
        try:
            meta_path = os.path.join("models", "active_model.json")
            report = None
            if os.path.isfile(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                report = meta.get("best_threshold_source_report")
            if not report or not os.path.isfile(report or ""):
                candidates = sorted(glob.glob(os.path.join("logs", "retrain", "report_*.json")))
                if candidates:
                    report = candidates[-1]
            if report and os.path.isfile(report):
                with open(report, encoding="utf-8") as f:
                    data = json.load(f)
                feats = data.get("features")
                if isinstance(feats, list) and feats:
                    self.expected_features = feats
                    print(f"[AISvc] expected_features loaded ({len(feats)} cols) from {report}")
                    return
        except Exception as exc:
            print(f"[AISvc][warn] expected_features load failed: {exc}")
        self.expected_features = None

    def predict(self, X: Any, *, no_metrics: bool = False) -> _ProbOut:
        """
        Parameters
        ----------
        X : Any
            特徴量（DataFrame, dict, array など）
        no_metrics : bool, optional
            True の場合、metrics の更新を行わない（デフォルト: False）
        """
        if self.model is None:
            return _ProbOut(0.5, 0.5, model_name=self.model_name, version="na", features_hash="")

        df = _as_2d_frame(X)
        if self.expected_features:
            for col in self.expected_features:
                if col not in df.columns:
                    df[col] = 0.0
            df = df[self.expected_features]

        values = df.to_numpy(dtype=float, copy=False)
        if hasattr(self.model, "predict_proba"):
            probs = np.asarray(self.model.predict_proba(values), dtype=float)
        elif hasattr(self.model, "predict"):
            preds = np.asarray(self.model.predict(values), dtype=float).reshape(-1, 1)
            probs = np.column_stack([1.0 - preds, preds])
        else:
            probs = np.zeros((len(values), 2), dtype=float)

        features_hash = ""
        try:
            if not df.empty:
                features_hash = str(hash(tuple(df.iloc[0].astype(float).values.tolist())))
        except Exception:
            features_hash = ""

        if probs.ndim == 2:
            p_buy = probs[0, 1]
            p_sell = probs[0, 0]
        else:
            p_buy = float(probs)
            p_sell = 1.0 - p_buy

        version = getattr(getattr(self.model, "clf", self.model), "version", "na")
        return _ProbOut(
            p_buy,
            p_sell,
            model_name=self.model_name,
            version=str(version),
            features_hash=features_hash,
        )
