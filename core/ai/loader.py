from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, cast, Sequence, Union

import json
import joblib
import numpy as np
import pandas as pd
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]

ArrayLike = Union[np.ndarray, Sequence[float]]

class ModelWrapper:
    """dict/ラッパーの多段ネストを再帰で“ほどき”、predict_proba/predictを安定提供する薄いラッパー。"""

    def __init__(self, obj_or_path: Union[str, Path, Any]) -> None:
        if isinstance(obj_or_path, (str, Path)):
            loaded = joblib.load(str(obj_or_path))
        else:
            loaded = obj_or_path

        self.base_model = self._unwrap(loaded, depth=0)
        self.classes_ = getattr(self.base_model, "classes_", None)
        self.model_name = getattr(self.base_model, "model_name", None) or \
                          getattr(self.base_model, "__class__", type("X",(object,),{})).__name__
        if isinstance(self.base_model, dict):
            try:
                print(f"[ModelWrapper][warn] still dict after unwrap. keys={list(self.base_model.keys())[:10]}")
            except Exception:
                print("[ModelWrapper][warn] still dict after unwrap (keys unavailable).")

    def _unwrap(self, obj: Any, depth: int = 0) -> Any:
        if depth > 5:
            return obj
        if hasattr(obj, "predict_proba") or hasattr(obj, "predict") or hasattr(obj, "decision_function"):
            return obj
        if isinstance(obj, dict):
            for key in ("model", "estimator", "clf", "base_model", "wrapped", "inner", "object"):
                if key in obj and obj[key] is not None:
                    out = self._unwrap(obj[key], depth + 1)
                    if hasattr(out, "predict_proba") or hasattr(out, "predict") or hasattr(out, "decision_function"):
                        return out
            for v in obj.values():
                out = self._unwrap(v, depth + 1)
                if hasattr(out, "predict_proba") or hasattr(out, "predict") or hasattr(out, "decision_function"):
                    return out
        if isinstance(obj, (list, tuple)):
            for v in obj:
                out = self._unwrap(v, depth + 1)
                if hasattr(out, "predict_proba") or hasattr(out, "predict") or hasattr(out, "decision_function"):
                    return out
        return obj

    def predict_proba(self, X: ArrayLike) -> np.ndarray:
        X_arr = X if isinstance(X, pd.DataFrame) else np.asarray(X)
        return self.base_model.predict_proba(X_arr)

    def predict(self, X: ArrayLike) -> np.ndarray:
        X_arr = X if isinstance(X, pd.DataFrame) else np.asarray(X)
        if hasattr(self.base_model, "predict"):
            return self.base_model.predict(X_arr)
        if hasattr(self.base_model, "decision_function"):
            scores = np.asarray(self.base_model.decision_function(X_arr), dtype=float)
            probs = 1.0 / (1.0 + np.exp(-scores))
            return (probs >= 0.5).astype(int)
        raise AttributeError("The underlying model has neither predict nor decision_function.")

def _load_pickle_or_joblib(path: str) -> Any:
    return joblib.load(path)

def _apply_calibration(calibrator: Any, p1: FloatArray) -> FloatArray:
    data = np.asarray(p1, dtype=float)
    if calibrator is None:
        return data
    if hasattr(calibrator, "transform"):
        transformed = calibrator.transform(data)
        return np.asarray(transformed, dtype=float)
    if hasattr(calibrator, "predict_proba"):
        proba = calibrator.predict_proba(data)
        return np.asarray(proba, dtype=float)
    return data

# ------------------------------------------------------------
# モデルバンドル（必要なら使う。未使用なら残しても害なし）
# ------------------------------------------------------------
@dataclass
class LGBBundle:
    name: str
    version: str
    clf: object            # predict_proba を持つ推論器
    feature_order: List[str]
    ready: bool = True

# ------------------------------------------------------------
# 校正付きラッパ（Booster / clf 両対応版）
# ------------------------------------------------------------
class _CalibratedWrapper:
    """
    base_model:
        - 通常: sklearn 系の clf (predict_proba を持つ)
        - 古いモデル: LightGBM Booster (predict のみ)
    calibrator:
        - None なら何もしない
        - transform / predict_proba を持っていればそれを適用
    """

    def __init__(self, base_model: Any, calibrator: Any, model_name: str = "(unknown)") -> None:
        self.base_model = base_model
        self.calibrator = calibrator
        self.model_name = model_name
        self.calibrator_name = getattr(calibrator, "method", "none") if calibrator else "none"
        # AISvc 側から埋められる（feature_order）
        self.expected_features: Optional[list[str]] = None

    def __getattr__(self, item: str) -> Any:
        # その他の属性は元モデルに委譲
        return getattr(self.base_model, item)

    def _raw_p1_from_model(self, X_arr: np.ndarray) -> FloatArray:
        """
        モデルから「クラス1の確率 or スコア」を 1次元配列で取り出す。

        - predict_proba があれば、それを優先
        - なければ predict をそのまま確率扱い（Booster想定）
        """
        # 1) 通常パス: predict_proba
        if hasattr(self.base_model, "predict_proba"):
            raw = self.base_model.predict_proba(X_arr)
            raw = np.asarray(raw, dtype=float)

            # (n, ) or (n, 1) or (n, 2) などを全部 1次元に落とす
            if raw.ndim == 1:
                p1 = raw.reshape(-1)
            elif raw.ndim == 2:
                if raw.shape[1] == 1:
                    p1 = raw[:, 0]
                else:
                    # 2列以上ある場合は「最後の列」を陽線クラスとして扱う
                    p1 = raw[:, -1]
            else:
                raise ValueError(f"unexpected predict_proba shape: {raw.shape}")
            return p1

        # 2) フォールバック: predict のみ
        if hasattr(self.base_model, "predict"):
            raw = self.base_model.predict(X_arr)
            p1 = np.asarray(raw, dtype=float).reshape(-1)
            return p1

        raise AttributeError("base_model has neither predict_proba nor predict")
    def predict_proba(self, X: Any) -> FloatArray:
        # DataFrameなら列名を保持したまま下流へ渡す（sklearn warning抑制）
        X_pass = X if isinstance(X, pd.DataFrame) else np.asarray(X, dtype=float)

        p1 = self._raw_p1_from_model(X_pass)

        # calibrator があれば適用
        if self.calibrator is not None:
            return _apply_calibrator_p1(p1, self.calibrator)

        return p1

# --- backward compatible API ---

def load_lgb_clf(*args, **kwargs):
    """Back-compat: active_model.json を唯一の真実としてロードし、推論器を返す（A案: 20特徴）。"""
    import json, joblib
    from pathlib import Path

    meta = json.loads(Path("models/active_model.json").read_text(encoding="utf-8"))
    pkl = Path("models") / meta["file"]
    obj = joblib.load(pkl)

    # ここで返すのは sklearn estimator / wrapper のどちらでも良いが、
    # scripts/export_val_probs.py は predict_proba を呼ぶので、それを満たす形に寄せる
    if hasattr(obj, "predict_proba") or hasattr(obj, "predict") or hasattr(obj, "decision_function"):
        return _CalibratedWrapper(obj, None)

    # dict等の多段ネストでも _CalibratedWrapper が unwrap できる前提
    return _CalibratedWrapper(obj, None)
