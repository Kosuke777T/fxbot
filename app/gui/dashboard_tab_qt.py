# app/gui/dashboard_tab_qt.py
from __future__ import annotations
from typing import Dict, Any
import json
import time

from PyQt6 import QtCore, QtWidgets
from core.metrics import METRICS_JSON, METRICS

class DashboardTab(QtWidgets.QWidget):
    """
    PyQt6版 Dashboard。runtime/metrics.json を1秒ごとに再読込し、
    値が無ければ core.metrics.METRICS(KVS) をフォールバック参照。
    """
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._labels: Dict[str, QtWidgets.QLabel] = {}

        group = QtWidgets.QGroupBox("Realtime Metrics (ATR / Grace / Trail)")
        grid = QtWidgets.QGridLayout()
        group.setLayout(grid)

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

        for r, (label, key) in enumerate(rows):
            grid.addWidget(QtWidgets.QLabel(label), r, 0, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
            val = QtWidgets.QLabel("-")
            val.setMinimumWidth(220)
            grid.addWidget(val, r, 1, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
            self._labels[key] = val

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(group)

        # タイマーで定期更新
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_metrics)
        self._timer.start()

        self._refresh_metrics()

    def _refresh_metrics(self) -> None:
        kv: Dict[str, Any] = {}
        try:
            with open(METRICS_JSON, "r", encoding="utf-8") as f:
                kv = json.load(f)
        except Exception:
            kv = METRICS.get()  # 同一プロセスKVSのフォールバック

        # 値の整形と描画
        self._set("last_decision", str(kv.get("last_decision", "-")))
        self._set("last_reason", str(kv.get("last_reason", "-")))
        self._set("atr_ref", f"{float(kv.get('atr_ref', 0) or 0):.6f}")
        self._set("atr_gate_state", str(kv.get("atr_gate_state", "-")))
        self._set("post_fill_grace", "ON" if kv.get("post_fill_grace") else "OFF")
        self._set("spread", str(kv.get("spread", "-")))
        self._set("prob_threshold", str(kv.get("prob_threshold", "-")))
        self._set("min_atr_pct", str(kv.get("min_atr_pct", "-")))
        adx = kv.get("adx"); m = kv.get("min_adx")
        self._set("adx_min", f"{adx} / {m}")
        self._set("trail_activated", "ON" if kv.get("trail_activated") else "OFF")
        self._set("trail_be_locked", "ON" if kv.get("trail_be_locked") else "OFF")
        self._set("trail_layers", str(kv.get("trail_layers", 0)))
        self._set("trail_current_sl", str(kv.get("trail_current_sl", "-")))
        cE = int(kv.get("count_entry", 0)); cS = int(kv.get("count_skip", 0)); cB = int(kv.get("count_blocked", 0))
        self._set("counts", f"{cE} / {cS} / {cB}")
        ts = kv.get("ts")
        local = "-" if not ts else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        self._set("ts", local)

    def _set(self, key: str, val: str) -> None:
        lab = self._labels.get(key)
        if lab is not None:
            lab.setText(val)
