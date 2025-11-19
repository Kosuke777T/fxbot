from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray


def _norm_importance(vals: Iterable[float]) -> List[float]:
    arr: NDArray[np.float64] = np.asarray(list(vals), dtype=float)
    if arr.size == 0:
        return []
    total = float(arr.sum())
    if total == 0.0:
        zero_dist = (np.ones_like(arr, dtype=float) / len(arr) * 100.0).astype(float).tolist()
        return cast(List[float], zero_dist)
    normalized = (arr / total * 100.0).astype(float).tolist()
    return cast(List[float], normalized)


def _lgbm_importance(model: Any, method: str = "gain") -> Optional[pd.DataFrame]:
    # LightGBM sklearn API or Booster を想定
    # method: "gain" or "split"
    booster = None
    feature_names = None
    if hasattr(model, "booster_"):
        booster = model.booster_
    elif hasattr(model, "booster"):
        booster = model.booster()
    elif hasattr(model, "model"):
        booster = getattr(model.model, "booster_", None)

    if booster is None:
        # fallback: sklearn APIの feature_importances_（=split相当が多い）
        if hasattr(model, "feature_importances_") and hasattr(model, "feature_name_"):
            vals = list(map(float, getattr(model, "feature_importances_")))
            feature_names = list(getattr(model, "feature_name_"))
            imp = _norm_importance(vals)
            return pd.DataFrame({"feature": feature_names, "importance": imp})
        return None

    # Booster から
    try:
        vals = booster.feature_importance(importance_type=method)
        feature_names = booster.feature_name()
        imp = _norm_importance(vals)
        return pd.DataFrame({"feature": feature_names, "importance": imp})
    except Exception:
        return None


def _xgb_importance(model: Any, method: str = "gain") -> Optional[pd.DataFrame]:
    # XGBoost：booster.get_score(importance_type=method) -> dict {feat: score}
    booster = None
    if hasattr(model, "get_booster"):
        booster = model.get_booster()
    elif hasattr(model, "booster"):
        booster = model.booster
    if booster is None:
        # fallback: sklearn APIの feature_importances_（仕様上gain相当ではない時もある）
        if hasattr(model, "feature_importances_"):
            vals = list(map(float, getattr(model, "feature_importances_")))
            # XGBのsklearnラッパは feature_names_in_ を持つ
            feats = getattr(model, "feature_names_in_", None)
            if feats is None:
                feats = [f"f{i}" for i in range(len(vals))]
            imp = _norm_importance(vals)
            return pd.DataFrame({"feature": feats, "importance": imp})
        return None

    try:
        score: Dict[str, float] = booster.get_score(importance_type=method)
        if not score:
            return None
        feats = list(score.keys())
        vals = list(score.values())
        imp = _norm_importance(vals)
        return pd.DataFrame({"feature": feats, "importance": imp})
    except Exception:
        return None


def extract_feature_importance(
    models: Dict[str, Any],
    method: str = "gain",
    top_n: int = 30,
) -> pd.DataFrame:
    """
    models: {"lgbm_cls": model_obj, "xgb_cls": model_obj, ...}
    method: "gain" | "split"（LightGBM/XGBoost両対応。XGBは"weight"=split相当）
    top_n : 上位Nのみ返す（モデルごとにtop_nを抽出→縦結合）

    return columns: ["feature","importance","model","method"]
    importanceは各モデル内で正規化（合計=100）後、top_n抽出。
    """
    frames: List[pd.DataFrame] = []
    for name, m in models.items():
        df = None
        # 判別ざっくり：文字列に"lightgbm" or "xgboost"が含まれるか、属性で判定
        module_name = type(m).__module__.lower()
        if "lightgbm" in module_name:
            df = _lgbm_importance(m, method=method)
        elif "xgboost" in module_name:
            # XGBoostの"split"相当はimportance_type="weight"
            xgb_method = method
            if method == "split":
                xgb_method = "weight"
            df = _xgb_importance(m, method=xgb_method)
        else:
            # 最後の手段：feature_importances_があれば使う
            if hasattr(m, "feature_importances_"):
                vals = list(map(float, getattr(m, "feature_importances_")))
                feats = getattr(m, "feature_names_in_", None)
                if feats is None:
                    feats = [f"f{i}" for i in range(len(vals))]
                imp = _norm_importance(vals)
                df = pd.DataFrame({"feature": feats, "importance": imp})

        if df is None or df.empty:
            continue

        df = df.sort_values("importance", ascending=False).head(top_n).copy()
        df["model"] = name
        df["method"] = method
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["feature", "importance", "model", "method"])

    out = pd.concat(frames, axis=0, ignore_index=True)
    # 表示安定化のためimportanceを丸める
    out["importance"] = out["importance"].round(2)
    return out.sort_values(["model", "importance"], ascending=[True, False]).reset_index(drop=True)
