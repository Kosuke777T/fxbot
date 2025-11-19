from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MODELS_DIR = "models"
LIVE_LINK = os.path.join(MODELS_DIR, "live")


def _is_ready(bundle_dir: str) -> bool:
    ready = os.path.exists(os.path.join(bundle_dir, "READY"))
    manifest = os.path.exists(os.path.join(bundle_dir, "manifest.json"))
    return ready and manifest


def _latest_ready() -> str:
    candidates: list[tuple[float, str]] = []
    for bundle in glob.glob(os.path.join(MODELS_DIR, "*")):
        if (
            os.path.isdir(bundle)
            and _is_ready(bundle)
            and os.path.basename(bundle) not in {"live", "prev"}
        ):
            candidates.append((os.path.getmtime(bundle), bundle))
    if not candidates:
        raise SystemExit("no READY bundles found.")
    candidates.sort(reverse=True)
    return candidates[0][1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_latest_best_threshold() -> tuple[float | None, str | None]:
    try:
        report_dir = os.path.join("logs", "retrain")
        pattern = os.path.join(report_dir, "report_*.json")
        reports = sorted(glob.glob(pattern))
        if not reports:
            return None, None
        latest = reports[-1]
        with open(latest, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        metrics = data.get("metrics_test") or {}
        best_t = metrics.get("best_threshold")
        if isinstance(best_t, (int, float)):
            return float(best_t), latest
        return None, latest
    except Exception:
        return None, None


def activate_model_from_pkl(
    pkl_path: Path, models_dir: Path, target_name: str = "LightGBM_clf.pkl"
) -> int:
    models_dir.mkdir(parents=True, exist_ok=True)
    active_pkl = models_dir / target_name
    tmp = active_pkl.with_suffix(".tmp.pkl")
    shutil.copy2(pkl_path, tmp)
    if active_pkl.exists():
        backup = models_dir / f"backup_{int(time.time())}.pkl"
        shutil.copy2(active_pkl, backup)
    os.replace(tmp, active_pkl)

    best_t, source_report = _load_latest_best_threshold()

    meta = {
        "activated_at_jst": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "source": "direct-pkl",
        "source_path": str(pkl_path.resolve()),
        "target_path": str(active_pkl.resolve()),
        "sha256": _sha256(active_pkl),
        "version": str(time.time()),
        "features_hash": None,
    }
    meta["best_threshold"] = None if best_t is None else f"{float(best_t):.6f}"
    if source_report:
        meta["best_threshold_source_report"] = source_report
    with open(models_dir / "active_model.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f"[swap_model] ACTIVATED direct pkl -> {active_pkl}")
    return 0


def _fallback_main(args: list[str]) -> int:
    # READY探索に失敗した場合のフォールバック。単独実行にも対応。
    if args:
        candidate = Path(args[0])
        if candidate.suffix.lower() == ".pkl" and candidate.exists():
            return activate_model_from_pkl(candidate, Path("models"))
    print("no READY bundles found and no valid .pkl path provided.")
    return 1


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]
    try:
        target = _latest_ready()
    except SystemExit as exc:
        if exc.code:
            print(exc.code)
        return _fallback_main(args)

    prev = os.path.join(MODELS_DIR, "prev")
    if os.path.exists(prev):
        shutil.rmtree(prev)
    if os.path.exists(LIVE_LINK):
        shutil.move(LIVE_LINK, prev)
    shutil.copytree(target, LIVE_LINK)
    print(f"[SWAP] live -> {os.path.basename(target)} (prev saved)")
    return 0


if __name__ == "__main__":
    # 既存のREADY切替ロジックがsys.exitする前に、フォールバックを呼べるようにする保険。
    sys.exit(main())
