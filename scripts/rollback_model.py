# scripts/rollback_model.py
import os, shutil, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> None:
    prod = "models"
    bks = sorted([d for d in os.listdir(prod) if d.startswith("_backup_")])
    if not bks:
        raise SystemExit("no backups found")
    last = os.path.join(prod, bks[-1])
    for fn in os.listdir(last):
        shutil.copy2(os.path.join(last, fn), os.path.join(prod, fn))
    print("ROLLED BACK to:", last)


if __name__ == "__main__":
    main()
