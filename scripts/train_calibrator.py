# scripts/train_calibrator.py
from __future__ import annotations
import numpy as np, joblib
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

import sys
if not (Path("logs/val_p_buy_raw.npy").exists() and Path("logs/val_y_true.npy").exists()):
    sys.exit("val_p_buy_raw.npy / val_y_true.npy が見つかりません。先に検証推論を実行して保存してください。")
    
# 検証データの「生BUY確率」と「正解ラベル」を用意して保存しておく
# 例: logs/val_p_buy_raw.npy (shape (N,)), logs/val_y_true.npy (0/1)
p_raw = np.load("logs/val_p_buy_raw.npy").astype(float).ravel()
y_true = np.load("logs/val_y_true.npy").astype(int).ravel()

models = Path("models"); models.mkdir(exist_ok=True, parents=True)

# Platt
lr = LogisticRegression(max_iter=1000)
lr.fit(p_raw.reshape(-1,1), y_true)
joblib.dump(lr, models / "calib_platt.pkl")

# Isotonic
iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
iso.fit(p_raw, y_true)
joblib.dump(iso, models / "calib_isotonic.pkl")

print("wrote: models/calib_platt.pkl, models/calib_isotonic.pkl")
