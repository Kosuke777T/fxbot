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
from app.services.ohlcv_update_service import ensure_lgbm_proba_uptodate


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

        lay.addSpacing(12)

        # デバッグ切替：prob_buy/prob_sell を2本表示
        self.chk_debug_both = QtWidgets.QCheckBox("Debug: show buy/sell")
        self.chk_debug_both.setChecked(False)
        self.chk_debug_both.stateChanged.connect(self.refresh)
        lay.addWidget(self.chk_debug_both)

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
        self._ax_prob = None
        self._drag_pan_active: bool = False
        self._drag_pan_x0: float | None = None
        self._drag_pan_xlim0: tuple[float, float] | None = None
        self._user_xlim: tuple[float, float] | None = None
        # 軽量描画モード用
        self._candle_bodies: list = []
        self._candle_wicks: list = []
        self._drag_light_mode: bool = False
        # OHLC cache (infinite scroll: prepend older bars on pan-left)
        self._ohlc_cache: dict | None = None
        self._ohlc_cache_key: tuple[str, str] | None = None
        self._ohlc_cache_n: int | None = None
        # throttle state for smooth pan
        self._drag_pan_last_x: float | None = None
        self._pan_tick_pending: bool = False
        self._pan_interval_ms: int = 16  # 60fps目安（重ければ33）

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
            # 現在の表示位置を維持したまま N を変更する（中心固定で表示幅をスケール）
            try:
                ax = getattr(self, "_ax_price", None)
                if ax is not None:
                    cur_left, cur_right = ax.get_xlim()
                elif isinstance(getattr(self, "_user_xlim", None), tuple) and len(self._user_xlim) == 2:
                    cur_left, cur_right = self._user_xlim
                else:
                    cur_left, cur_right = 0.0, 0.0

                width = float(cur_right) - float(cur_left)
                if width > 0 and cur > 0:
                    center = (float(cur_left) + float(cur_right)) * 0.5
                    scale = float(new_n) / float(cur)
                    new_width = width * scale
                    self._user_xlim = (center - new_width * 0.5, center + new_width * 0.5)
                else:
                    self._user_xlim = None
            except Exception:
                self._user_xlim = None

            # cache が十分にあるなら維持（N変更で過去表示が飛ぶのを避ける）
            try:
                sym = (self.ed_symbol.text() or "USDJPY-").strip()
                tf = (self.cmb_tf.currentText() or "M5").strip()
                key = (str(sym), str(tf))
                cache = getattr(self, "_ohlc_cache", None)
                if (
                    isinstance(cache, dict)
                    and bool(cache.get("ok"))
                    and getattr(self, "_ohlc_cache_key", None) == key
                    and int(cache.get("rows") or 0) >= int(new_n)
                ):
                    self._ohlc_cache_n = int(new_n)
                else:
                    # 不足する場合は refresh() 側で取り直す
                    self._ohlc_cache = None
            except Exception:
                pass
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
        # ohlc: cache を優先（pan-left 時は prepend して伸ばす）
        key = (str(sym), str(tf))
        need_fetch = (
            self._ohlc_cache is None
            or self._ohlc_cache_key != key
            or int(self._ohlc_cache_n or 0) != int(n)
            or (not bool(self._ohlc_cache.get("ok")) if isinstance(self._ohlc_cache, dict) else True)
        )
        if need_fetch:
            ohlc = get_recent_ohlcv(symbol=sym, timeframe=tf, count=n)
            self._ohlc_cache = ohlc if isinstance(ohlc, dict) else None
            self._ohlc_cache_key = key
            self._ohlc_cache_n = int(n)
        else:
            ohlc = self._ohlc_cache or {"ok": False, "reason": "ohlc_cache_missing"}

        # OHLCの時間範囲を確定してからprobaを埋める
        t_min: datetime | None = None
        t_max: datetime | None = None
        if isinstance(ohlc, dict) and ohlc.get("ok") and isinstance(ohlc.get("time"), list):
            times = ohlc["time"]
            if times:
                t_min = min(times)
                t_max = max(times)

        # proba CSVの自動更新（M5のみ、表示範囲を埋める）
        if tf == "M5" and t_min is not None and t_max is not None:
            try:
                ensure_lgbm_proba_uptodate(symbol=sym, timeframe=tf, start_time=t_min, end_time=t_max)
            except Exception as e:
                logger.warning(f"[viz] ensure_lgbm_proba_uptodate failed: {e}")

        # 同じ範囲でprobaを取得（prob_sellも取得可能にする）
        lgbm = get_recent_lgbm_series(
            symbol=sym, count=n, keys=("prob_buy", "prob_sell"), start_time=t_min, end_time=t_max
        )

        # 観測用（1回/refresh 程度に抑える）
        try:
            keys = list(lgbm.get("keys") or []) if isinstance(lgbm, dict) else []
            series = lgbm.get("series") if isinstance(lgbm, dict) else None
            prob = (series.get("prob_buy") if isinstance(series, dict) else None) if isinstance(lgbm, dict) else None
            len_prob = int(len(prob)) if isinstance(prob, list) else 0
            logger.info("[viz] lgbm keys={} len_prob={}", keys, len_prob)
        except Exception:
            pass

        try:
            self._render(ohlc=ohlc, lgbm=lgbm, threshold=thr)
        except Exception as e:
            logger.error(f"[viz] render failed: {e}")
            self.lbl_status.setText(f"render failed: {e}")

    def _prepend_older_ohlc_if_needed(self) -> None:
        """
        user_xlim の左端が、保持OHLCの最古より左に出た場合のみ、過去分を services から取得して prepend する。
        - release 時だけ呼ぶ（motion中は取得しない）
        """
        try:
            if not (isinstance(getattr(self, "_user_xlim", None), tuple) and len(self._user_xlim) == 2):
                return
            left_xlim = float(self._user_xlim[0])  # float(date2num)

            cache = getattr(self, "_ohlc_cache", None)
            if not (isinstance(cache, dict) and bool(cache.get("ok"))):
                return
            times = cache.get("time")
            if not (isinstance(times, list) and len(times) >= 2 and isinstance(times[0], datetime)):
                return

            xs = date2num(times)
            try:
                min_x = float(xs[0])
                bar_w = float(xs[1] - xs[0])
            except Exception:
                return
            if not (bar_w > 0):
                return

            if left_xlim >= min_x:
                return

            sym, tf, _n = self._get_inputs()
            min_time = times[0]
            # どれだけ左に出たかから必要本数を見積もる（取りすぎ防止）
            margin = 20
            bars_needed = int((min_x - left_xlim) / bar_w) + margin
            bars_needed = max(10, min(int(bars_needed), 2000))

            older = get_recent_ohlcv(symbol=sym, timeframe=tf, count=bars_needed, until=min_time)
            if not (isinstance(older, dict) and bool(older.get("ok"))):
                return

            ot = older.get("time")
            if not (isinstance(ot, list) and ot):
                return

            # 重複（境界）を避けて strictly older のみ prepend
            keep_idx = [i for i, t in enumerate(ot) if isinstance(t, datetime) and t < min_time]
            if not keep_idx:
                return

            def pick(col: str) -> list[float]:
                arr = older.get(col)
                if not isinstance(arr, list):
                    return []
                out: list[float] = []
                for i in keep_idx:
                    try:
                        out.append(float(arr[i]))
                    except Exception:
                        out.append(0.0)
                return out

            older_times = [ot[i] for i in keep_idx]
            new_cache = dict(cache)
            new_cache["time"] = older_times + list(times)
            for col in ["open", "high", "low", "close"]:
                base = cache.get(col)
                base_list = list(base) if isinstance(base, list) else []
                new_cache[col] = pick(col) + base_list
            try:
                new_cache["rows"] = int(len(new_cache["time"]))
            except Exception:
                pass

            self._ohlc_cache = new_cache
            logger.info(
                "[viz] ohlc prepend: added={} total_rows={} until={}",
                len(older_times),
                int(new_cache.get("rows") or 0),
                str(min_time),
            )
        except Exception as e:
            logger.error(f"[viz] ohlc prepend failed: {e}")

    def _render(self, *, ohlc: dict, lgbm: dict, threshold: float) -> None:
        self.fig.clear()
        self._candle_bodies = []  # 軽量モード用にクリア
        self._candle_wicks = []
        gs = self.fig.add_gridspec(2, 1, height_ratios=[2.2, 1.0], hspace=0.05)
        ax_price = self.fig.add_subplot(gs[0, 0])
        ax_prob = self.fig.add_subplot(gs[1, 0], sharex=ax_price)
        # refresh() 毎に axes が作り直されるため、最新参照を保持
        self._ax_price = ax_price
        self._ax_prob = ax_prob

        # --- upper: candlestick (best-effort) ---
        ohlc_ok = bool(ohlc.get("ok"))
        t = ohlc.get("time") if ohlc_ok else None
        opens = ohlc.get("open") if ohlc_ok else None
        highs = ohlc.get("high") if ohlc_ok else None
        lows = ohlc.get("low") if ohlc_ok else None
        closes = ohlc.get("close") if ohlc_ok else None

        xs_ohlc: list[float] = []
        if ohlc_ok and isinstance(t, list) and len(t) >= 2 and isinstance(opens, list) and isinstance(closes, list):
            xs_ohlc = list(date2num(t))
            try:
                width = float(xs_ohlc[1] - xs_ohlc[0]) * 0.6
            except Exception:
                width = 0.0005

            for i in range(min(len(xs_ohlc), len(opens), len(closes), len(highs or []), len(lows or []))):
                x = xs_ohlc[i]
                o = float(opens[i])
                c = float(closes[i])
                h = float(highs[i])
                l = float(lows[i])
                up = c >= o
                col = "#26a69a" if up else "#ef5350"
                # wick
                lc = ax_price.vlines(x, l, h, color=col, linewidth=1.0, alpha=0.9)
                self._candle_wicks.append(lc)
                # body
                y0 = min(o, c)
                hh = abs(c - o)
                if hh <= 0:
                    ax_price.hlines(o, x - width / 2, x + width / 2, color=col, linewidth=1.2)
                else:
                    rect = Rectangle(
                        (x - width / 2, y0),
                        width,
                        hh,
                        facecolor=col,
                        edgecolor=col,
                        alpha=0.85,
                    )
                    ax_price.add_patch(rect)
                    self._candle_bodies.append(rect)

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
        series = lgbm.get("series") if lgbm_ok else None
        prob_buy_raw = None
        prob_sell_raw = None
        if isinstance(series, dict):
            prob_buy_raw = series.get("prob_buy")
            prob_sell_raw = series.get("prob_sell")

        # デバッグ切替フラグ
        show_both = bool(getattr(self, "chk_debug_both", None) and self.chk_debug_both.isChecked())

        # threshold 線は diffモードでは非表示（混乱防止）
        if not show_both:
            # diffモードでは threshold 線を出さない
            pass
        else:
            # デバッグモード（both表示）では threshold 線を表示
            ax_prob.axhline(float(threshold), color="#ff9800", linewidth=1.2, linestyle="--", label="threshold")

        markers = 0
        # lgbmのtimeを使用（OHLCのxsと同長である必要はない）
        lgbm_times = lgbm.get("time") if lgbm_ok and isinstance(lgbm.get("time"), list) else None
        xs_lgbm: list[float] = []
        prob_buy: list[float] = []
        prob_sell: list[float] = []
        if lgbm_ok and lgbm_times:
            try:
                xs_lgbm = [date2num(ts) for ts in lgbm_times]
                if isinstance(prob_buy_raw, list):
                    prob_buy = [float(v) for v in prob_buy_raw]
                if isinstance(prob_sell_raw, list):
                    prob_sell = [float(v) for v in prob_sell_raw]
            except Exception:
                xs_lgbm = []
                prob_buy = []
                prob_sell = []

        # 統計計算（prob_buy/prob_sell/diffが確定した後）
        mean_buy = None
        mean_sell = None
        mean_diff = None
        mean_abs_diff = None
        pct_pos = None
        pct_neg = None
        
        if prob_buy and len(prob_buy) > 0:
            mean_buy = sum(prob_buy) / len(prob_buy)
        if prob_sell and len(prob_sell) > 0:
            mean_sell = sum(prob_sell) / len(prob_sell)
        
        # diff が計算できる場合（両方が揃っている場合）
        diff: list[float] = []
        if prob_buy and prob_sell and len(prob_buy) == len(prob_sell) and len(prob_buy) > 0:
            diff = [float(prob_buy[i] - prob_sell[i]) for i in range(len(prob_buy))]
            if len(diff) > 0:
                mean_diff = sum(diff) / len(diff)
                mean_abs_diff = sum(abs(d) for d in diff) / len(diff)
                pos_count = sum(1 for d in diff if d > 0)
                neg_count = sum(1 for d in diff if d < 0)
                pct_pos = pos_count / len(diff)
                pct_neg = neg_count / len(diff)
        
        # 統計表示文字列を生成（短縮版）
        def fmt_val(v: float | None) -> str:
            if v is None:
                return "NA"
            return f"{v:.3f}"
        
        stats_text = (
            f"buy={fmt_val(mean_buy)}  sell={fmt_val(mean_sell)}  "
            f"diff={fmt_val(mean_diff)}  |diff|={fmt_val(mean_abs_diff)}  "
            f"+={fmt_val(pct_pos)}  -={fmt_val(pct_neg)}"
        )
        
        # 統計表示（上段のタイトル領域へ移動：上下グラフの干渉を避ける）
        try:
            ax_price.set_title(stats_text, loc="left", fontsize=9, pad=8)
        except Exception:
            pass

        if show_both:
            # デバッグモード：prob_buy と prob_sell を2本表示
            if xs_lgbm and prob_buy and len(xs_lgbm) == len(prob_buy):
                ax_prob.plot(xs_lgbm, prob_buy, color="#2196f3", linewidth=1.5, label="prob_buy", alpha=0.8)
            if xs_lgbm and prob_sell and len(xs_lgbm) == len(prob_sell):
                ax_prob.plot(xs_lgbm, prob_sell, color="#f44336", linewidth=1.5, label="prob_sell", alpha=0.8)
            ax_prob.set_ylabel("Prob")
            ax_prob.set_ylim(0.0, 1.0)
            ax_prob.grid(True, alpha=0.25)
            if not prob_buy and not prob_sell:
                ax_prob.text(
                    0.5,
                    0.5,
                    "prob_buy/prob_sell unavailable",
                    transform=ax_prob.transAxes,
                    ha="center",
                    va="center",
                )
        else:
            # デフォルトモード：diff = prob_buy - prob_sell を1本表示
            if xs_lgbm and diff and len(xs_lgbm) == len(diff):
                # crossing: below -> above（diffで判定、threshold=0.0を基準）
                for i in range(1, len(diff)):
                    if diff[i - 1] < 0.0 and diff[i] >= 0.0:
                        ax_prob.plot(
                            xs_lgbm[i],
                            0.0,
                            marker="^",
                            markersize=8,
                            color="#4caf50",
                            label="cross_up" if i == 1 else "",
                        )
                        markers += 1
                    elif diff[i - 1] > 0.0 and diff[i] <= 0.0:
                        ax_prob.plot(
                            xs_lgbm[i],
                            0.0,
                            marker="v",
                            markersize=8,
                            color="#f44336",
                            label="cross_down" if i == 1 else "",
                        )
                        markers += 1
                ax_prob.plot(xs_lgbm, diff, color="#2196f3", linewidth=1.5, label="diff (buy-sell)", alpha=0.8)
                ax_prob.axhline(0.0, color="#888", linewidth=0.8, linestyle=":", alpha=0.5)
                ax_prob.set_ylabel("Diff (buy-sell)")
                ax_prob.set_ylim(-1.0, 1.0)
                ax_prob.grid(True, alpha=0.25)
            elif xs_lgbm and prob_buy and len(xs_lgbm) == len(prob_buy):
                # prob_sell が無い場合は注記のみ
                logger.warning("[viz] prob_sell missing, diff display suppressed")
                ax_prob.text(
                    0.5,
                    0.5,
                    "prob_sell missing (diff unavailable)",
                    transform=ax_prob.transAxes,
                    ha="center",
                    va="center",
                    fontsize=9,
                    color="#888",
                )
                ax_prob.set_ylabel("Diff (buy-sell)")
                ax_prob.set_ylim(-1.0, 1.0)
                ax_prob.grid(True, alpha=0.15)
            else:
                if lgbm_ok:
                    ax_prob.text(
                        0.5,
                        0.5,
                        f"prob_unavailable (len_xs={len(xs_lgbm)} len_buy={len(prob_buy)} len_sell={len(prob_sell)})",
                        transform=ax_prob.transAxes,
                        ha="center",
                        va="center",
                    )
                else:
                    reason = lgbm.get("reason") if isinstance(lgbm, dict) else "unknown"
                    ax_prob.text(
                        0.5,
                        0.5,
                        f"lgbm_unavailable: {reason}",
                        transform=ax_prob.transAxes,
                        ha="center",
                        va="center",
                    )
                ax_prob.set_ylim(-1.0, 1.0)
                ax_prob.set_ylabel("Diff (buy-sell)")
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
            self.fig.subplots_adjust(left=0.06, right=0.985, top=0.93, bottom=0.08, hspace=0.06)
        except Exception:
            pass
        self.canvas.draw_idle()

    def _toolbar_busy(self) -> bool:
        """Toolbar の Zoom/Pan などのモード中は自前ドラッグパンを抑止する（競合回避）。"""
        try:
            return bool(getattr(self.toolbar, "mode", ""))
        except Exception:
            return False

    def _x_to_num(self, x: object) -> float | None:
        """xdata を float（date2num 正規化済み）に変換。変換不可なら None。"""
        if x is None:
            return None
        try:
            # datetime 系なら date2num
            if isinstance(x, datetime):
                return float(date2num(x))
            # numpy datetime64 などは date2num が対応
            if hasattr(x, "dtype") and "datetime" in str(getattr(x, "dtype", "")):
                return float(date2num(x))
            # 通常の数値
            return float(x)
        except Exception:
            return None

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
            raw_x = getattr(event, "xdata", None)
            x = self._x_to_num(raw_x)
            if x is None:
                logger.info(f"[viz] drag start skipped: xdata={raw_x!r} not convertible to float")
                return
            ax = getattr(self, "_ax_price", None)
            if ax is None:
                return
            self._drag_pan_active = True
            self._drag_pan_x0 = x
            self._drag_pan_xlim0 = tuple(ax.get_xlim())
            # 軽量モードON: body+wick非表示
            self._drag_light_mode = True
            for r in self._candle_bodies:
                r.set_visible(False)
            for w in self._candle_wicks:
                w.set_visible(False)
            self.canvas.draw_idle()
        except Exception:
            self._drag_pan_active = False

    def _on_mpl_motion(self, event: object) -> None:
        # ドラッグ中は最軽量化のため何もしない（release時にxdataで確定する）
        if self._drag_pan_active:
            return

    def _on_mpl_release(self, event: object) -> None:
        do_refresh = False
        try:
            if self._drag_pan_active:
                # release時点のxdataを拾って最後の位置を確定（motionは無処理）
                raw_x = getattr(event, "xdata", None)
                x = self._x_to_num(raw_x)
                if x is not None:
                    self._drag_pan_last_x = x

                # xlim確定（light_mode中はdrawしない）
                self._apply_pan_tick()

                ax = getattr(self, "_ax_price", None)
                if ax is not None:
                    self._user_xlim = tuple(ax.get_xlim())

                # 軽量モードOFF → フル描画に戻す（drag解除後に実施）
                self._drag_light_mode = False
                do_refresh = True
        except Exception as e:
            logger.error(f"[viz] drag release failed: {e}")
        self._drag_pan_active = False
        self._drag_pan_x0 = None
        self._drag_pan_xlim0 = None
        self._drag_pan_last_x = None
        if do_refresh:
            # 左端が足りなければ過去OHLCを prepend（release時のみ）
            self._prepend_older_ohlc_if_needed()
            self.refresh()

    def _schedule_pan_tick(self) -> None:
        try:
            if self._pan_tick_pending:
                return
            self._pan_tick_pending = True
            QtCore.QTimer.singleShot(int(self._pan_interval_ms), self._apply_pan_tick)
        except Exception as e:
            self._pan_tick_pending = False
            logger.error(f'[viz] pan schedule failed: {e}')

    def _apply_pan_tick(self) -> None:
        try:
            self._pan_tick_pending = False
            if not self._drag_pan_active:
                return
            ax = getattr(self, '_ax_price', None)
            if ax is None or self._drag_pan_x0 is None or self._drag_pan_xlim0 is None:
                return
            x = self._drag_pan_last_x
            if x is None:
                return
            dx = float(x) - float(self._drag_pan_x0)
            x0, x1 = self._drag_pan_xlim0
            ax.set_xlim(float(x0) - dx, float(x1) - dx)
            # 軽量モード中は描画しない（release後のrefreshでフル描画する）
            if not bool(getattr(self, "_drag_light_mode", False)):
                self.canvas.draw_idle()
        except Exception as e:
            logger.error(f'[viz] pan apply failed: {e}')
