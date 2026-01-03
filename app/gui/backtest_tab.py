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
