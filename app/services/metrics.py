# app/services/metrics.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any
import json, os, time, tempfile
import shutil,time
import traceback
from core.metrics import METRICS_JSON, METRICS  # METRICS_JSON はファイルパス、METRICS はKVS

def _metrics_enabled(no_metrics: bool = False) -> bool:
    """
    metrics の書き込みが有効かどうかを判定する。

    Parameters
    ----------
    no_metrics : bool, optional
        no_metrics フラグ（デフォルト: False）

    Returns
    -------
    bool
        True のとき metrics を書き込む
    """
    if no_metrics:
        return False
    v = os.getenv("FXBOT_NO_METRICS", "").strip().lower()
    return v not in ("1", "true", "on", "yes")

def publish_metrics(kv: Dict[str, Any], no_metrics: bool = False) -> None:
    """
    Dashboardが読むランタイム指標を KVS と JSON(atomic write) に出力する。
    必要なキー例は下記の通り（全部でなくてOK）:
      last_decision, last_reason, atr_ref, atr_gate_state, post_fill_grace,
      spread, prob_threshold, min_atr_pct, adx, min_adx,
      trail_activated, trail_be_locked, trail_layers, trail_current_sl,
      count_entry, count_skip, count_blocked, cb_tripped, cb_reason, ts
    """
    if os.getenv("FXBOT_METRICS_TRACE", "").strip().lower() in ("1", "true", "on", "yes"):
        print("[METRICS_TRACE][app] publish_metrics called:",
              "no_metrics=", no_metrics,
              "FXBOT_NO_METRICS=", os.getenv("FXBOT_NO_METRICS"))
        traceback.print_stack(limit=18)

    # metrics が無効な場合はスキップ
    if not _metrics_enabled(no_metrics):
        return

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
