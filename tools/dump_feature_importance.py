# tools/dump_feature_importance.py
import csv
import glob
import json
import os
from datetime import datetime
from typing import Any, Iterable, Tuple

import joblib


def _load_latest_report() -> Tuple[str | None, dict[str, Any] | None]:
    rps = sorted(glob.glob(os.path.join("logs", "retrain", "report_*.json")))
    if not rps:
        return None, None
    rp = rps[-1]
    with open(rp, encoding="utf-8") as f:
        j = json.load(f)
    return rp, j


def _load_features_from_report(j: dict[str, Any]) -> list[str]:
    feats = j.get("features") or []
    return list(feats)


def _load_model(pkl_path: str) -> Any:
    return joblib.load(pkl_path)


def _write_feat_csv(model: Any, feat_cols: list[str], out_csv: str) -> str:
    try:
        booster = getattr(model, "booster_", None)
        if booster is None:
            split_importance = getattr(model, "feature_importances_", None)
            gain_importance = None
            names = feat_cols
        else:
            names = booster.feature_name()
            split_importance = booster.feature_importance(importance_type="split")
            gain_importance = booster.feature_importance(importance_type="gain")

        def _all_column_style(xs: Iterable[str]) -> bool:
            if not xs:
                return False
            return all(str(x).startswith("Column_") for x in xs)

        if not names or len(names) != len(feat_cols) or _all_column_style(names):
            names = feat_cols[:]

        rows: list[dict[str, float | str]] = []
        for i, name in enumerate(names):
            s = (
                float(split_importance[i])
                if (split_importance is not None and i < len(split_importance))
                else 0.0
            )
            g = (
                float(gain_importance[i])
                if (gain_importance is not None and i < len(gain_importance))
                else 0.0
            )
            rows.append({"feature": name, "gain": g, "split": s})
        rows.sort(key=lambda r: (r["gain"], r["split"]), reverse=True)

        os.makedirs(os.path.dirname(out_csv), exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["feature", "gain", "split"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return out_csv
    except Exception as e:
        print(f"[dump] failed: {e}")
        return ""


def main() -> None:
    # モデルは active_model.json or 既定の models/LightGBM_clf.pkl を使う
    active_meta = os.path.join("models", "active_model.json")
    if os.path.exists(active_meta):
        try:
            j = json.load(open(active_meta, encoding="utf-8"))
            model_path = (
                j.get("target_path")
                or j.get("source_path")
                or os.path.join("models", "LightGBM_clf.pkl")
            )
        except Exception:
            model_path = os.path.join("models", "LightGBM_clf.pkl")
    else:
        model_path = os.path.join("models", "LightGBM_clf.pkl")

    rp, rep = _load_latest_report()
    if rep is None:
        print("[dump] no reports found. specify features manually.")
        return
    feats = _load_features_from_report(rep)
    if not feats:
        print("[dump] features not found in report.")
        return

    model = _load_model(model_path)
    tag = "manual"
    if rep is not None:
        lh = (rep.get("lookahead") or {}).get("selected")
        tag = f"lk{lh}" if lh is not None else "manual"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = os.path.join("logs", "retrain", f"feat_importance_{tag}_{ts}.csv")
    out = _write_feat_csv(model, feats, out_csv)
    if out:
        print("[dump] wrote:", out)
        print("[dump] top5 preview:")
        with open(out, encoding="utf-8") as f:
            for i, line in enumerate(f):
                print(line.rstrip())
                if i >= 5:
                    break


if __name__ == "__main__":
    main()
