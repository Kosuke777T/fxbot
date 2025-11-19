# app/gui/dashboard_tab.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]  # D:\macht\OneDrive\fxbot
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import json, os
from core.metrics import METRICS_JSON

import time
try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    # Tkinterが無い環境用メッセージ（Windows公式Pythonなら入ってます）
    raise

from app.services import trade_service
from core.metrics import METRICS


class DashboardTab(ttk.Frame):
    """
    Realtime Metrics (ATR / Grace / Trail) を表示するだけの軽量パネル。
    別スレッドでMETRICSが更新される前提。self.afterで1秒ごとにpull。
    """
    def __init__(self, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)

        self._vars = {}

        box = ttk.LabelFrame(self, text="Realtime Metrics (ATR / Grace / Trail)")
        box.pack(fill="x", padx=8, pady=6)

        grid = ttk.Frame(box)
        grid.pack(fill="x", padx=8, pady=8)

        rows = [
            ("Last decision", "last_decision"),
            ("Reason", "last_reason"),
            ("ATR ref", "atr_ref"),
            ("ATR gate", "atr_gate_state"),
            ("Post-fill grace", "post_fill_grace"),
            ("Spread", "spread"),
            ("ADX / Min", "adx_min"),
            ("Prob threshold", "prob_threshold"),
            ("Min ATR %", "min_atr_pct"),
            ("Trail: activated", "trail_activated"),
            ("Trail: BE locked", "trail_be_locked"),
            ("Trail: layers", "trail_layers"),
            ("Trail: current SL", "trail_current_sl"),
            ("Guard/Open", "guard_open"),
            ("Guard/Inflight", "guard_inflight"),
            ("Guard/LastFix", "guard_last_fix"),
            ("CB/Tripped", "cb_tripped"),
            ("CB/Reason", "cb_reason"),
            ("CB/ConsecLoss", "cb_consec"),
            ("CB/DailyLossJPY", "cb_daily_loss"),
            ("Counts ENTRY/SKIP/BLOCK", "counts"),
            ("Updated (local)", "ts"),
        ]

        for i, (label, key) in enumerate(rows):
            ttk.Label(grid, text=label, width=22).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            var = tk.StringVar(value="-")
            ttk.Label(grid, textvariable=var, width=28).grid(row=i, column=1, sticky="w", padx=4, pady=2)
            self._vars[key] = var

        # 最初の更新をセット
        self.after(500, self._refresh_metrics)

    def _refresh_metrics(self):
        # まずはファイルから読む（別プロセス更新に対応）
        kv = {}
        try:
            with open(METRICS_JSON, "r", encoding="utf-8") as f:
                kv = json.load(f)
        except Exception:
            # ファイルがまだ無い／壊れている場合はローカルKVSを参照
            from core.metrics import METRICS
            kv = METRICS.get()

        # 以降は同じ（値の反映処理）
        self._vars["last_decision"].set(str(kv.get("last_decision", "-")))
        self._vars["last_reason"].set(str(kv.get("last_reason", "-")))
        self._vars["atr_ref"].set(f"{float(kv.get('atr_ref', 0) or 0):.6f}")
        self._vars["atr_gate_state"].set(str(kv.get("atr_gate_state", "-")))
        self._vars["post_fill_grace"].set("ON" if kv.get("post_fill_grace") else "OFF")
        self._vars["spread"].set(str(kv.get("spread", "-")))
        self._vars["prob_threshold"].set(str(kv.get("prob_threshold", "-")))
        self._vars["min_atr_pct"].set(str(kv.get("min_atr_pct", "-")))
        adx = kv.get("adx"); m = kv.get("min_adx")
        self._vars["adx_min"].set(f"{adx} / {m}")
        self._vars["trail_activated"].set("ON" if kv.get("trail_activated") else "OFF")
        self._vars["trail_be_locked"].set("ON" if kv.get("trail_be_locked") else "OFF")
        self._vars["trail_layers"].set(str(kv.get("trail_layers", 0)))
        self._vars["trail_current_sl"].set(str(kv.get("trail_current_sl", "-")))
        cE = int(kv.get("count_entry", 0)); cS = int(kv.get("count_skip", 0)); cB = int(kv.get("count_blocked", 0))
        self._vars["counts"].set(f"{cE} / {cS} / {cB}")
        ts = kv.get("ts")
        local = "-" if not ts else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        self._vars["ts"].set(local)

        svc = getattr(trade_service, "SERVICE", None)
        guard_state = getattr(svc, "pos_guard", None)
        if guard_state:
            self._vars["guard_open"].set(str(guard_state.state.open_count))
            self._vars["guard_inflight"].set(str(len(guard_state.state.inflight_orders)))
            self._vars["guard_last_fix"].set(guard_state.state.last_fix_reason or "-")
        else:
            self._vars["guard_open"].set("-")
            self._vars["guard_inflight"].set("-")
            self._vars["guard_last_fix"].set("-")

        cb = getattr(svc, "cb", None) if svc else None
        cb_status = cb.status() if cb else {}
        self._vars["cb_tripped"].set(str(cb_status.get("tripped", False)))
        self._vars["cb_reason"].set(str(cb_status.get("reason", "-")))
        self._vars["cb_consec"].set(str(cb_status.get("consecutive_losses", "-")))
        self._vars["cb_daily_loss"].set(f"{float(cb_status.get('daily_loss_accum_jpy', 0.0)):.0f}")

        self.after(1000, self._refresh_metrics)
