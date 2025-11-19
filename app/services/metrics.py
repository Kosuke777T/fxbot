# app/services/metrics.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any
import json, os, time, tempfile
import shutil,time
from core.metrics import METRICS_JSON, METRICS  # METRICS_JSON はファイルパス、METRICS はKVS

def publish_metrics(kv: Dict[str, Any]) -> None:
    """
    Dashboardが読むランタイム指標を KVS と JSON(atomic write) に出力する。
    必要なキー例は下記の通り（全部でなくてOK）:
      last_decision, last_reason, atr_ref, atr_gate_state, post_fill_grace,
      spread, prob_threshold, min_atr_pct, adx, min_adx,
      trail_activated, trail_be_locked, trail_layers, trail_current_sl,
      count_entry, count_skip, count_blocked, cb_tripped, cb_reason, ts
    """
    # KVS（同一プロセス向けフォールバック）
    METRICS.update(**kv)

    # JSON（別プロセス連携／Dashboard標準入力）
    path = Path(METRICS_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(kv)
    # ts（ローカル更新時刻）はここで保証
    data.setdefault("ts", int(time.time()))

    txt = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    tmp_path = Path(tempfile.mkstemp(prefix="metrics_", suffix=".json", dir=str(path.parent))[1])
    tmp_path.write_text(txt, encoding="utf-8")

    # --- safe replace with retry ---
    for i in range(10):
        try:
            shutil.move(tmp_path, path)
            break
        except PermissionError:
            time.sleep(0.5)
    else:
        print(f"[metrics][warn] could not update {path} (still locked). skipped.")
    