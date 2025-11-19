# scripts/promote_model.py
import os, shutil, json, sys
from core.config import cfg
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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
        if fn.startswith("_backup_"): continue
        shutil.copy2(os.path.join(prod, fn), os.path.join(bk, fn))
    for fn in os.listdir(staging):
        shutil.copy2(os.path.join(staging, fn), os.path.join(prod, fn))
    print("PROMOTED. backup:", bk)


if __name__ == "__main__":
    main()
