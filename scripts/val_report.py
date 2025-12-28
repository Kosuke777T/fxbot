import json
from pathlib import Path
import numpy as np

# matplotlib は標準的に使う（色指定しない）
import matplotlib.pyplot as plt

try:
    from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss, roc_curve
except Exception as e:
    raise SystemExit(f"scikit-learn required: {e}")


def main() -> None:
    p_path = Path("logs/val_p_buy_raw.npy")
    y_path = Path("logs/val_y_true.npy")
    if not p_path.exists() or not y_path.exists():
        raise SystemExit("NG: logs/val_p_buy_raw.npy and logs/val_y_true.npy are required. Run scripts/export_val_probs.py first.")

    p = np.load(p_path).astype(float).reshape(-1)
    y = np.load(y_path).astype(int).reshape(-1)

    if p.shape[0] != y.shape[0]:
        raise SystemExit(f"NG: length mismatch p={p.shape} y={y.shape}")

    # guard
    p = np.clip(p, 1e-9, 1 - 1e-9)

    # metrics
    auc = float(roc_auc_score(y, p))
    ll = float(log_loss(y, np.vstack([1 - p, p]).T, labels=[0, 1]))
    brier = float(brier_score_loss(y, p))

    # calibration (10 bins)
    bins = np.linspace(0.0, 1.0, 11)
    idx = np.digitize(p, bins) - 1
    cal = []
    for b in range(10):
        m = idx == b
        if m.sum() == 0:
            cal.append({"bin": b, "count": 0, "p_mean": None, "y_rate": None})
        else:
            cal.append({
                "bin": b,
                "count": int(m.sum()),
                "p_mean": float(p[m].mean()),
                "y_rate": float(y[m].mean())
            })

    # threshold table
    thresholds = np.linspace(0.05, 0.95, 19)
    rows = []
    for th in thresholds:
        pred = (p >= th).astype(int)
        tp = int(((pred == 1) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        tn = int(((pred == 0) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        hit_rate = pred.mean()
        rows.append({
            "threshold": float(th),
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": float(prec),
            "recall": float(rec),
            "f1": float(f1),
            "signal_rate": float(hit_rate),
        })

    out = {
        "n": int(len(y)),
        "auc": auc,
        "logloss": ll,
        "brier": brier,
        "calibration_bins": cal,
        "thresholds": rows,
        "source": {
            "p": str(p_path).replace('\\\\', '/'),
            "y": str(y_path).replace('\\\\', '/')
        }
    }

    rep_path = Path("logs/val_report.json")
    rep_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote:", rep_path)

    # ROC plot
    fpr, tpr, _ = roc_curve(y, p)
    plt.figure()
    plt.plot(fpr, tpr)
    plt.plot([0, 1], [0, 1])
    plt.xlabel("FPR")
    plt.ylabel("TPR")
    plt.title("ROC curve")
    roc_path = Path("logs/val_roc.png")
    plt.savefig(roc_path, dpi=150, bbox_inches="tight")
    plt.close()
    print("wrote:", roc_path)

    # calibration plot
    xs = []
    ys = []
    for b in cal:
        if b["count"] > 0 and b["p_mean"] is not None and b["y_rate"] is not None:
            xs.append(b["p_mean"])
            ys.append(b["y_rate"])
    plt.figure()
    if xs:
        plt.plot(xs, ys, marker='o')
    plt.plot([0, 1], [0, 1])
    plt.xlabel("mean predicted p")
    plt.ylabel("empirical y rate")
    plt.title("Calibration")
    cal_path = Path("logs/val_calibration.png")
    plt.savefig(cal_path, dpi=150, bbox_inches="tight")
    plt.close()
    print("wrote:", cal_path)

    # thresholds CSV
    import csv
    csv_path = Path("logs/val_thresholds.csv")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("wrote:", csv_path)


if __name__ == "__main__":
    main()
