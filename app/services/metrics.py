# app/services/metrics.py
# T-62 以降: runtime/metrics.json は atomic write（tmp → os.replace）+ ロック時リトライ、失敗時は tmp 残して次回回復可能。
# 変更箇所: publish_metrics() 内の JSON 書き込みブロック（write_text → .tmp、os.replace で置換、リトライ、失敗時 tmp 残し）
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any
import json
import os
import time
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

    # JSON（別プロセス連携／Dashboard標準入力）。atomic write: 同一ディレクトリの .tmp に書いて os.replace で置換。
    path = Path(METRICS_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(kv)
    data.setdefault("ts", int(time.time()))

    txt = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    tmp_path = path.parent / "metrics.json.tmp"

    try:
        tmp_path.write_text(txt, encoding="utf-8")
    except OSError as e:
        print(f"[metrics][warn] could not write tmp {tmp_path}: {e}")
        return

    # --- atomic replace with retry（読み手がいても壊れない／次回復帰可能） ---
    retry_delay_sec = 0.1
    retry_count = 5
    for _ in range(retry_count):
        try:
            os.replace(tmp_path, path)
            return
        except (PermissionError, OSError):
            time.sleep(retry_delay_sec)
    # 捨てず tmp を残す（次回 publish で上書きして再試行される）
    print(f"[metrics][warn] could not replace {path} (locked). tmp left for next retry.")
