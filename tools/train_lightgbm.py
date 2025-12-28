# tools/train_lightgbm.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pathlib import Path
import pandas as pd
import numpy as np
import lightgbm as lgb
import joblib
from app.strategies.ai_strategy import build_features_recipe

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "USDJPY" / "ohlcv" / "USDJPY_M15.csv"
MODEL_DIR = PROJECT_ROOT / "models"
MODEL_DIR.mkdir(exist_ok=True, parents=True)

# =============================
# パラメータ設定
# =============================
LOOKAHEAD = 5  # 5本先の終値と比較して上昇しているかを分類
THRESH_PCT = 0.001  # 0.1%以上上昇を1とみなす

# =============================
# データ読み込み & 特徴量生成
# =============================
print(f"[train] load {DATA_PATH}")
df = pd.read_csv(DATA_PATH)
df["time"] = pd.to_datetime(df["time"])

# build_features_recipe() は ai_strategy.py にある既存関数
feat = build_features_recipe(df, "ohlcv_tech_v1")

# 目的変数：5バー後に0.1%以上上昇しているか
feat["target"] = (feat["close"].shift(-LOOKAHEAD) / feat["close"] - 1.0 > THRESH_PCT).astype(int)
feat = feat.dropna().reset_index(drop=True)

X = feat.drop(columns=["time", "target"])
y = feat["target"]

print(f"[train] samples={len(X)} features={X.shape[1]} pos_rate={y.mean():.3f}")

# =============================
# モデル学習
# =============================
params = dict(
    objective="binary",
    metric="binary_logloss",
    learning_rate=0.05,
    num_leaves=31,
    n_estimators=200,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
)

model = lgb.LGBMClassifier(**params)
model.fit(X, y)

# =============================
# モデル保存（pkl と Booster の二刀流）
# =============================
MODEL_PATH = MODEL_DIR / "LightGBM_clf.pkl"
BOOSTER_PATH = MODEL_DIR / "LightGBM_clf.txt"

# pkl（互換性重視）
joblib.dump(model, MODEL_PATH, compress=0, protocol=4)
print(f"[train] saved model (pkl) -> {MODEL_PATH}")

# booster テキスト（フォールバック用）
try:
    booster = model.booster_
    booster.save_model(str(BOOSTER_PATH))
    print(f"[train] saved model (booster txt) -> {BOOSTER_PATH}")
except Exception as e:
    print(f"[train] WARN: booster save failed: {e}")
