from __future__ import annotations

from datetime import datetime
from typing import Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as Canvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter, date2num
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from loguru import logger

from app.services.visualization_service import (
    get_default_symbol_timeframe,
    get_recent_lgbm_series,
    get_recent_ohlcv,
    log_viz_info,
)


class VisualizeTab(QWidget):
    """
    可視化タブ（将来シミュレーターの土台）
    - 上段: ローソク足（OHLC）
    - 下段: LightGBM 出力（prob_buy 等）+ threshold + crossing marker
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        self.setLayout(root)

        # ---- controls ----
        box = QGroupBox("Visualize")
        lay = QHBoxLayout(box)

        defaults = get_default_symbol_timeframe()
        default_symbol = str(defaults.get("symbol") or "USDJPY-")
        default_tf = str(defaults.get("timeframe") or "M5")

        lay.addWidget(QLabel("Symbol"))
        self.ed_symbol = QLineEdit(default_symbol)
        self.ed_symbol.setPlaceholderText("例: USDJPY-")
        self.ed_symbol.setMaximumWidth(120)
        lay.addWidget(self.ed_symbol)

        lay.addWidget(QLabel("TF"))
        self.cmb_tf = QtWidgets.QComboBox()
        self.cmb_tf.addItems(["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
        try:
            idx = self.cmb_tf.findText(default_tf.upper())
            if idx >= 0:
                self.cmb_tf.setCurrentIndex(idx)
        except Exception:
            pass
        self.cmb_tf.setMaximumWidth(80)
        lay.addWidget(self.cmb_tf)

        lay.addWidget(QLabel("N"))
        self.spn_n = QSpinBox()
        self.spn_n.setRange(20, 2000)
        self.spn_n.setValue(120)
        self.spn_n.setMaximumWidth(90)
        lay.addWidget(self.spn_n)

        lay.addSpacing(12)

        lay.addWidget(QLabel("Threshold"))
        self.lbl_thr = QLabel("0.60")

        self.sld_thr = QSlider(Qt.Orientation.Horizontal)
        self.sld_thr.setRange(0, 100)
        self.sld_thr.setValue(60)
        self.sld_thr.setMaximumWidth(240)
        self.sld_thr.valueChanged.connect(self._on_threshold_changed)

        lay.addWidget(self.lbl_thr)
        lay.addWidget(self.sld_thr)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        lay.addWidget(self.btn_refresh)

        lay.addStretch(1)
        root.addWidget(box)

        # ---- plot (matplotlib) ----
        self.fig = Figure(figsize=(9, 6))
        self.canvas = Canvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, self)
        root.addWidget(self.toolbar)
        root.addWidget(self.canvas, 1)
        self.canvas.mpl_connect("scroll_event", self._on_mpl_scroll)
        self.canvas.mpl_connect("button_press_event", self._on_mpl_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_mpl_motion)
        self.canvas.mpl_connect("button_release_event", self._on_mpl_release)

        # drag-pan state (refresh() で axes が作り直される前提のため、参照は都度更新する)
        self._ax_price = None
        self._drag_pan_active: bool = False
        self._drag_pan_x0: float | None = None
        self._drag_pan_xlim0: tuple[float, float] | None = None
        self._user_xlim: tuple[float, float] | None = None

        # ---- status ----
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #666; font-size: 9pt;")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        # timer: simple, no threads
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.refresh()

    def _on_mpl_scroll(self, event: object) -> None:
        """
        Ctrl+ホイールのみ、表示本数 N を増減する（ズーム等とは混ぜない）。
        - spn_n を唯一の正とし、setValue() → refresh() で再描画。
        """
        try:
            mods = QtWidgets.QApplication.keyboardModifiers()
            if not (mods & Qt.KeyboardModifier.ControlModifier):
                return
        except Exception:
            return

        try:
            cur = int(self.spn_n.value())
            step = 20
            btn = getattr(event, "button", None)
            if btn == "up":
                new_n = cur + step
            elif btn == "down":
                new_n = cur - step
            else:
                # backend によっては step を持つことがある（正なら増、負なら減）
                s = getattr(event, "step", None)
                try:
                    si = int(s) if s is not None else 0
                except Exception:
                    si = 0
                if si > 0:
                    new_n = cur + step
                elif si < 0:
                    new_n = cur - step
                else:
                    return

            mn = int(self.spn_n.minimum())
            mx = int(self.spn_n.maximum())
            new_n = max(mn, min(mx, int(new_n)))
            if new_n == cur:
                return

            blocker = QtCore.QSignalBlocker(self.spn_n)
            self.spn_n.setValue(int(new_n))
            del blocker
            self.refresh()
        except Exception as e:
            logger.error(f"[viz] scroll handler failed: {e}")

    def _threshold(self) -> float:
        try:
            return float(self.sld_thr.value()) / 100.0
        except Exception:
            return 0.60

    def _on_threshold_changed(self, *_: object) -> None:
        thr = self._threshold()
        try:
            self.lbl_thr.setText(f"{thr:.2f}")
        except Exception:
            pass
        # UI操作で即反映（軽量なので全再描画でOK）
        self.refresh()

    def _get_inputs(self) -> tuple[str, str, int]:
        sym = (self.ed_symbol.text() or "USDJPY-").strip()
        tf = (self.cmb_tf.currentText() or "M5").strip()
        n = int(self.spn_n.value())
        return sym, tf, n

    def refresh(self) -> None:
        # ドラッグ中に timer refresh が走ると xlim が巻き戻って「パンが効かない」に見えるため抑止する
        if bool(getattr(self, "_drag_pan_active", False)):
            return
        sym, tf, n = self._get_inputs()
        thr = self._threshold()

        # services: get data (GUI does not import core)
        ohlc = get_recent_ohlcv(symbol=sym, timeframe=tf, count=n)
        lgbm = get_recent_lgbm_series(symbol=sym, count=n, keys=("prob_buy",))

        try:
            self._render(ohlc=ohlc, lgbm=lgbm, threshold=thr)
        except Exception as e:
            logger.error(f"[viz] render failed: {e}")
            self.lbl_status.setText(f"render failed: {e}")

    def _render(self, *, ohlc: dict, lgbm: dict, threshold: float) -> None:
        self.fig.clear()
        gs = self.fig.add_gridspec(2, 1, height_ratios=[2.2, 1.0], hspace=0.05)
        ax_price = self.fig.add_subplot(gs[0, 0])
        ax_prob = self.fig.add_subplot(gs[1, 0], sharex=ax_price)
        # refresh() 毎に axes が作り直されるため、最新参照を保持
        self._ax_price = ax_price

        # --- upper: candlestick (best-effort) ---
        ohlc_ok = bool(ohlc.get("ok"))
        t = ohlc.get("time") if ohlc_ok else None
        opens = ohlc.get("open") if ohlc_ok else None
        highs = ohlc.get("high") if ohlc_ok else None
        lows = ohlc.get("low") if ohlc_ok else None
        closes = ohlc.get("close") if ohlc_ok else None

        if ohlc_ok and isinstance(t, list) and len(t) >= 2 and isinstance(opens, list) and isinstance(closes, list):
            xs = date2num(t)
            try:
                width = float(xs[1] - xs[0]) * 0.6
            except Exception:
                width = 0.0005

            for i in range(min(len(xs), len(opens), len(closes), len(highs or []), len(lows or []))):
                x = xs[i]
                o = float(opens[i])
                c = float(closes[i])
                h = float(highs[i])
                l = float(lows[i])
                up = c >= o
                col = "#26a69a" if up else "#ef5350"
                # wick
                ax_price.vlines(x, l, h, color=col, linewidth=1.0, alpha=0.9)
                # body
                y0 = min(o, c)
                hh = abs(c - o)
                if hh <= 0:
                    ax_price.hlines(o, x - width / 2, x + width / 2, color=col, linewidth=1.2)
                else:
                    ax_price.add_patch(
                        Rectangle(
                            (x - width / 2, y0),
                            width,
                            hh,
                            facecolor=col,
                            edgecolor=col,
                            alpha=0.85,
                        )
                    )

            ax_price.set_ylabel("Price")
            ax_price.grid(True, alpha=0.25)
        else:
            reason = ohlc.get("reason") if isinstance(ohlc, dict) else "unknown"
            ax_price.text(
                0.02,
                0.9,
                f"OHLC unavailable: {reason}",
                transform=ax_price.transAxes,
                fontsize=9,
                color="#888",
            )
            ax_price.set_ylabel("Price")
            ax_price.grid(True, alpha=0.15)

        # --- lower: prob + threshold + crossing ---
        lgbm_ok = bool(lgbm.get("ok"))
        times = lgbm.get("time") if lgbm_ok else None
        series = lgbm.get("series") if lgbm_ok else None
        y = None
        if isinstance(series, dict):
            y = series.get("prob_buy")

        markers = 0
        if lgbm_ok and isinstance(times, list) and isinstance(y, list) and len(times) >= 2 and len(times) == len(y):
            ax_prob.plot(times, y, label="prob_buy", linewidth=1.4, color="#3366cc")
            ax_prob.axhline(float(threshold), color="#ff9800", linewidth=1.2, linestyle="--", label="threshold")

            # crossing: below -> above
            xs_m: list[datetime] = []
            ys_m: list[float] = []
            for i in range(1, len(y)):
                try:
                    if float(y[i - 1]) < threshold <= float(y[i]):
                        xs_m.append(times[i])
                        ys_m.append(float(y[i]))
                except Exception:
                    continue
            if xs_m:
                ax_prob.scatter(xs_m, ys_m, s=24, marker="^", color="#e91e63", label="cross_up")
                markers = len(xs_m)

            ax_prob.set_ylim(0.0, 1.0)
            ax_prob.set_ylabel("Prob")
            ax_prob.grid(True, alpha=0.25)
            ax_prob.legend(loc="upper left", fontsize=8)
        else:
            reason = lgbm.get("reason") if isinstance(lgbm, dict) else "unknown"
            ax_prob.text(
                0.02,
                0.8,
                f"LGBM series unavailable: {reason}",
                transform=ax_prob.transAxes,
                fontsize=9,
                color="#888",
            )
            ax_prob.set_ylim(0.0, 1.0)
            ax_prob.set_ylabel("Prob")
            ax_prob.grid(True, alpha=0.15)

        # x-axis formatting
        try:
            loc = AutoDateLocator()
            ax_prob.xaxis.set_major_locator(loc)
            ax_prob.xaxis.set_major_formatter(ConciseDateFormatter(loc))
            for label in ax_price.get_xticklabels():
                label.set_visible(False)
            self.fig.autofmt_xdate()
        except Exception:
            pass

        # ユーザーがパンした xlim を、次回 render にも反映（表示ロジックは崩さない）
        try:
            if isinstance(getattr(self, "_user_xlim", None), tuple) and len(self._user_xlim) == 2:
                ax_price.set_xlim(self._user_xlim[0], self._user_xlim[1])
        except Exception:
            pass

        # status + log
        ohlc_n = int(ohlc.get("rows") or 0) if isinstance(ohlc, dict) else 0
        lgbm_keys = list(lgbm.get("keys") or []) if isinstance(lgbm, dict) else []
        src_ohlc = ohlc.get("source") if isinstance(ohlc, dict) else None
        src_lgbm = "decisions_log" if lgbm_ok else None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.lbl_status.setText(
            f"updated={now} | ohlc={ohlc_n} ({src_ohlc}) | lgbm={int(lgbm.get('rows') or 0) if isinstance(lgbm, dict) else 0} ({src_lgbm}) | threshold={threshold:.2f} | markers={markers}"
        )
        log_viz_info(ohlc_n=ohlc_n, lgbm_keys=lgbm_keys, threshold=float(threshold), markers=int(markers))

        try:
            # tight_layout は Canvas/Toolbar 組み合わせによって警告が出ることがあるため、
            # ここでは静的な余白調整に留める（表示のみ・ロジック影響なし）。
            self.fig.subplots_adjust(left=0.06, right=0.985, top=0.97, bottom=0.08, hspace=0.06)
        except Exception:
            pass
        self.canvas.draw_idle()

    def _toolbar_busy(self) -> bool:
        """Toolbar の Zoom/Pan などのモード中は自前ドラッグパンを抑止する（競合回避）。"""
        try:
            return bool(getattr(self.toolbar, "mode", ""))
        except Exception:
            return False

    def _event_in_price_x(self, event: object) -> bool:
        """
        event.inaxes が price と同じx軸グループなら True。
        sharex=ax_price なので、下段でドラッグしても上段xlimを更新すれば追随する。
        """
        try:
            ax_price = getattr(self, "_ax_price", None)
            inaxes = getattr(event, "inaxes", None)
            if ax_price is None or inaxes is None:
                return False
            if inaxes == ax_price:
                return True
            # sharex 判定（Matplotlib API が無い/変わる可能性があるので best-effort）
            try:
                sxa = inaxes.get_shared_x_axes()
                return bool(sxa.joined(inaxes, ax_price))
            except Exception:
                return False
        except Exception:
            return False

    def _on_mpl_press(self, event: object) -> None:
        try:
            if self._toolbar_busy():
                return
            if getattr(event, "button", None) != 1:  # left only
                return
            if not self._event_in_price_x(event):
                return
            x = getattr(event, "xdata", None)
            if x is None:
                return
            ax = getattr(self, "_ax_price", None)
            if ax is None:
                return
            self._drag_pan_active = True
            self._drag_pan_x0 = float(x)
            self._drag_pan_xlim0 = tuple(ax.get_xlim())
        except Exception:
            self._drag_pan_active = False

    def _on_mpl_motion(self, event: object) -> None:
        try:
            if not self._drag_pan_active:
                return
            ax = getattr(self, "_ax_price", None)
            if ax is None:
                return
            x = getattr(event, "xdata", None)
            if x is None or self._drag_pan_x0 is None or self._drag_pan_xlim0 is None:
                return
            dx = float(x) - float(self._drag_pan_x0)
            x0, x1 = self._drag_pan_xlim0
            new_xlim = (float(x0) - dx, float(x1) - dx)
            ax.set_xlim(new_xlim[0], new_xlim[1])
            self.canvas.draw_idle()
        except Exception:
            pass

    def _on_mpl_release(self, event: object) -> None:
        try:
            if self._drag_pan_active:
                ax = getattr(self, "_ax_price", None)
                if ax is not None:
                    self._user_xlim = tuple(ax.get_xlim())
        except Exception:
            pass
        self._drag_pan_active = False
        self._drag_pan_x0 = None
        self._drag_pan_xlim0 = None
