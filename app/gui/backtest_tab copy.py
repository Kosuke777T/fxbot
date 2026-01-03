# app/gui/backtest_tab.py
from __future__ import annotations

from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QToolTip
import json
import pathlib
import sys
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import numpy as np
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt, QProcess, QTimer
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as Canvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as Toolbar
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, AutoLocator
from matplotlib.widgets import SpanSelector
import matplotlib.gridspec as gridspec
from pandas.api.types import is_datetime64_any_dtype
from app.services.data_guard import ensure_data
from app.services.profiles_store import load_profiles
from functools import partial
from datetime import datetime, timedelta
from tools.backtest_run import compute_monthly_returns

def _thousands(x, pos):
    try:
        return f"{int(x):,}"
    except Exception:
        return str(x)

def _find_trades_csv(equity_csv: Path):
    """equity_curve.csv と同じフォルダにある trades*.csv を探す（優先: test -> train -> trades）"""
    cand = [
        equity_csv.with_name("trades_test.csv"),
        equity_csv.with_name("trades_train.csv"),
        equity_csv.with_name("trades.csv"),
    ]
    for c in cand:
        if c.exists():
            return c
    return None

def plot_equity_with_markers_to_figure(fig: Figure, csv_path: str, note: str = "", bands=None):
    """equity_curve.csv を描画。signal変化点でマーク。変化が無ければ trades*.csv の entry_time でマーク。"""
    p = Path(csv_path)
    try:
        df = pd.read_csv(p)
    except Exception as e:
        print(f"[gui] plot error: failed to read {csv_path}: {e}")
        return

    if "equity" not in df.columns:
        print(f"[gui] plot error: CSVに 'equity' 列がありません。columns={df.columns.tolist()}")
        return

    # time列の正規化
    if "time" in df.columns and not is_datetime64_any_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
    elif "timestamp" in df.columns:
        df["time"] = pd.to_datetime(df["timestamp"], errors="coerce")
    else:
        df["time"] = pd.RangeIndex(0, len(df))

    # figをクリアしてsubplotを作成
    fig.clear()
    ax = fig.add_subplot(111)

    # equity曲線を描画
    ax.plot(df["time"], df["equity"], label="Equity", linewidth=1.5)

    # keep x-limits from equity plot (do not expand by out-of-range bands)

    xlim_before_bands = ax.get_xlim()

    # --- background action bands (draw-only; source=KPIService payload["bands"]) ---
    BAND_KIND_COLOR = {
        "HOLD":    "tab:blue",
        "BLOCKED": "tab:red",
    }
    BAND_ALPHA = 0.12

    _bands = bands or []
    band_spans = []
    for band in _bands:
        if not isinstance(band, dict):
            continue
        kind = (band.get("kind") or "").strip().upper()
        start = band.get("start")
        end = band.get("end")
        if start is None or end is None:
            continue
        try:
            start_dt = pd.to_datetime(start, errors="coerce")
            end_dt = pd.to_datetime(end, errors="coerce")
            if pd.isna(start_dt) or pd.isna(end_dt):
                continue
            color = BAND_KIND_COLOR.get(kind, "0.5")
            span = ax.axvspan(start_dt, end_dt, facecolor=color, alpha=BAND_ALPHA)
            try:
                from matplotlib.dates import date2num
                span._band_x0 = float(date2num(start_dt))
                span._band_x1 = float(date2num(end_dt))
            except Exception:
                pass
            # span に reason を紐づけておく（tooltipは呼び出し側で）
            try:
                span._band_reason = (band.get("reason") or "").strip()
            except Exception:
                pass
            band_spans.append(span)
        except Exception:
            continue

    # ax に一時属性として保存（呼び出し側で参照可）
    # restore x-limits (prevent bands from expanding axis)
    try:
        ax.set_xlim(xlim_before_bands)
    except Exception:
        pass
    try:
        ax._band_spans = band_spans
    except Exception:
        pass

    # レイアウト設定
    ax.set_xlabel("Time")
    ax.set_ylabel("Equity")
    if note:
        ax.set_title(f"Equity Curve: {note}")
    ax.legend()
    ax.grid(True, alpha=0.3)
    try:
        fig.tight_layout()
    except Exception:
        pass

