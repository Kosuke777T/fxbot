from typing import Optional, Dict, Any, List
from pathlib import Path
import json
from datetime import datetime, timezone
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
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

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from core.metrics import METRICS_JSON, METRICS

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
    """ログイン/ログアウトは接続処理を持たず、シグナルで MainWindow に通知する。"""
    mt5_login_toggle_requested = pyqtSignal()

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
        lay_run = QVBoxLayout()
        # ログイン/ログアウト行（取引ボタン群の上）
        row_login = QHBoxLayout()
        self.login_btn = QPushButton("ログイン")
        self.login_btn.clicked.connect(lambda: self.mt5_login_toggle_requested.emit())
        row_login.addWidget(self.login_btn)
        row_login.addStretch()
        lay_run.addLayout(row_login)
        # 口座ステータス表示（ログイン行の直下、取引ボタン行の直前）
        self.lbl_mt5_stats = QLabel("残高: -- / 有効証拠金: -- / 余剰証拠金: -- / ポジション: --")
        self.lbl_mt5_stats.setStyleSheet(
            "QLabel { background-color: #F0F0F0; border-radius: 2px; padding: 2px 6px; font-size: 20px; }"
        )
        lay_run.addWidget(self.lbl_mt5_stats)
        # 取引ボタン行
        row_trading = QHBoxLayout()
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

        row_trading.addWidget(self.btn_toggle)
        row_trading.addWidget(self.btn_test_entry)
        row_trading.addWidget(self.btn_close_all)
        row_trading.addWidget(self.btn_cb_reset)
        lay_run.addLayout(row_trading)
        box_run.setLayout(lay_run)
        root_layout.addWidget(box_run)

        # しきい値（縦に圧縮）
        box_thr = QGroupBox("エントリーしきい値（確信度）")
        box_thr.setMaximumHeight(80)
        lay_thr = QHBoxLayout()
        lay_thr.setContentsMargins(6, 6, 6, 6)
        self.lbl_buy = QLabel("買い: 0.60")
        self.sld_buy = QSlider(Qt.Orientation.Horizontal)
        self.sld_buy.setRange(50, 80)
        self.sld_buy.setValue(60)
        self.sld_buy.setFixedHeight(18)
        self.sld_buy.valueChanged.connect(self._on_thr_changed)

        self.lbl_sell = QLabel("売り: 0.60")
        self.sld_sell = QSlider(Qt.Orientation.Horizontal)
        self.sld_sell.setRange(50, 80)
        self.sld_sell.setValue(60)
        self.sld_sell.setFixedHeight(18)
        self.sld_sell.valueChanged.connect(self._on_thr_changed)

        lay_thr.addWidget(self.lbl_buy)
        lay_thr.addWidget(self.sld_buy)
        lay_thr.addSpacing(12)
        lay_thr.addWidget(self.lbl_sell)
        lay_thr.addWidget(self.sld_sell)
        box_thr.setLayout(lay_thr)
        root_layout.addWidget(box_thr)

        # 決済（縦に圧縮）
        box_exit = QGroupBox("決済（固定pips）")
        box_exit.setMaximumHeight(70)
        lay_exit = QHBoxLayout()
        lay_exit.setContentsMargins(6, 6, 6, 6)
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

        # 確率履歴グラフ（決済枠の下）
        graph_group = QGroupBox("Probs (p_buy / p_sell / p_skip) — latest 100")
        graph_layout = QVBoxLayout(graph_group)
        self._probs_fig = Figure(figsize=(6, 2.5), tight_layout=True)
        self._probs_ax = self._probs_fig.add_subplot(111)
        self._probs_canvas = FigureCanvas(self._probs_fig)
        self._probs_canvas.setMinimumHeight(180)
        graph_layout.addWidget(self._probs_canvas)
        root_layout.addWidget(graph_group)

        # 状態表示
        self.lbl_status = QLabel("")
        root_layout.addWidget(self.lbl_status)

        # 確率グラフの定期更新（metrics.json を1秒ごとに読む）
        self._probs_timer = QTimer(self)
        self._probs_timer.setInterval(1000)
        self._probs_timer.timeout.connect(self._refresh_probs_graph_from_metrics)
        self._probs_timer.start()
        self._refresh_probs_graph_from_metrics()

        self._sync_from_state()

    def set_mt5_stats_text(self, text: str) -> None:
        """口座ステータス表示ラベルのテキストを更新する（表示器のみ、MT5アクセスはしない）。"""
        self.lbl_mt5_stats.setText(text)

    def set_trading_controls_enabled(self, enabled: bool, *, reason_text: str = "") -> None:
        """
        取引系ボタンの有効/無効を切り替える（GUI内のみのAPI、未ログイン時は無効にし表示と内部状態を一致させる）。
        enabled=False: 取引ボタン・テストエントリーを押せなくし、OFF＋trading_enabled=False に確定する。
        enabled=True: 取引ボタン・テストエントリーを押せるようにする。
        """
        self.btn_toggle.setEnabled(enabled)
        self.btn_test_entry.setEnabled(enabled)
        if not enabled:
            self.btn_toggle.blockSignals(True)
            try:
                self.btn_toggle.setChecked(False)
                self.btn_toggle.setText("取引：停止中（ログインしてください）")
                trade_state.update(trading_enabled=False)
                _write_runtime_flags(trading_enabled=False)
                self._refresh_status()
            finally:
                self.btn_toggle.blockSignals(False)
        else:
            s = trade_state.get_settings()
            self.btn_toggle.blockSignals(True)
            try:
                self.btn_toggle.setChecked(bool(getattr(s, "trading_enabled", False)))
                self.btn_toggle.setText(
                    "取引：稼働中（クリックで停止）" if getattr(s, "trading_enabled", False) else "取引：停止中（クリックで開始）"
                )
            finally:
                self.btn_toggle.blockSignals(False)
            self._refresh_status()

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

            # 自動売買ループ開始/停止の唯一のGUI入口（観測で確定）
            # GUI「取引ON」押下 → _main_window._trade_loop.start() のみ。services 直呼びはしない。
            if self._main_window is not None and hasattr(self._main_window, "_trade_loop"):
                if enabled:
                    if not self._main_window._trade_loop.start():
                        # T-65.1: start() 失敗時は必ず OFF 表記に戻す（blockSignals で再入防止）
                        self.btn_toggle.blockSignals(True)
                        try:
                            self.btn_toggle.setChecked(False)
                            trade_state.update(trading_enabled=False)
                            _write_runtime_flags(trading_enabled=False)
                            self.btn_toggle.setText("取引：停止中（クリックで開始）")
                            self._refresh_status()
                        finally:
                            self.btn_toggle.blockSignals(False)
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

    def _refresh_probs_graph_from_metrics(self) -> None:
        """runtime/metrics.json の probs_history から 3本線 + threshold 水平線を描画。データ無し/キー無しは N/A で落ちない。"""
        kv: Dict[str, Any] = {}
        try:
            with open(METRICS_JSON, "r", encoding="utf-8") as f:
                kv = json.load(f)
        except Exception:
            kv = METRICS.get()
        try:
            hist = kv.get("probs_history")
            if not isinstance(hist, dict):
                self._probs_ax.clear()
                self._probs_ax.text(0.5, 0.5, "N/A", ha="center", va="center", transform=self._probs_ax.transAxes)
                self._probs_canvas.draw_idle()
                return
            p_buy_list: List[float] = list(hist.get("p_buy") or [])
            p_sell_list: List[float] = list(hist.get("p_sell") or [])
            p_skip_list: List[float] = list(hist.get("p_skip") or [])
            threshold_val = float(hist.get("threshold", 0.52))
            n = max(len(p_buy_list), len(p_sell_list), len(p_skip_list), 1)
            if n == 0:
                self._probs_ax.clear()
                self._probs_ax.text(0.5, 0.5, "N/A", ha="center", va="center", transform=self._probs_ax.transAxes)
                self._probs_canvas.draw_idle()
                return
            x = list(range(n))
            self._probs_ax.clear()
            if p_buy_list:
                self._probs_ax.plot(x, p_buy_list, color="green", label="p_buy", linewidth=1)
            if p_sell_list:
                self._probs_ax.plot(x, p_sell_list, color="red", label="p_sell", linewidth=1)
            if p_skip_list:
                self._probs_ax.plot(x, p_skip_list, color="gray", label="p_skip", linewidth=1)
            self._probs_ax.axhline(y=threshold_val, color="blue", linestyle="--", linewidth=1, label="threshold")
            self._probs_ax.set_ylim(-0.05, 1.05)
            self._probs_ax.legend(loc="upper right", fontsize=7)
            self._probs_ax.set_xlabel("tick (latest 100)")
            self._probs_canvas.draw_idle()
        except Exception:
            try:
                self._probs_ax.clear()
                self._probs_ax.text(0.5, 0.5, "N/A", ha="center", va="center", transform=self._probs_ax.transAxes)
                self._probs_canvas.draw_idle()
            except Exception:
                pass

    def _close_all_mock(self):
        cfg = load_config()
        symbol = cfg.get("runtime", {}).get("symbol", "USDJPY-")
        orderbook().close_all(symbol)

    def _cb_reset(self):
        circuit_breaker.reset()
        circuit_breaker.scan_and_update()
        self._refresh_status()
