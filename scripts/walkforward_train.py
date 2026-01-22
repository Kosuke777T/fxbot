# scripts/walkforward_train.py
from __future__ import annotations
import argparse, os, json, time, shutil, hashlib, random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd

# 依存がなければロジ回帰にフォールバック
try:
    import lightgbm as lgb
    HAVE_LGB = True
except Exception:
    from sklearn.linear_model import LogisticRegression
    HAVE_LGB = False
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.calibration import CalibratedClassifierCV
import pickle

MODELS_DIR = "models"
DATA_DIR = "data"

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def _sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(8192), b""):
            h.update(ch)
    return h.hexdigest()

def load_dataset(csv_glob: str) -> pd.DataFrame:
    import glob
    files = sorted(glob.glob(os.path.join(DATA_DIR, csv_glob)))
    if not files:
        raise FileNotFoundError(f"No dataset CSVs under data/ matched: {csv_glob}")
    dfs = []
    for f in files:
        df = pd.read_csv(f)
        dfs.append(df)
    df = pd.concat(dfs, axis=0, ignore_index=True)
    # 期待カラム: time, open, high, low, close, label (0/1 for BUY=1), ...features...
    if "label" not in df.columns:
        raise ValueError("dataset must contain 'label' column (0/1)")
    return df

def split_walkforward(df: pd.DataFrame, weeks_train: int, weeks_valid: int, steps: int) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
    """
    直近から遡るWF。週単位で train/valid を切って steps 回。
    """
    if "time" in df.columns:
        dt = pd.to_datetime(df["time"])
    else:
        # 疑似時系列
        base = datetime(2020,1,1)
        dt = pd.Series([base + timedelta(minutes=i) for i in range(len(df))])
    df = df.copy()
    df["__dt__"] = dt

    spans = []
    end = df["__dt__"].max()
    for k in range(steps):
        valid_end = end - timedelta(weeks=k*weeks_valid)
        valid_start = valid_end - timedelta(weeks=weeks_valid)
        train_end = valid_start
        train_start = train_end - timedelta(weeks=weeks_train)

        tr = df[(df["__dt__"]>=train_start) & (df["__dt__"]<train_end)]
        va = df[(df["__dt__"]>=valid_start) & (df["__dt__"]<valid_end)]
        if len(tr) < 100 or len(va) < 100:
            continue
        spans.append((tr, va))
    spans.reverse()  # 古い→新しい
    return spans[-1:]  # 直近の1ステップだけで十分（高速）

def _train_one(tr: pd.DataFrame, va: pd.DataFrame, features: List[str]) -> Tuple[Any, Any, Dict[str, float]]:
    Xtr, ytr = tr[features].values, tr["label"].values
    Xva, yva = va[features].values, va["label"].values
    
    # ===== ラベル比率の観測（DATA BALANCE） =====
    total_samples = len(ytr)
    buy_count = int((ytr == 1).sum())
    sell_count = int((ytr == 0).sum())
    skip_count = int((~np.isin(ytr, [0, 1])).sum()) if isinstance(ytr, np.ndarray) else 0
    
    buy_pct = (buy_count / total_samples * 100.0) if total_samples > 0 else 0.0
    sell_pct = (sell_count / total_samples * 100.0) if total_samples > 0 else 0.0
    skip_pct = (skip_count / total_samples * 100.0) if total_samples > 0 else 0.0
    
    print(
        "[DATA BALANCE]\n"
        f"  total_samples: {total_samples}\n"
        f"  buy:  {buy_count:6d} ({buy_pct:5.1f}%)\n"
        f"  sell: {sell_count:6d} ({sell_pct:5.1f}%)\n"
        f"  skip: {skip_count:6d} ({skip_pct:5.1f}%)\n"
        f"  label_definition:\n"
        f"    buy  = 1 (BUY)\n"
        f"    sell = 0 (SELL)\n"
        f"    skip = その他（存在すれば）\n"
        f"  source:\n"
        f"    file: scripts/walkforward_train.py\n"
        f"    around: L79 (_train_one関数内、ytr確定直後)\n"
        f"    label_source: CSVの'label'列（0/1）"
    )

    if HAVE_LGB:
        clf = lgb.LGBMClassifier(
            n_estimators=300, learning_rate=0.05, max_depth=-1,
            subsample=0.8, colsample_bytree=0.8, random_state=42
        )
    else:
        clf = LogisticRegression(max_iter=200)

    clf.fit(Xtr, ytr)
    pva = clf.predict_proba(Xva)[:,1]
    auc = roc_auc_score(yva, pva)
    acc = accuracy_score(yva, (pva>=0.5).astype(int))

    # キャリブレータ（isotonic優先、フォールバックはsigmoid）
    try:
        cal = CalibratedClassifierCV(base_estimator=clf, method="isotonic", cv=5)
        cal.fit(Xtr, ytr)
        cal_name = "isotonic"
    except Exception:
        cal = CalibratedClassifierCV(base_estimator=clf, method="sigmoid", cv=5)
        cal.fit(Xtr, ytr)
        cal_name = "platt"

    return clf, cal, {"auc": float(auc), "acc": float(acc), "calibrator": cal_name}

