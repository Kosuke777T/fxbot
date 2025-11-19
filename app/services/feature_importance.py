# app/services/feature_importance.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


# LightGBM / XGBoost は「あるなら使う」スタイルにしておく
try:  # type: ignore[unused-ignore]
    import lightgbm as lgb
except Exception:  # ランタイムで LightGBM 未インストールでも死なないように
    lgb = None  # type: ignore[assignment]


try:  # type: ignore[unused-ignore]
    import xgboost as xgb
except Exception:
    xgb = None  # type: ignore[assignment]


@dataclass
class FeatureImportanceItem:
    """1つの特徴量についての FI 情報."""

    name: str
    importance: float
    importance_pct: float
    rank: int


def _unwrap_model(model: Any) -> Any:
    """
    CalibratedClassifierCV やラッパーに包まれている場合、
    中身の base_estimator / estimator をできるだけ剥がす。
    """
    for attr in ("base_estimator_", "base_estimator", "estimator_", "estimator"):
        inner = getattr(model, attr, None)
        if inner is not None:
            return inner
    return model


def _detect_model_type(model: Any) -> str:
    """
    LightGBM / XGBoost / その他 をざっくり判定する。
    """
    m = _unwrap_model(model)

    # LightGBM
    if lgb is not None:
        try:
            if isinstance(m, lgb.Booster):
                return "lightgbm"
        except Exception:
            pass
        try:
            from lightgbm.sklearn import LGBMModel  # type: ignore
        except Exception:
            LGBMModel = tuple()  # type: ignore[assignment]

        try:
            if isinstance(m, LGBMModel):
                return "lightgbm"
        except Exception:
            pass

    # XGBoost
    if xgb is not None:
        try:
            from xgboost import XGBModel  # type: ignore
        except Exception:
            XGBModel = tuple()  # type: ignore[assignment]

        try:
            if isinstance(m, XGBModel):
                return "xgboost"
        except Exception:
            pass

        try:
            if isinstance(m, xgb.Booster):
                return "xgboost"
        except Exception:
            pass

    return "unknown"


def _fi_lightgbm(
    model: Any,
    feature_names: Optional[Sequence[str]] = None,
) -> Tuple[np.ndarray, List[str]]:
    """
    LightGBM 用の raw FI 抽出.
    戻り値: (importances, names)
    """
    booster = None
    m = _unwrap_model(model)

    # sklearn API: LGBMClassifier / LGBMRegressor など
    if hasattr(m, "feature_importances_"):
        importances = np.asarray(getattr(m, "feature_importances_"), dtype=float)

        # feature_name_ があれば優先、それがなければ引数の feature_names
        names: List[str]
        fn = getattr(m, "feature_name_", None)
        if fn is not None:
            names = list(fn)
        elif feature_names is not None:
            names = list(feature_names)
        else:
            names = [f"f{i}" for i in range(len(importances))]

        return importances, names

    # Booster インスタンスを直接持っている場合
    if hasattr(m, "booster_"):
        booster = getattr(m, "booster_")
    elif lgb is not None and isinstance(m, lgb.Booster):
        booster = m

    if booster is not None:
        try:
            importances = np.asarray(
                booster.feature_importance(importance_type="gain"), dtype=float
            )
        except Exception:
            importances = np.asarray(
                booster.feature_importance(importance_type="split"), dtype=float
            )

        fn = getattr(booster, "feature_name", None)
        names: List[str]
        if callable(fn):
            names = list(fn())
        elif feature_names is not None:
            names = list(feature_names)
        else:
            names = [f"f{i}" for i in range(len(importances))]

        return importances, names

    raise ValueError("LightGBM モデルから feature_importance を取得できませんでした。")


def _fi_xgboost(
    model: Any,
    feature_names: Optional[Sequence[str]] = None,
) -> Tuple[np.ndarray, List[str]]:
    """
    XGBoost 用の raw FI 抽出.
    戻り値: (importances, names)
    """
    m = _unwrap_model(model)

    booster = None
    # sklearn API: XGBClassifier / XGBRegressor
    if hasattr(m, "get_booster"):
        booster = m.get_booster()
    elif xgb is not None and isinstance(m, xgb.Booster):
        booster = m

    if booster is None:
        raise ValueError("XGBoost モデルから Booster を取得できませんでした。")

    # gain ベースを優先、無ければ weight
    try:
        score_dict: Dict[str, float] = booster.get_score(importance_type="gain")
    except Exception:
        score_dict = {}

    if not score_dict:
        score_dict = booster.get_score(importance_type="weight")

    if not score_dict:
        raise ValueError("XGBoost Booster.get_score() が空でした。")

    # key は "f0", "f1" … のことが多い
    names: List[str] = []
    importances: List[float] = []

    for key, val in score_dict.items():
        # key が f0 形式なら index を解釈して feature_names と合わせる
        if feature_names is not None and key.startswith("f") and key[1:].isdigit():
            idx = int(key[1:])
            if 0 <= idx < len(feature_names):
                fname = str(feature_names[idx])
            else:
                fname = key
        else:
            fname = key

        names.append(fname)
        importances.append(float(val))

    return np.asarray(importances, dtype=float), names


def compute_feature_importance(
    model: Any,
    feature_names: Optional[Sequence[str]] = None,
    top_n: Optional[int] = 30,
) -> List[FeatureImportanceItem]:
    """
    LightGBM / XGBoost モデルから Feature Importance を取り出し、
    降順ソート＋割合付きのリストにして返す。

    Parameters
    ----------
    model:
        LightGBM / XGBoost の学習済みモデル
        （calibration ラッパー付きでも OK）
    feature_names:
        特徴量名のシーケンス。
        モデル側から取れない場合にここで補う。
    top_n:
        上位いくつまで返すか。None なら全件。
    """
    model_type = _detect_model_type(model)

    if model_type == "lightgbm":
        importances, names = _fi_lightgbm(model, feature_names)
    elif model_type == "xgboost":
        importances, names = _fi_xgboost(model, feature_names)
    else:
        raise ValueError(
            f"未対応のモデルタイプです: {_detect_model_type(model)} "
            "(LightGBM / XGBoost 以外は compute_feature_importance() では扱いません)"
        )

    if len(importances) == 0:
        raise ValueError("feature_importance の長さが 0 です。")

    # 負の値などが来ても一応扱えるように abs を取る
    imp = np.asarray(importances, dtype=float)
    imp = np.nan_to_num(imp, nan=0.0)
    total = float(np.sum(np.abs(imp)))
    if total <= 0:
        # すべて 0 の場合は一律 0%
        pct = np.zeros_like(imp, dtype=float)
    else:
        pct = (np.abs(imp) / total) * 100.0

    items: List[FeatureImportanceItem] = []
    for name, v, p in zip(names, imp, pct):
        items.append(
            FeatureImportanceItem(
                name=str(name),
                importance=float(v),
                importance_pct=float(p),
                rank=-1,  # ここでは仮。あとでソートして rank を振る。
            )
        )

    # importance 降順でソート
    items.sort(key=lambda x: x.importance, reverse=True)

    # rank を振り直し
    for i, it in enumerate(items, start=1):
        it.rank = i

    # top_n で切る
    if top_n is not None and top_n > 0:
        items = items[:top_n]

    return items
