from typing import Optional
from pathlib import Path
import json
from datetime import datetime, timezone
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.config_loader import load_config
from app.services import circuit_breaker, trade_state
from app.services.orderbook_stub import orderbook


_RUNTIME_FLAGS_PATH = Path("config/runtime_flags.json")


def _write_runtime_flags(trading_enabled: bool) -> None:
    """
    GUI→daemon の最小同期（ファイル永続）。
    daemon 側は trading_enabled だけを参照する（他キーは観測用）。
    """
    _RUNTIME_FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "trading_enabled": bool(trading_enabled),
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source": "gui",
    }
    _RUNTIME_FLAGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class ControlTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self._main_window = main_window
        self.setLayout(QVBoxLayout())
        root_layout: Optional[QVBoxLayout] = self.layout()  # guard for static analyzers
        if root_layout is None:
            root_layout = QVBoxLayout()
            self.setLayout(root_layout)

        # 運転
        box_run = QGroupBox("運転")
        lay_run = QHBoxLayout()
        self.btn_toggle = QPushButton("取引：停止中（クリックで開始）")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.clicked.connect(self._toggle_trading)

        self.btn_close_all = QPushButton("全クローズ（ドライラン）")
        self.btn_close_all.clicked.connect(self._close_all_mock)

        self.btn_cb_reset = QPushButton("サーキット解除")
        self.btn_cb_reset.clicked.connect(self._cb_reset)

        self.btn_test_entry = QPushButton("テストエントリー（SL/TP）")
        if self._main_window is not None and hasattr(self._main_window, "request_test_entry"):
            self.btn_test_entry.clicked.connect(self._main_window.request_test_entry)

        lay_run.addWidget(self.btn_toggle)
        lay_run.addWidget(self.btn_test_entry)
        lay_run.addWidget(self.btn_close_all)
        lay_run.addWidget(self.btn_cb_reset)
        box_run.setLayout(lay_run)
        root_layout.addWidget(box_run)

        # しきい値
        box_thr = QGroupBox("エントリーしきい値（確信度）")
        lay_thr = QHBoxLayout()
        self.lbl_buy = QLabel("買い: 0.60")
        self.sld_buy = QSlider(Qt.Orientation.Horizontal)
        self.sld_buy.setRange(50, 80)
        self.sld_buy.setValue(60)
        self.sld_buy.valueChanged.connect(self._on_thr_changed)

        self.lbl_sell = QLabel("売り: 0.60")
        self.sld_sell = QSlider(Qt.Orientation.Horizontal)
        self.sld_sell.setRange(50, 80)
        self.sld_sell.setValue(60)
        self.sld_sell.valueChanged.connect(self._on_thr_changed)

        lay_thr.addWidget(self.lbl_buy)
        lay_thr.addWidget(self.sld_buy)
        lay_thr.addSpacing(12)
        lay_thr.addWidget(self.lbl_sell)
        lay_thr.addWidget(self.sld_sell)
        box_thr.setLayout(lay_thr)
        root_layout.addWidget(box_thr)

        # 決済
        box_exit = QGroupBox("決済（固定pips）")
        lay_exit = QHBoxLayout()
        self.sp_sl = QSpinBox()
        self.sp_sl.setRange(1, 200)
        self.sp_sl.setValue(10)
        self.sp_sl.valueChanged.connect(self._on_exit_changed)

        self.sp_tp = QSpinBox()
        self.sp_tp.setRange(1, 300)
        self.sp_tp.setValue(15)
        self.sp_tp.valueChanged.connect(self._on_exit_changed)

        lay_exit.addWidget(QLabel("SL"))
        lay_exit.addWidget(self.sp_sl)
        lay_exit.addSpacing(16)
        lay_exit.addWidget(QLabel("TP"))
        lay_exit.addWidget(self.sp_tp)
        box_exit.setLayout(lay_exit)
        root_layout.addWidget(box_exit)

        # 状態表示
        self.lbl_status = QLabel("")
        root_layout.addWidget(self.lbl_status)

        self._sync_from_state()

    def _sync_from_state(self):
        s = trade_state.get_settings()
        self.btn_toggle.setChecked(s.trading_enabled)
        self.btn_toggle.setText(
            "取引：稼働中（クリックで停止）" if s.trading_enabled else "取引：停止中（クリックで開始）"
        )
        self.sld_buy.setValue(int(s.threshold_buy * 100))
        self.sld_sell.setValue(int(s.threshold_sell * 100))
        self.sp_sl.setValue(int(s.sl_pips))
        self.sp_tp.setValue(int(s.tp_pips))
        self._refresh_status()

    def _refresh_status(self):
        s = trade_state.as_dict()
        state_txt = "稼働中" if s["trading_enabled"] else "停止中"
        self.lbl_status.setText(
            f"状態: {state_txt} / 買い閾値: {s['threshold_buy']:.2f} / 売り閾値: {s['threshold_sell']:.2f} / "
            f"SL: {s['sl_pips']} / TP: {s['tp_pips']}"
        )

    def _toggle_trading(self):
        try:
            enabled = self.btn_toggle.isChecked()
            trade_state.update(trading_enabled=enabled)
            _write_runtime_flags(trading_enabled=enabled)
            self.btn_toggle.setText(
                "取引：稼働中（クリックで停止）" if enabled else "取引：停止中（クリックで開始）"
            )
            self._refresh_status()

            # ループを開始/停止
            if self._main_window is not None and hasattr(self._main_window, "_trade_loop"):
                if enabled:
                    if not self._main_window._trade_loop.start():
                        # 既に実行中の場合はボタンの状態を戻す
                        self.btn_toggle.setChecked(False)
                        trade_state.update(trading_enabled=False)
                        _write_runtime_flags(trading_enabled=False)
                        self.btn_toggle.setText("取引：停止中（クリックで開始）")
                        self._refresh_status()
                else:
                    self._main_window._trade_loop.stop(reason="ui_toggle_off")
        except Exception as e:
            self.btn_toggle.setChecked(not self.btn_toggle.isChecked())
            QMessageBox.critical(self, "Trading switch error", str(e))
            print("[control_tab] toggle failed:", e)

    def _on_thr_changed(self, *_):
        buy = self.sld_buy.value() / 100.0
        sell = self.sld_sell.value() / 100.0
        trade_state.update(threshold_buy=buy, threshold_sell=sell)
        self.lbl_buy.setText(f"買い: {buy:.2f}")
        self.lbl_sell.setText(f"売り: {sell:.2f}")
        self._refresh_status()

    def _on_exit_changed(self, *_):
        trade_state.update(sl_pips=int(self.sp_sl.value()), tp_pips=int(self.sp_tp.value()))
        self._refresh_status()

    def _close_all_mock(self):
        cfg = load_config()
        symbol = cfg.get("runtime", {}).get("symbol", "USDJPY-")
        orderbook().close_all(symbol)

    def _cb_reset(self):
        circuit_breaker.reset()
        circuit_breaker.scan_and_update()
        self._refresh_status()