# ------------------ 描画・メトリクス ------------------
class BacktestTab(QtWidgets.QWidget):
    def __init__(self, parent=None, kpi_service=None, profile_name="michibiki_std"):
        super().__init__(parent)
        # injected: accept KPIService from caller (main)
        if kpi_service is None:
            from pathlib import Path
            from app.services.kpi_service import KPIService
            kpi_service = KPIService(base_dir=Path('.'))
        self.kpi_service = kpi_service
        self._profile_name = profile_name


        # ---- UI scaffold (was missing; prevents blank tab) ----
        self._pop = None
        self._last_plot_kind = None
        self._last_plot_data = None
        self._last_plot_note = ""
        self._current_equity_df = None
        self._current_price_df = None
        self._band_tooltip_hooked = False
        self._last_band_reason = None

        # top layout
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # controls row (range jump)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        root.addLayout(row)

        row.addWidget(QtWidgets.QLabel("表示期間:"))
        btn_all = QtWidgets.QPushButton("ALL")
        btn_1w  = QtWidgets.QPushButton("1W")
        btn_1m  = QtWidgets.QPushButton("1M")
        for b in (btn_all, btn_1w, btn_1m):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(btn_all)
        row.addWidget(btn_1w)
        row.addWidget(btn_1m)
        row.addStretch(1)

        # timeframe combo (used by _on_range_jump fallback)
        self.tf_combo = QtWidgets.QComboBox()
        self.tf_combo.addItems(["M1","M5","M15","M30","H1","H4","D1"])
        self.tf_combo.setCurrentText("M5")
        row.addWidget(QtWidgets.QLabel("TF:"))
        row.addWidget(self.tf_combo)

        # date edits (used by _on_range_jump ALL sync)
        self.start_edit = QtWidgets.QDateEdit()
        self.start_edit.setCalendarPopup(True)
        self.end_edit = QtWidgets.QDateEdit()
        self.end_edit.setCalendarPopup(True)
        row.addWidget(QtWidgets.QLabel("開始:"))
        row.addWidget(self.start_edit)
        row.addWidget(QtWidgets.QLabel("終了:"))
        row.addWidget(self.end_edit)


        # action controls (run / load / WFO)
        act = QtWidgets.QHBoxLayout()
        act.setSpacing(6)
        root.addLayout(act)

        self.btn_run_bt = QtWidgets.QPushButton("Backtest実行")
        self.btn_load_latest = QtWidgets.QPushButton("最新結果ロード")
        self.btn_pick_result_dir = QtWidgets.QPushButton("結果フォルダ選択")
        self.btn_load_equity_csv = QtWidgets.QPushButton("equity_curve.csv 読込")
        self.btn_load_candles_csv = QtWidgets.QPushButton("ローソク足CSV 読込")
        self.chk_wfo = QtWidgets.QCheckBox("WFO")
        self.chk_wfo.setToolTip("ON: metrics.json が train/test の場合、test を優先して表示（対応している場合）")

        for b in (self.btn_run_bt, self.btn_load_latest, self.btn_pick_result_dir, self.btn_load_equity_csv, self.btn_load_candles_csv):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        act.addWidget(self.btn_run_bt)
        act.addWidget(self.btn_load_latest)
        act.addWidget(self.btn_pick_result_dir)
        act.addWidget(self.btn_load_equity_csv)
        act.addWidget(self.btn_load_candles_csv)
        act.addWidget(self.chk_wfo)
        act.addStretch(1)

        # selected backtest directory (optional)
        self._bt_dir = None

        self._bt_proc = None  # QProcess for backtest execution

        # wire actions (safe: methods added below)
        self.btn_pick_result_dir.clicked.connect(self._pick_backtest_dir)
        self.btn_load_equity_csv.clicked.connect(self._pick_equity_csv)
        self.btn_load_candles_csv.clicked.connect(self._pick_candles_csv)
        self.btn_load_latest.clicked.connect(self._load_latest_backtest_result)
        self.btn_run_bt.clicked.connect(self._run_backtest_clicked)

        # meta labels
        self.label_meta = QtWidgets.QLabel("Backtest: ready")
        self.label_meta.setWordWrap(True)
        root.addWidget(self.label_meta)

        self.output_status_label = QtWidgets.QLabel("")
        self.output_status_label.setWordWrap(True)
        self.output_status_label.hide()
        root.addWidget(self.output_status_label)

        # plot area (matplotlib)
        self.fig = Figure(figsize=(9, 4))
        self.canvas = Canvas(self.fig)
        self.toolbar = Toolbar(self.canvas, self)
        root.addWidget(self.toolbar)
        root.addWidget(self.canvas, 1)

        # bottom split: metrics table + progress log
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(8)
        root.addLayout(bottom)

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["key", "value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumWidth(360)
        bottom.addWidget(self.table, 0)

        self.progress = QtWidgets.QPlainTextEdit()
        self.progress.setReadOnly(True)
        self.progress.setPlaceholderText("ログ / デバッグ出力")
        bottom.addWidget(self.progress, 1)

        # wire buttons
        btn_all.clicked.connect(lambda: self._on_range_jump("ALL"))
        btn_1w.clicked.connect(lambda: self._on_range_jump("1W"))
        btn_1m.clicked.connect(lambda: self._on_range_jump("1M"))

        self._append_progress("[gui] BacktestTab UI initialized")


    def _append_progress(self, msg: str) -> None:

        try:

            w = getattr(self, "progress", None)

            if w is not None:

                w.appendPlainText(str(msg))

            else:

                print(str(msg))

        except Exception:

            try:

                print(str(msg))

            except Exception:

                pass


    # ------------------ UI actions (helpers) ------------------
    def _pick_backtest_dir(self) -> None:
        try:
            base = Path('logs/backtest')
            start = str(base.resolve()) if base.exists() else str(Path('.').resolve())
            d = QtWidgets.QFileDialog.getExistingDirectory(self, 'Backtest結果フォルダを選択', start)
            if not d:
                return
            bt_dir = Path(d)
            self._bt_dir = bt_dir
            self._append_progress(f'[gui] selected bt_dir={bt_dir}')
            self._load_from_bt_dir(bt_dir)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, 'フォルダ選択エラー', str(e))
            self._append_progress(f'[gui] pick dir error: {e}')

    def _pick_equity_csv(self) -> None:
        try:
            start = str(Path('logs').resolve()) if Path('logs').exists() else str(Path('.').resolve())
            fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'equity_curve.csv を選択', start, 'CSV (*.csv)')
            if not fn:
                return
            csvp = Path(fn)
            self._append_progress(f'[gui] selected equity_csv={csvp}')
            bands = self._try_load_bands_for_equity(csvp)
            self._load_plot(csvp, bands=bands)
            mp = csvp.with_name('metrics.json')
            if mp.exists():
                self._load_metrics(mp)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, 'CSV読込エラー', str(e))
            self._append_progress(f'[gui] pick equity csv error: {e}')

    def _pick_candles_csv(self) -> None:
        try:
            start = str(Path('logs').resolve()) if Path('logs').exists() else str(Path('.').resolve())
            fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'ローソク足CSV（OHLCV）を選択', start, 'CSV (*.csv)')
            if not fn:
                return
            csvp = Path(fn)
            self._append_progress(f'[gui] selected candles_csv={csvp}')
            self._load_plot(csvp)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, 'CSV読込エラー', str(e))
            self._append_progress(f'[gui] pick candles csv error: {e}')

    def _load_latest_backtest_result(self) -> None:
        try:
            base = Path('logs/backtest')
            if not base.exists():
                raise RuntimeError('logs/backtest が見つかりません。')

            # まず metrics.json の最新を探す（無ければ equity_curve.csv の最新）
            metrics = sorted(base.rglob('metrics.json'), key=lambda x: x.stat().st_mtime, reverse=True)
            if metrics:
                bt_dir = metrics[0].parent
            else:
                eqs = sorted(base.rglob('equity_curve.csv'), key=lambda x: x.stat().st_mtime, reverse=True)
                if not eqs:
                    raise RuntimeError('metrics.json / equity_curve.csv が見つかりません。')
                bt_dir = eqs[0].parent

            self._bt_dir = bt_dir
            self._append_progress(f'[gui] latest bt_dir={bt_dir}')
            self._load_from_bt_dir(bt_dir)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, '最新結果ロード失敗', str(e))
            self._append_progress(f'[gui] load latest error: {e}')

    def _load_from_bt_dir(self, bt_dir: Path) -> None:
        try:
            bt_dir = Path(bt_dir)

            # 代表ファイルを探す（直下優先、無ければサブ）
            eq = bt_dir / 'equity_curve.csv'
            if not eq.exists():
                c = list(bt_dir.rglob('equity_curve.csv'))
                eq = c[0] if c else eq

            mp = bt_dir / 'metrics.json'
            if not mp.exists():
                c = list(bt_dir.rglob('metrics.json'))
                mp = c[0] if c else mp

            bands = None
            if eq.exists():
                bands = self._try_load_bands_for_equity(eq)
                self._load_plot(eq, bands=bands)
            else:
                self._append_progress(f'[gui] equity_curve.csv not found under {bt_dir}')

            if mp.exists():
                self._load_metrics(mp)

            # 日付ピッカーに範囲を反映（equity が読めた場合だけ）
            try:
                df = getattr(self, '_current_equity_df', None)
                if df is not None and 'time' in df.columns:
                    t = pd.to_datetime(df['time'], errors='coerce').dropna()
                    if len(t) >= 2:
                        first, last = t.iloc[0], t.iloc[-1]
                        self.start_edit.setDate(QtCore.QDate(first.year, first.month, first.day))
                        self.end_edit.setDate(QtCore.QDate(last.year, last.month, last.day))
            except Exception:
                pass

        except Exception as e:
            QtWidgets.QMessageBox.warning(self, '結果読込エラー', str(e))
            self._append_progress(f'[gui] load_from_bt_dir error: {e}')

    def _run_backtest_clicked(self) -> None:
        """
        Backtest実行:
        - 既存の backtest 実行スクリプト（tools.backtest_run）を QProcess で起動する。
        - 終了後に最新結果を自動ロードして表示する。
        """
        try:
            # すでに実行中なら二重起動を防ぐ
            if getattr(self, "_bt_proc", None) is not None:
                try:
                    st = self._bt_proc.state()
                    if st != QProcess.ProcessState.NotRunning:
                        QtWidgets.QMessageBox.information(self, "Backtest実行", "バックテストは既に実行中です。")
                        return
                except Exception:
                    pass

            # 実行対象（既存）を確認
            runner = Path("tools/backtest_run.py")
            if not runner.exists():
                QtWidgets.QMessageBox.warning(
                    self,
                    "Backtest実行",
                    "tools/backtest_run.py が見つかりません。\n"
                    "既存の実行導線（run_backtest など）がある場合は、そのメソッド名に合わせて委譲実装してください。"
                )
                self._append_progress("[gui] Backtest実行: tools/backtest_run.py not found")
                return

            # パラメータ（最小）
            symbol = "USDJPY-"  # 仕様：symbol は 'USDJPY-' で正しい
            tf = (self.tf_combo.currentText() or "M5").upper()

            # QDateEdit -> YYYY-MM-DD
            sd = self.start_edit.date().toString("yyyy-MM-dd")
            ed = self.end_edit.date().toString("yyyy-MM-dd")

            # WFO: checkbox を引数に反映（CLIが未対応でも害はないよう optional に）
            use_wfo = bool(self.chk_wfo.isChecked())

            # --csv は backtest_run.py で必須
            # 優先: 直近の price CSV（ローソク足CSV 読込で選んだもの）
            csv_in = None
            try:
                if getattr(self, "_last_plot_kind", None) == "price" and isinstance(getattr(self, "_last_plot_data", None), str):
                    pp = Path(self._last_plot_data)
                    if pp.exists():
                        csv_in = str(pp)
            except Exception:
                pass

            # fallback: ファイル選択
            if not csv_in:
                start_dir = str(Path("logs").resolve()) if Path("logs").exists() else str(Path(".").resolve())
                fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Backtest入力CSV（OHLCV）を選択", start_dir, "CSV (*.csv)")
                if not fn:
                    QtWidgets.QMessageBox.warning(self, "Backtest実行", "--csv が必要です。ローソク足CSVを選択してください。")
                    self._append_progress("[gui] Backtest aborted: missing --csv")
                    return
                csv_in = fn
            self._append_progress(f"[gui] Backtest input --csv = {csv_in}")

            # 実行コマンド（なるべく汎用的に）
            # まずは「python tools/backtest_run.py ...」形式（argparseの一般形）で起動する。
            exe = sys.executable
            args = [
                str(runner),
                "--csv", str(csv_in),
                "--symbol", symbol,
                "--tf", tf,
                "--start", sd,
                "--end", ed,
                "--profile", str(getattr(self, "_profile_name", "michibiki_std")),
            ]
            if use_wfo:
                args.append("--wfo")

            self._append_progress(f"[gui] Backtest run cmd: {exe} " + " ".join(args))

            proc = QProcess(self)
            self._bt_proc = proc

            # 出力取り込み
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

            def _on_ready():
                try:
                    out = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
                    if out.strip():
                        for line in out.splitlines():
                            self._append_progress(line)
                except Exception:
                    pass

            def _on_finished(code: int, status: QProcess.ExitStatus):
                self._append_progress(f"[gui] Backtest finished: code={code} status={status}")
                # 終了後に最新結果をロード（失敗してもGUIは生きる）
                try:
                    self._load_latest_backtest_result()
                except Exception as e:
                    self._append_progress(f"[gui] post-load warn: {e}")
                finally:
                    try:
                        self._bt_proc = None
                    except Exception:
                        pass

            proc.readyReadStandardOutput.connect(_on_ready)
            proc.finished.connect(_on_finished)

            # 起動
            proc.start(exe, args)
            if not proc.waitForStarted(2000):
                QtWidgets.QMessageBox.warning(self, "Backtest実行", "バックテストを開始できませんでした。")
                self._append_progress("[gui] Backtest start failed")
                self._bt_proc = None
                return

            self._append_progress("[gui] Backtest started")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Backtest実行エラー", str(e))
            self._append_progress(f"[gui] run backtest error: {e}")

    def _try_load_bands_for_equity(self, equity_csv: Path):
        """KPIService が利用できるなら bands を取得。無理なら None。"""
        try:
            ks = getattr(self, 'kpi_service', None)
            if ks is None:
                return None
            # 既存API優先：存在するものだけ試す
            if hasattr(ks, 'load_equity_curve_with_action_bands'):
                out = ks.load_equity_curve_with_action_bands(equity_csv=Path(equity_csv))
                if isinstance(out, dict):
                    return out.get('bands') or (out.get('payload') or {}).get('bands')
            if hasattr(ks, 'load_backtest_kpi_summary'):
                out = ks.load_backtest_kpi_summary(equity_csv=Path(equity_csv), profile=self._profile_name)
                if isinstance(out, dict):
                    return out.get('bands') or (out.get('payload') or {}).get('bands')
        except Exception as e:
            self._append_progress(f'[gui] bands load warn: {e}')
        return None


    def _load_plot(self, path_or_csv, bands=None):
        # NOTE: GUI is draw-only. bands must be passed from services layer.

        p = Path(path_or_csv)
        csv_path = str(p)
        ##
        if "equity_curve.csv" in csv_path:
            try:
                # DataFrameを読み込んで保持
                df = pd.read_csv(csv_path)
                if "time" in df.columns and not is_datetime64_any_dtype(df["time"]):
                    df["time"] = pd.to_datetime(df["time"], errors="coerce")
                self._current_equity_df = df
                self._current_price_df = None

                # 描画前のデバッグログ
                csv_abs_path = str(p.resolve())
                equity_min = float(df["equity"].min()) if "equity" in df.columns and len(df) > 0 else None
                equity_max = float(df["equity"].max()) if "equity" in df.columns and len(df) > 0 else None
                equity_unique_count = df["equity"].nunique() if "equity" in df.columns else 0
                time_unique_count = df["time"].nunique() if "time" in df.columns else 0
                self._append_progress(f"[gui] EQUITY plot before: csv={csv_abs_path}")
                self._append_progress(f"[gui] EQUITY plot before: equity min={equity_min} max={equity_max} unique={equity_unique_count} len={len(df)}")
                self._append_progress(f"[gui] EQUITY plot before: time unique={time_unique_count}")
                plot_equity_with_markers_to_figure(self.fig, csv_path, note=p.name, bands=bands)

                # axes数の確認（デバッグ用）
                axes_count = len(self.fig.axes)
                if axes_count != 1:
                    self._append_progress(f"[gui] WARNING: axes count = {axes_count} (expected 1)")

                # 描画後のデバッグログ（axesとレンジを確認）
                try:
                    if axes_count > 0:
                        ax = self.fig.axes[0]
                        ax_id = f"ax_{id(ax)}"
                        y_range = ax.get_ylim() if ax else None
                        x_range = ax.get_xlim() if ax else None
                        self._append_progress(f"[gui] EQUITY plot after: axes={ax_id} y_range={y_range} x_range={x_range}")
                except Exception as e:
                    self._append_progress(f"[gui] EQUITY plot after debug error: {e}")

                # tooltip for action bands (reason) - draw-only
                try:
                    if not getattr(self, "_band_tooltip_hooked", False):
                        self._band_tooltip_hooked = True
                        self._last_band_reason = None
                        self.canvas.mpl_connect("motion_notify_event", self._on_band_tooltip_motion)
                except Exception:
                    pass

                # 描画を確実に実行
                self.canvas.draw_idle()
                self.canvas.flush_events()

                self._append_progress(f"[gui] plotted EQUITY (markers) from {p.name}, axes={axes_count}")
                self._last_plot_kind = "equity"
                self._last_plot_data = str(p)  # CSVパスを記憶（期間ジャンプで利用）
                self._last_plot_note = p.name
                # 直近表示種別を保存（期間ジャンプ用）
                if self._pop is not None:
                    self._pop._last_kind = "equity"
                    self._pop._last_csv = str(p)
            except Exception as e:
                self.label_meta.setText(f"描画失敗: {e}")
                self._append_progress(f"[gui] plot error: {e}")
            return

        ##
        try:
            df = pd.read_csv(p)
            self.fig.clear()
            ax = self.fig.add_subplot(111)

            if "close" in df.columns:
                # DataFrameを保持
                self._current_price_df = df
                self._current_equity_df = None

                price = pd.to_numeric(df["close"], errors="coerce").ffill()
                if len(price) == 0 or price.iloc[0] == 0:
                    raise ValueError("プレビュー用 'close' 列が空です。")
                norm = price / price.iloc[0] * 100.0
                ax.plot(norm.values, label="Price (close, =100@start)")

                ax.set_title("Price Preview (from OHLCV)")
                ax.set_ylabel("index (=100@start)")
                self._append_progress(f"[gui] plotted PRICE {len(df)} rows from {p.name}")
                self._last_plot_kind = "price"
                self._last_plot_data = str(p)
                self._last_plot_note = p.name
                # 直近表示種別を保存（期間ジャンプ用）
                if self._pop is not None:
                    self._pop._last_kind = "price"
                    self._pop._last_csv = str(p)
            else:
                raise ValueError(f"CSVに 'close' 列が含まれていません。columns={list(df.columns)}")

            ax.set_xlabel("bars")
            ax.legend()

            # レイアウト調整
            try:
                self.fig.tight_layout()
            except Exception as e:
                self._append_progress(f"[gui] tight_layout error: {e}")

            # axes数の確認（デバッグ用）
            axes_count = len(self.fig.axes)
            if axes_count != 1:
                self._append_progress(f"[gui] WARNING: axes count = {axes_count} (expected 1)")

            # 描画を確実に実行
            self.canvas.draw_idle()
            self.canvas.flush_events()

        except Exception as e:
            self.label_meta.setText(f"描画失敗: {e}")
            self._append_progress(f"[gui] plot error: {e}")

    def _load_metrics(self, metrics_path: Path):
        self.table.setRowCount(0)
        try:
            txt = Path(metrics_path).read_text(encoding="utf-8")
            m = json.loads(txt)

            # 成果物検証結果を読んで表示更新
            output_ok = m.get("output_ok", None)
            output_errors = m.get("output_errors") or []
            # list[str] を想定。違う型でも安全に list[str] へ寄せる
            if not isinstance(output_errors, list):
                output_errors = []
            output_errors = [str(e) for e in output_errors if e]

            if hasattr(self, "output_status_label"):
                if output_ok is False:
                    # NG時: 赤系で「成果物検証NG」＋ output_errors を最大3行
                    error_lines = output_errors[:3]
                    error_text = "\n".join([f"- {err}" for err in error_lines])
                    if len(output_errors) > 3:
                        error_text += "\n…"
                    status_text = f"成果物検証NG\n{error_text}"
                    self.output_status_label.setText(status_text)
                    self.output_status_label.setStyleSheet("font-size: 11px; color: #b00020;")
                    self.output_status_label.show()
                elif output_ok is True:
                    # OK時: 非表示（推奨）
                    self.output_status_label.hide()
                # output_ok is None の場合は何もしない（非表示のまま）

            # WFO の場合は {train:{}, test:{}} 形式
            if "train" in m and "test" in m:
                # test 側を表に出す、train はログに
                ts = m["test"]; tr = m["train"]
                msg = (f"[WFO] Train ret={tr['total_return']*100:.2f}% mdd={tr['max_drawdown']*100:.2f}% sharpe≈{tr['sharpe_like']:.2f} "
                       f"| Test ret={ts['total_return']*100:.2f}% mdd={ts['max_drawdown']*100:.2f}% sharpe≈{ts['sharpe_like']:.2f}")
                self.label_meta.setText(msg)
                rows = ts
            else:
                rows = m
                self.label_meta.setText(
                    f"[BT] ret={m['total_return']*100:.2f}% mdd={m['max_drawdown']*100:.2f}% sharpe≈{m['sharpe_like']:.2f} bars={m['bars']}"
                )

            # 表へ
            for k in [
                "start_equity","end_equity","total_return","max_drawdown","max_dd_days","sharpe_like","bars",
                "trades","win_rate","avg_pnl","profit_factor","avg_holding_bars","avg_holding_days",
                "max_consec_win","max_consec_loss"
            ]:
                if k in rows:
                    r = self.table.rowCount()
                    self.table.insertRow(r)
                    self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(k))
                    val = rows[k]
                    if isinstance(val, float):
                        if "return" in k or "drawdown" in k or k=="win_rate":
                            val = f"{val*100:.2f}%"
                        else:
                            val = f"{val:.4g}"
                    self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(val)))

            self._append_progress("[gui] metrics loaded")
        except Exception as e:
            self.label_meta.setText(f"メトリクス読込失敗: {e}")
            self._append_progress(f"[gui] metrics error: {e}")

    # ------------------ 期間ジャンプ（本体） ------------------
    def _on_range_jump(self, mode: str) -> None:
        """
        Backtestタブのインライン描画と、ポップアウト済みウィンドウの両方に期間ジャンプを適用する。
        - インライン: equity だけでなく price プレビューにも対応。
        - ポップアウト: PlotWindow.jump_range() に委譲。
        """
        try:
            # 1) ポップアウト側（起動済みなら）
            if self._pop is not None:
                try:
                    self._pop.jump_range(mode)
                except Exception as e:
                    self._append_progress(f"[pop] jump_range warn: {e}")

            # 2) インライン側：equity
            if self._last_plot_kind == "equity" and isinstance(self._last_plot_data, str):
                csvp = Path(self._last_plot_data)
                if csvp.exists():
                    df = pd.read_csv(csvp)
                    if "time" in df.columns:
                        t = pd.to_datetime(df["time"], errors="coerce").dropna()
                        if len(t) >= 2:
                            tmax = t.iloc[-1]
                            if mode == "ALL":
                                tmin = t.iloc[0]
                            elif mode == "1W":
                                tmin = tmax - pd.Timedelta(days=7)
                            elif mode == "1M":
                                tmin = tmax - pd.Timedelta(days=31)
                            else:
                                raise ValueError(f"Unknown mode: {mode}")
                            tmin = max(t.iloc[0], tmin)
                            if self.fig.axes:
                                ax = self.fig.axes[0]
                                ax.set_xlim(tmin.to_pydatetime(), tmax.to_pydatetime())
                                self.canvas.draw_idle()
                                if mode == "ALL" and isinstance(self.start_edit, QtWidgets.QDateEdit):
                                    first = t.iloc[0]
                                    last = t.iloc[-1]
                                    self.start_edit.setDate(
                                        QtCore.QDate(first.year, first.month, first.day)
                                    )
                                    self.end_edit.setDate(
                                        QtCore.QDate(last.year, last.month, last.day)
                                    )
                                    self._append_progress(
                                        f"[gui] date pickers reset to ALL (equity): {first.date()} .. {last.date()}"
                                    )
                        else:
                            raise RuntimeError("エクイティの時系列が短すぎます。")
                    else:
                        raise RuntimeError("equity_curve.csv に 'time' 列がありません。")
                else:
                    raise RuntimeError("最後に描画したCSVが見つかりません。")

            # 3) インライン側：price（time列ありなら日付、なければバー数でズーム）
            elif self._last_plot_kind == "price" and isinstance(self._last_plot_data, str):
                csvp = Path(self._last_plot_data)
                if not csvp.exists():
                    raise RuntimeError("最後に描画した価格CSVが見つかりません。")
                df = pd.read_csv(csvp)

                if "time" in df.columns:
                    t = pd.to_datetime(df["time"], errors="coerce").dropna()
                    if len(t) < 2:
                        raise RuntimeError("価格プレビューの時系列が短すぎます。")
                    tmax = t.iloc[-1]
                    if mode == "ALL":
                        tmin = t.iloc[0]
                    elif mode == "1W":
                        tmin = tmax - pd.Timedelta(days=7)
                    elif mode == "1M":
                        tmin = tmax - pd.Timedelta(days=31)
                    else:
                        raise ValueError(f"Unknown mode: {mode}")
                    tmin = max(t.iloc[0], tmin)
                    if self.fig.axes:
                        ax = self.fig.axes[0]
                        ax.set_xlim(tmin.to_pydatetime(), tmax.to_pydatetime())
                        self.canvas.draw_idle()
                        if mode == "ALL" and isinstance(self.start_edit, QtWidgets.QDateEdit) and "time" in df.columns:
                            try:
                                first = t.iloc[0]
                                last = t.iloc[-1]
                                self.start_edit.setDate(
                                    QtCore.QDate(first.year, first.month, first.day)
                                )
                                self.end_edit.setDate(
                                    QtCore.QDate(last.year, last.month, last.day)
                                )
                                self._append_progress(
                                    f"[gui] date pickers reset to ALL (price): {first.date()} .. {last.date()}"
                                )
                            except Exception as e:
                                self._append_progress(f"[gui] failed to sync date pickers on price: {e}")
                else:
                    # x軸がインデックス（0..n-1）の場合は「バー数」で近似
                    n = len(df)
                    if n < 2:
                        raise RuntimeError("価格プレビューの行数が不足しています。")
                    tf = (self.tf_combo.currentText() or "M5").upper()
                    tf_min = {"M1":1, "M5":5, "M15":15, "M30":30, "H1":60, "H4":240, "D1":1440}.get(tf, 5)
                    def bars_for_days(days: int) -> int:
                        return int(days * 24 * 60 / tf_min)
                    if mode == "ALL":
                        start_idx = 0
                    elif mode == "1W":
                        start_idx = max(0, n - bars_for_days(7))
                    elif mode == "1M":
                        start_idx = max(0, n - bars_for_days(31))
                    else:
                        raise ValueError(f"Unknown mode: {mode}")
                    if self.fig.axes:
                        ax = self.fig.axes[0]
                        ax.set_xlim(start_idx, n - 1)
                        self.canvas.draw_idle()

            else:
                # それ以外（未描画など）
                self._append_progress("[gui] 期間ジャンプは直近の描画対象に対してのみ動作します。")

            # ステータス表示
            self.label_meta.setText(f"表示期間を {mode} に切り替えました")

        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "期間ジャンプエラー", str(e))
            self._append_progress(f"[range] error: {e}")

    def _on_band_tooltip_motion(self, event) -> None:
        try:
            ax = getattr(self, "ax", None)
            if ax is None and hasattr(self, "fig") and len(self.fig.axes) > 0:
                ax = self.fig.axes[0]
            if ax is None:
                return
            spans = getattr(ax, "_band_spans", None) or []
            if event is None or event.xdata is None:
                if getattr(self, "_last_band_reason", None):
                    QToolTip.hideText()
                    self._last_band_reason = None
                return

            x = event.xdata
            reason = ""
            for sp in spans:
                r = getattr(sp, "_band_reason", "") or ""
                if not r:
                    continue
                try:
                    x0 = getattr(sp, "_band_x0", None)
                    x1 = getattr(sp, "_band_x1", None)
                    if x0 is None or x1 is None:
                        continue
                    if float(x0) <= float(x) <= float(x1):
                        reason = r
                        break
                except Exception:
                    continue

            reason = (reason or "").strip()
            if reason:
                if reason != getattr(self, "_last_band_reason", None):
                    QToolTip.showText(QCursor.pos(), reason)
                    self._last_band_reason = reason
            else:
                if getattr(self, "_last_band_reason", None):
                    QToolTip.hideText()
                    self._last_band_reason = None
        except Exception:
            return
