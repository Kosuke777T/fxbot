# tools/train_scaler.py
import json
import pandas as pd
from sklearn.preprocessing import StandardScaler

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROJECT_ROOT = ROOT

from app.strategies.ai_strategy import build_features_recipe

DATA_PATH = PROJECT_ROOT / "data" / "USDJPY" / "ohlcv" / "USDJPY_M15.csv"
INFO_PATH = PROJECT_ROOT / "models" / "LightGBM_info.json"
OUT_DIR = PROJECT_ROOT / "models" / "scalers"
OUT_DIR.mkdir(parents=True, exist_ok=True)

print(f"[train_scaler] load {DATA_PATH}")
df = pd.read_csv(DATA_PATH)
feat = build_features_recipe(df, "ohlcv_tech_v1")

# 学習時の列順を取り出す
info = json.loads(INFO_PATH.read_text(encoding="utf-8"))
cols = info["features"]
missing = [c for c in cols if c not in feat.columns]
if missing:
    raise RuntimeError(f"[train_scaler] missing columns from build_features: {missing}")

X = feat.loc[:, cols].dropna()

scaler = StandardScaler()
scaler.fit(X.values)

import joblib
joblib.dump(scaler, OUT_DIR / "std_v1.pkl")
print(f"[train_scaler] saved: {OUT_DIR / 'std_v1.pkl'} (shape={X.shape})")
