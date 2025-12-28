import subprocess
import sys
from pathlib import Path

PY = sys.executable

def run(cmd):
    print("[RUN]", " ".join(cmd))
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    if p.returncode != 0:
        print(p.stdout)
        print(p.stderr)
        raise SystemExit(f"NG: command failed -> {' '.join(cmd)}")
    return p.stdout


def main():
    # 1) export
    run([PY, "scripts/export_val_probs.py"])

    # 2) report
    run([PY, "scripts/val_report.py"])

    # 3) artifacts check
    outs = [
        "logs/val_p_buy_raw.npy",
        "logs/val_y_true.npy",
        "logs/val_report.json",
        "logs/val_roc.png",
        "logs/val_calibration.png",
        "logs/val_thresholds.csv",
    ]

    missing = [p for p in outs if not Path(p).exists()]
    if missing:
        raise SystemExit(f"NG: missing artifacts -> {missing}")

    print("[OK] val smoke passed")
    for p in outs:
        print("  -", p)


if __name__ == "__main__":
    main()
