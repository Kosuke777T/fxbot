from __future__ import annotations

import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

USE_LGB = True
try:
    from lightgbm import LGBMClassifier
except Exception:
    USE_LGB = False
    from sklearn.linear_model import LogisticRegression

MODELS_DIR = Path("models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "ema_5",
    "ema_20",
    "rsi_14",
    "atr_14",
    "adx_14",
    "bbp",
    "vol_chg",
    "wick_ratio",
]


def make_synthetic(n: int = 5000, seed: int = 42) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(
        {
            "ema_5": rng.normal(0.0, 0.2, n),
            "ema_20": rng.normal(0.0, 0.2, n),
            "rsi_14": rng.uniform(0, 100, n),
            "atr_14": rng.uniform(0, 1, n),
            "adx_14": rng.uniform(5, 40, n),
            "bbp": rng.uniform(-0.5, 1.5, n),
            "vol_chg": rng.normal(0.0, 0.05, n),
            "wick_ratio": rng.uniform(0, 1, n),
        }
    )
    score = (
        0.8 * X["ema_5"]
        - 0.5 * X["ema_20"]
        + 0.01 * (X["rsi_14"] - 50)
        + 0.4 * (X["bbp"] - 0.5)
        - 0.2 * X["vol_chg"]
        + 0.15 * (X["adx_14"] > 20).astype(float)
    )
    p = 1 / (1 + np.exp(-score))
    y = (rng.uniform(0, 1, n) < p).astype(int)
    return X[FEATURES], y


def main() -> None:
    X, y = make_synthetic()

    if USE_LGB:
        model = LGBMClassifier(
            n_estimators=200,
            max_depth=-1,
            num_leaves=31,
            learning_rate=0.05,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=123,
            n_jobs=1,
        )
    else:
        model = LogisticRegression(max_iter=1000, n_jobs=1)

    model.fit(X, y)

    joblib.dump(model, MODELS_DIR / "LightGBM_clf.pkl")
    with open(MODELS_DIR / "LightGBM_clf.features.json", "w", encoding="utf-8") as f:
        json.dump(list(X.columns), f, ensure_ascii=False, indent=2)

    classes = getattr(model, "classes_", None)
    if classes is not None:
        with open(MODELS_DIR / "LightGBM_clf.classes.json", "w", encoding="utf-8") as f:
            json.dump([str(c) for c in classes], f, ensure_ascii=False, indent=2)

    print("[OK] Exported:")
    print(" - models/LightGBM_clf.pkl")
    print(" - models/LightGBM_clf.features.json")
    print(" - models/LightGBM_clf.classes.json (optional)")


if __name__ == "__main__":
    main()
