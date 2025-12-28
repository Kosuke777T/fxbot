from pathlib import Path
import sys

# --- project root bootstrap (scripts/ 直叩きでも app.* を import 可能にする) ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# ---------------------------------------------------------------------------

# scripts/promote_model.py
import os, shutil, json, sys
from app.core.config import cfg


def main() -> None:
    tr = cfg.get("training", {})
    staging = tr.get("staging_dir", "models/_staging")
    prod = tr.get("model_out_dir", "models")
    if not os.path.isdir(staging):
        raise SystemExit("staging not found")
    prom = os.path.join(staging, "PROMOTE.json")
    if os.path.exists(prom):
        st = json.load(open(prom, "r", encoding="utf-8"))
        print("PROMOTE.json:", st)
    # 上書き昇格
    os.makedirs(prod, exist_ok=True)
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(prod, f"_backup_{ts}")
    os.makedirs(bk, exist_ok=True)
    for fn in os.listdir(prod):
        if fn.startswith("_backup_"):
            continue
        src = os.path.join(prod, fn)
        dst = os.path.join(bk, fn)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    for fn in os.listdir(staging):
        src = os.path.join(staging, fn)
        dst = os.path.join(prod, fn)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    print("PROMOTED. backup:", bk)


if __name__ == "__main__":
    main()