def _features_from(df: pd.DataFrame) -> List[str]:
    # 最低限：OHLCや派生が入っている前提。label/time/非数値は除外。
    feats = [c for c in df.columns if c not in ("time","label") and pd.api.types.is_numeric_dtype(df[c])]
    if not feats:
        raise ValueError("no numeric features found.")
    return feats

def save_bundle(tag: str, clf: Any, cal: Any, features: List[str], classes: Dict[str, int], metrics: Dict[str, Any]) -> str:
    out = os.path.join(MODELS_DIR, tag)
    os.makedirs(out, exist_ok=True)

    with open(os.path.join(out, "LightGBM_clf.pkl"), "wb") as f:
        pickle.dump(clf, f)
    with open(os.path.join(out, "features.json"), "w", encoding="utf-8") as f:
        json.dump(features, f, ensure_ascii=False)
    with open(os.path.join(out, "classes.json"), "w", encoding="utf-8") as f:
        json.dump(classes, f, ensure_ascii=False)

    if metrics.get("calibrator") == "isotonic":
        with open(os.path.join(out, "calib_isotonic.pkl"), "wb") as f:
            pickle.dump(cal, f)
    else:
        with open(os.path.join(out, "calib_platt.pkl"), "wb") as f:
            pickle.dump(cal, f)

    # manifest
    sums = {}
    for name in ("LightGBM_clf.pkl","features.json","classes.json","calib_isotonic.pkl","calib_platt.pkl"):
        p = os.path.join(out, name)
        if os.path.exists(p):
            sums[name] = _sha256(p)

    mani = {
        "tag": tag,
        "ts": _now_iso(),
        "metrics": metrics,
        "sha256": sums,
        "features_hash": hashlib.sha256(json.dumps(features).encode()).hexdigest(),
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(mani, f, ensure_ascii=False, indent=2)

    # READY は最後に
    with open(os.path.join(out, "READY"), "w") as f:
        f.write("ok\n")
    return out

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="*.csv", help="data/ 以下で読むCSVのglob")
    ap.add_argument("--weeks-train", type=int, default=156, help="学習期間（週）=3年")
    ap.add_argument("--weeks-valid", type=int, default=12,  help="検証期間（週）=3ヶ月")
    ap.add_argument("--min-auc", type=float, default=0.55,  help="差し替えの最低AUC")
    ap.add_argument("--tag", default=None, help="出力タグ（デフォルトは日時）")
    args = ap.parse_args()

    df = load_dataset(args.csv)
    feats = _features_from(df)
    spans = split_walkforward(df, args.weeks_train, args.weeks_valid, steps=3)
    if not spans:
        raise SystemExit("not enough data for walk-forward.")

    tr, va = spans[-1]
    clf, cal, m = _train_one(tr, va, feats)

    print(f"[METRICS] AUC={m['auc']:.4f} ACC={m['acc']:.4f} CAL={m['calibrator']}")

    if m["auc"] < args.min_auc:
        print(f"[SKIP] AUC {m['auc']:.4f} < min_auc {args.min_auc}")
        return

    tag = args.tag or datetime.now().strftime("lgb_%Y%m%d_%H%M%S")
    bundle = save_bundle(tag, clf, cal, feats, {"BUY":1, "SELL":0}, m)
    print(f"[READY] {bundle}")

if __name__ == "__main__":
    main()
