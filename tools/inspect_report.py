import json, glob

rp = sorted(glob.glob("logs/retrain/report_*.json"))[-1]
with open(rp, encoding="utf-8") as f:
    j = json.load(f)

lk = j.get("lookahead", {})
print("report:", rp)
print("status:", j.get("status"))
print("selected_lookahead:", lk.get("selected"))
print("promote_thresholds:", j.get("promote_thresholds"))
print("metrics_test:", j.get("metrics_test"))
print("calibration:", j.get("calibration"))
print("candidates (lookahead -> f1@best, auc@cal):")
for c in lk.get("candidates", []):
    m = c["metrics_test"]
    print(f"  L={c['lookahead']}: f1@best={m.get('f1@best')}, auc@cal={m.get('auc@cal', m.get('auc'))}")
