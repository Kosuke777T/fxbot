# app/services/metrics.py
# T-62 以降: runtime/metrics.json は atomic write（tmp → os.replace）+ ロック時リトライ、失敗時は tmp 残して次回回復可能。
# 変更箇所: publish_metrics() 内の JSON 書き込みブロック（write_text → .tmp、os.replace で置換、リトライ、失敗時 tmp 残し）
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
import json
import os
import time
import traceback
from loguru import logger as _logger
from core.metrics import METRICS_JSON, METRICS  # METRICS_JSON はファイルパス、METRICS はKVS

# 確率履歴リング（最新100件）。Dashboard の p_buy/p_sell/p_skip グラフ用。
_PROBS_HISTORY_MAXLEN = 100
_probs_deque: deque = deque(maxlen=_PROBS_HISTORY_MAXLEN)
_probs_latest: Dict[str, Any] | None = None


_PROBS_DEDUP_EPS = 1e-6


def push_probs(p_buy: float, p_sell: float, p_skip: float, threshold: float) -> None:
    """
    確率が確定した直後に1回だけ呼ぶ。履歴をリングに追加し、probs_latest を更新する。
    直近と同一（eps 未満）なら append しない（変化時のみ履歴を伸ばす）。
    publish_metrics 内でこれらが JSON に merge される。
    """
    eps = _PROBS_DEDUP_EPS
    p_buy_f = float(p_buy)
    p_sell_f = float(p_sell)
    p_skip_f = float(p_skip)
    threshold_f = float(threshold)
    last = _probs_deque[-1] if _probs_deque else None
    if last is not None:
        if (
            abs(last["p_buy"] - p_buy_f) < eps
            and abs(last["p_sell"] - p_sell_f) < eps
            and abs(last["p_skip"] - p_skip_f) < eps
            and abs(last["threshold"] - threshold_f) < eps
        ):
            _logger.debug(
                "[probs] dedup skip (no change) p_buy={:.4f} p_sell={:.4f}",
                p_buy_f, p_sell_f,
            )
            return
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    entry = {"p_buy": p_buy_f, "p_sell": p_sell_f, "p_skip": p_skip_f, "threshold": threshold_f, "ts": ts}
    global _probs_latest
    _probs_latest = dict(entry)
    _probs_deque.append(entry)
    _logger.info(
        "[probs] pushed len={} p_buy={:.4f} p_sell={:.4f} p_skip={:.4f} thr={:.4f}",
        len(_probs_deque), p_buy_f, p_sell_f, p_skip_f, threshold_f,
    )

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

    # 確率履歴を merge（Dashboard の折れ線グラフ用）
    if _probs_latest is not None:
        data["probs_latest"] = dict(_probs_latest)
    _list = list(_probs_deque)
    if _list:
        data["probs_history"] = {
            "p_buy": [e["p_buy"] for e in _list],
            "p_sell": [e["p_sell"] for e in _list],
            "p_skip": [e["p_skip"] for e in _list],
            "threshold": float(_list[-1]["threshold"]) if _list else 0.52,
        }
    hist = data.get("probs_history")
    hist_len = len(hist.get("p_buy", [])) if isinstance(hist, dict) else 0
    _logger.info("[probs] publish merge ok hist_len={}", hist_len)
    # 観測点②：配布点（probs_history の末尾を時刻つきで保証）
    tail_p_buy = "n/a"
    if isinstance(hist, dict) and hist.get("p_buy"):
        _pb = hist["p_buy"]
        tail_p_buy = f"{_pb[-1]:.3f}" if _pb else "n/a"
    _logger.info("METRICS_WRITE bar_time=n/a probs_len={} tail_p_buy={}", hist_len, tail_p_buy)

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
