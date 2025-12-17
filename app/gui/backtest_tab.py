# app/gui/backtest_tab.py
from __future__ import annotations

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

def plot_equity_with_markers_to_figure(fig: Figure, csv_path: str, note: str = ""):
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

    # 日時化
    if "time" in df.columns and not is_datetime64_any_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"], errors="coerce")

    # ベース線
    fig.clear()
    ax = fig.add_subplot(111)
    ax.set_title("Equity Curve & Trade Markers" + (f" — {p.name}" if p.name else ""))
    ax.set_xlabel("time")
    ax.set_ylabel("equity (JPY)")
    ax.plot(df["time"], df["equity"], lw=1.4, antialiased=True, label="Equity", zorder=2)
    ax.margins(x=0.01, y=0.06)
    ax.grid(True, which="major", alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(_thousands))
    ax.yaxis.set_major_locator(AutoLocator())

    # --- マーカー①: signal の変化点 ---
    buys = sells = pd.DataFrame()
    if "signal" in df.columns:
        sig = pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int)
        chg = sig.ne(sig.shift(1)).fillna(sig.iloc[0] != 0)
        if len(sig) > 0 and sig.iloc[0] != 0:
            chg.iloc[0] = True
        buys  = df[(sig ==  1) & chg]
        sells = df[(sig == -1) & chg]

    # --- マーカー②: trades*.csv の entry_time でフォールバック ---
    if (buys.empty and sells.empty):
        tcsv = _find_trades_csv(p)
        if tcsv is not None:
            try:
                tdf = pd.read_csv(tcsv)
                # 必須列チェック
                if "entry_time" in tdf.columns:
                    tdf["entry_time"] = pd.to_datetime(tdf["entry_time"], errors="coerce")
                    # 方向の推定：directionが無ければ pnl の符号で代用
                    if "direction" in tdf.columns:
                        dirv = pd.to_numeric(tdf["direction"], errors="coerce").fillna(0).astype(int)
                    else:
                        dirv = np.sign(pd.to_numeric(tdf.get("pnl", 0.0), errors="coerce").fillna(0.0)).astype(int)
                    # エクイティ上の Y 座標は近い時刻の equity を使う
                    if "time" in df.columns:
                        eq = df[["time", "equity"]].dropna().copy()
                        eq["time"] = pd.to_datetime(eq["time"], errors="coerce")
                        eq = eq.set_index("time").sort_index()

                        # entry の最近傍 equity を拾う
                        def _y_at(t):
                            try:
                                idx = eq.index.searchsorted(t, side="left")
                                if idx == len(eq):
                                    idx -= 1
                                return float(eq.iloc[idx, 0])
                            except Exception:
                                return np.nan

                        tdf["y"] = tdf["entry_time"].map(_y_at)
                        tb = tdf[(dirv ==  1)].copy()
                        ts = tdf[(dirv == -1)].copy()
                        tb = tb.dropna(subset=["y"])
                        ts = ts.dropna(subset=["y"])
                        buys  = pd.DataFrame({"time": tb["entry_time"], "equity": tb["y"]})
                        sells = pd.DataFrame({"time": ts["entry_time"], "equity": ts["y"]})
                        print(f"[gui] fallback markers from trades: buys={len(buys)} sells={len(sells)} ({tcsv.name})")
            except Exception as e:
                print(f"[gui] trades fallback error: {e}")

    # マーカー描画
    if not buys.empty:
        ax.scatter(buys["time"], buys["equity"],
                   marker="o", s=48, facecolors="tab:blue", edgecolors="black",
                   linewidths=0.7, label="Buy", zorder=5)
    if not sells.empty:
        ax.scatter(sells["time"], sells["equity"],
                   marker="x", s=64, c="tab:orange", linewidths=1.4,
                   label="Sell", zorder=6)

    # 日付目盛り
    if "time" in df.columns:
        loc = AutoDateLocator()
        ax.xaxis.set_major_locator(loc)
        ax.xaxis.set_major_formatter(ConciseDateFormatter(loc))
        fig.autofmt_xdate()

    ax.legend(loc="upper left")

    # レイアウト調整（axes数の確認も含む）
    try:
        fig.tight_layout()
        # デバッグ: axesの数を確認
        axes_count = len(fig.axes)
        if axes_count != 1:
            print(f"[gui] WARNING: plot_equity_with_markers_to_figure: axes count = {axes_count} (expected 1)")
    except Exception as e:
        print(f"[gui] tight_layout error: {e}")

    # 描画後のデバッグログ（EQUITYが直線に見える原因調査用）
    try:
        csv_abs_path = str(Path(csv_path).resolve())
        equity_min = float(df["equity"].min()) if "equity" in df.columns and len(df) > 0 else None
        equity_max = float(df["equity"].max()) if "equity" in df.columns and len(df) > 0 else None
        equity_unique_count = df["equity"].nunique() if "equity" in df.columns else 0
        time_unique_count = df["time"].nunique() if "time" in df.columns else 0

        # 描画先のaxes識別子と表示レンジ
        ax_id = f"ax_{id(ax)}"
        y_range = ax.get_ylim() if ax else None
        x_range = ax.get_xlim() if ax else None

        print(f"[gui] EQUITY plot debug: csv={csv_abs_path}")
        print(f"[gui] EQUITY plot debug: equity min={equity_min} max={equity_max} unique={equity_unique_count} len={len(df)}")
        print(f"[gui] EQUITY plot debug: time unique={time_unique_count}")
        print(f"[gui] EQUITY plot debug: axes={ax_id} y_range={y_range} x_range={x_range}")
    except Exception as e:
        print(f"[gui] EQUITY plot debug error: {e}")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class PlotWindow(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        mode: str = "bt",
        equity_df: Optional[pd.DataFrame] = None,
        price_df: Optional[pd.DataFrame] = None,
        wfo_train_df: Optional[pd.DataFrame] = None,
        wfo_test_df: Optional[pd.DataFrame] = None,
        last_view_kind: str | None = None,
        last_csv: str | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("FXBot — Chart")
        self.resize(1000, 650)

        # Figure + Canvas を新規作成（使い回さない）
        self.figure = Figure(figsize=(10, 6), tight_layout=True)
        self.canvas = Canvas(self.figure)
        self.toolbar = Toolbar(self.canvas, self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

        self.span = None
        # _last_kind: ログ/表示用の状態（"equity" or "price"）
        # 注意: これはUIの見た目分岐（ボタン表示や強調）には使わない。UIはpriorityルールに従う。
        self._last_kind: str | None = last_view_kind
        self._last_csv: str | None = last_csv
        self._overlay_lines: list = []

        # データを保持
        self._mode = mode
        self._equity_df = equity_df
        self._price_df = price_df
        self._wfo_train_df = wfo_train_df
        self._wfo_test_df = wfo_test_df

        # 初期描画（showEventでも再描画される）
        self._plot()

    def showEvent(self, event):
        """ウインドウが表示されたときに必ず描画を実行"""
        super().showEvent(event)
        # 再描画を確実に実行
        self._plot()

    def _plot(self):
        """modeに応じて描画を実行する。"""
        self.figure.clear()

        if self._mode == "wfo":
            # Walk-Forwardモード: train/test equity + overlay
            self._plot_wfo()
        elif self._equity_df is not None:
            # Backtestモード: price + equity
            self._plot_bt_equity()
        elif self._price_df is not None:
            # Price preview
            self._plot_price()
        else:
            # データがない場合は何も描画しない
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, "データがありません", ha="center", va="center", transform=ax.transAxes)
            try:
                self.figure.tight_layout()
            except Exception:
                pass
            self.canvas.draw_idle()
            self.canvas.flush_events()
            return

    def _plot_bt_equity(self):
        """Backtestモード: equity curveを描画"""
        if self._equity_df is None:
            return

        df = self._equity_df.copy()

        # 2段のaxes（上：拡大、下：全体ナビ）
        gs = gridspec.GridSpec(2, 1, height_ratios=[3, 1], hspace=0.08, figure=self.figure)
        ax_main = self.figure.add_subplot(gs[0])
        ax_nav  = self.figure.add_subplot(gs[1], sharex=ax_main)

        # 上段：本描画（マーカー付き）
        ax_main.plot(df["time"], df["equity"], lw=1.4, label="Equity", zorder=2)
        ax_main.grid(True, alpha=0.25)

        # マーカー描画
        buys = sells = pd.DataFrame()
        if "signal" in df.columns:
            sig = pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int)
            chg = sig.ne(sig.shift(1)).fillna(sig.iloc[0] != 0)
            if len(sig) > 0 and sig.iloc[0] != 0:
                chg.iloc[0] = True
            buys  = df[(sig ==  1) & chg]
            sells = df[(sig == -1) & chg]

        if not buys.empty:
            ax_main.scatter(buys["time"], buys["equity"], marker="o", s=48,
                            facecolors="tab:blue", edgecolors="black", linewidths=0.7, label="Buy", zorder=5)
        if not sells.empty:
            ax_main.scatter(sells["time"], sells["equity"], marker="x", s=64,
                            c="tab:orange", linewidths=1.4, label="Sell", zorder=6)

        ax_main.legend(loc="upper left")
        ax_main.margins(x=0.01, y=0.05)

        # 目盛り・タイトル
        ax_main.set_title("Equity Curve & Trade Markers")
        ax_main.set_ylabel("equity (JPY)")
        ax_main.yaxis.set_major_locator(AutoLocator())
        ax_main.yaxis.set_major_formatter(FuncFormatter(_thousands))

        # 下段：全体ナビ（薄い線）
        ax_nav.plot(df["time"], df["equity"], lw=1.0, alpha=0.5)
        ax_nav.grid(True, alpha=0.2)

        # SpanSelector で範囲選択 → 上段の xlim を同期
        def onselect(xmin, xmax):
            ax_main.set_xlim(xmin, xmax)
            self.canvas.draw_idle()

        self.span = SpanSelector(ax_nav, onselect, "horizontal", useblit=True,
                                 interactive=True, props=dict(alpha=0.15))

        # 目盛り体裁
        loc = AutoDateLocator()
        ax_nav.xaxis.set_major_locator(loc)
        ax_nav.xaxis.set_major_formatter(ConciseDateFormatter(loc))
        ax_main.xaxis.set_major_locator(loc)
        ax_main.xaxis.set_major_formatter(ConciseDateFormatter(loc))
        self.figure.autofmt_xdate()

        # 保存（期間ジャンプ用に参照）
        self.ax_main = ax_main
        self.ax_nav  = ax_nav
        self._last_kind = "equity"

        # レイアウト調整と描画
        try:
            self.figure.tight_layout()
        except Exception as e:
            print(f"[PlotWindow] tight_layout error: {e}")

        # axes数の確認（デバッグ用）
        axes_count = len(self.figure.axes)
        if axes_count != 2:
            print(f"[PlotWindow] WARNING: _plot_bt_equity axes count = {axes_count} (expected 2)")

        self.canvas.draw_idle()
        self.canvas.flush_events()

    def _plot_wfo(self):
        """Walk-Forwardモード: train/test equity + overlay"""
        # 2段のaxes（上：拡大、下：全体ナビ）
        gs = gridspec.GridSpec(2, 1, height_ratios=[3, 1], hspace=0.08, figure=self.figure)
        ax_main = self.figure.add_subplot(gs[0])
        ax_nav  = self.figure.add_subplot(gs[1], sharex=ax_main)

        # test equityをメイン描画として表示
        if self._wfo_test_df is not None and "equity" in self._wfo_test_df.columns:
            df_test = self._wfo_test_df.copy()
            if "time" in df_test.columns and not is_datetime64_any_dtype(df_test["time"]):
                df_test["time"] = pd.to_datetime(df_test["time"], errors="coerce")

            ax_main.plot(df_test["time"], df_test["equity"], lw=2.2, color="tab:red",
                        label="WFO Test", zorder=2)
            ax_nav.plot(df_test["time"], df_test["equity"], lw=1.0, alpha=0.5, color="tab:red")

        # train equityをoverlay
        if self._wfo_train_df is not None and "equity" in self._wfo_train_df.columns:
            df_train = self._wfo_train_df.copy()
            if "time" in df_train.columns and not is_datetime64_any_dtype(df_train["time"]):
                df_train["time"] = pd.to_datetime(df_train["time"], errors="coerce")

            ax_main.plot(df_train["time"], df_train["equity"], linestyle="--", linewidth=1.0,
                        color="tab:blue", alpha=0.7, label="WFO Train", zorder=1)

        ax_main.grid(True, alpha=0.25)
        ax_main.legend(loc="upper left")
        ax_main.margins(x=0.01, y=0.05)

        # 目盛り・タイトル
        ax_main.set_title("Walk-Forward: Train/Test Equity")
        ax_main.set_ylabel("equity (JPY)")
        ax_main.yaxis.set_major_locator(AutoLocator())
        ax_main.yaxis.set_major_formatter(FuncFormatter(_thousands))

        ax_nav.grid(True, alpha=0.2)

        # SpanSelector で範囲選択 → 上段の xlim を同期
        def onselect(xmin, xmax):
            ax_main.set_xlim(xmin, xmax)
            self.canvas.draw_idle()

        self.span = SpanSelector(ax_nav, onselect, "horizontal", useblit=True,
                                 interactive=True, props=dict(alpha=0.15))

        # 目盛り体裁
        loc = AutoDateLocator()
        ax_nav.xaxis.set_major_locator(loc)
        ax_nav.xaxis.set_major_formatter(ConciseDateFormatter(loc))
        ax_main.xaxis.set_major_locator(loc)
        ax_main.xaxis.set_major_formatter(ConciseDateFormatter(loc))
        self.figure.autofmt_xdate()

        # 保存（期間ジャンプ用に参照）
        self.ax_main = ax_main
        self.ax_nav  = ax_nav
        self._last_kind = "equity"

        # レイアウト調整と描画
        try:
            self.figure.tight_layout()
        except Exception as e:
            print(f"[PlotWindow] tight_layout error: {e}")

        # axes数の確認（デバッグ用）
        axes_count = len(self.figure.axes)
        if axes_count != 2:
            print(f"[PlotWindow] WARNING: _plot_wfo axes count = {axes_count} (expected 2)")

        self.canvas.draw_idle()
        self.canvas.flush_events()

    def _plot_price(self):
        """Price previewを描画"""
        if self._price_df is None:
            return

        df = self._price_df.copy()
        ax = self.figure.add_subplot(111)

        if "close" in df.columns:
            price = pd.to_numeric(df["close"], errors="coerce").ffill()
            if len(price) > 0 and price.iloc[0] != 0:
                norm = price / price.iloc[0] * 100.0
                ax.plot(norm.values, label="Price (close, =100@start)")
                ax.set_title("Price Preview (from OHLCV)")
                ax.set_ylabel("index (=100@start)")
                ax.set_xlabel("bars")
                ax.legend()
                ax.grid(True, alpha=0.25)

        self.ax_main = ax
        self.ax_nav = None
        self._last_kind = "price"

        # レイアウト調整と描画
        try:
            self.figure.tight_layout()
        except Exception as e:
            print(f"[PlotWindow] tight_layout error: {e}")

        # axes数の確認（デバッグ用）
        axes_count = len(self.figure.axes)
        if axes_count != 1:
            print(f"[PlotWindow] WARNING: _plot_price axes count = {axes_count} (expected 1)")

        self.canvas.draw_idle()
        self.canvas.flush_events()

    def plot_equity_csv(self, csv_path: str):
        # 上段をクリアして本描画（既存の描画関数を再利用）
        self.figure.clear()
        gs = gridspec.GridSpec(2, 1, height_ratios=[3, 1], hspace=0.08, figure=self.figure)
        ax_main = self.figure.add_subplot(gs[0])
        ax_nav  = self.figure.add_subplot(gs[1], sharex=ax_main)

        # 読み込み
        df = pd.read_csv(csv_path)
        if "time" in df.columns and not is_datetime64_any_dtype(df["time"]):
            df["time"] = pd.to_datetime(df["time"], errors="coerce")

        # 上段：本描画（マーカー付き）
        ax_main.plot(df["time"], df["equity"], lw=1.4, label="Equity")
        ax_main.grid(True, alpha=0.25)
        sig = df.get("signal", pd.Series(0, index=df.index)).astype(int)
        chg = sig.ne(sig.shift(1)).fillna(False)
        if len(sig) > 0 and sig.iloc[0] != 0:
            chg.iloc[0] = True
        buys  = df[(sig == 1)  & chg]
        sells = df[(sig == -1) & chg]
        if not buys.empty:
            ax_main.scatter(buys["time"], buys["equity"], marker="o", s=48,
                            facecolors="tab:blue", edgecolors="black", linewidths=0.7, label="Buy", zorder=5)
        if not sells.empty:
            ax_main.scatter(sells["time"], sells["equity"], marker="x", s=64,
                            c="tab:orange", linewidths=1.4, label="Sell", zorder=6)
        ax_main.legend(loc="upper left")
        ax_main.margins(x=0.01, y=0.05)

        # 目盛り・タイトル
        from matplotlib.ticker import AutoLocator, FuncFormatter
        ax_main.set_title("Equity Curve & Trade Markers — " + Path(csv_path).name)
        ax_main.set_ylabel("equity (JPY)")
        ax_main.yaxis.set_major_locator(AutoLocator())
        ax_main.yaxis.set_major_formatter(FuncFormatter(_thousands))

        # 下段：全体ナビ（薄い線）
        ax_nav.plot(df["time"], df["equity"], lw=1.0, alpha=0.5)
        ax_nav.grid(True, alpha=0.2)

        # SpanSelector で範囲選択 → 上段の xlim を同期
        def onselect(xmin, xmax):
            ax_main.set_xlim(xmin, xmax)
            self.canvas.draw_idle()

        self.span = SpanSelector(ax_nav, onselect, "horizontal", useblit=True,
                                 interactive=True, props=dict(alpha=0.15))

        # 目盛り体裁
        loc = AutoDateLocator()
        ax_nav.xaxis.set_major_locator(loc)
        ax_nav.xaxis.set_major_formatter(ConciseDateFormatter(loc))
        ax_main.xaxis.set_major_locator(loc)
        ax_main.xaxis.set_major_formatter(ConciseDateFormatter(loc))
        self.figure.autofmt_xdate()
        self.canvas.draw_idle()

        # 保存（期間ジャンプ用に参照）
        self.ax_main = ax_main
        self.ax_nav  = ax_nav
        self._last_kind = "equity"
        self._last_csv = str(csv_path)

    def overlay_wfo_equity(
        self,
        df_train: Optional[pd.DataFrame],
        df_test: Optional[pd.DataFrame],
    ) -> None:
        """Overlay WFO train/test equity lines on this window's axes."""
        axes = self.figure.get_axes()
        if not axes:
            return
        ax_main = axes[0]

        for ln in getattr(self, "_overlay_lines", []):
            try:
                ln.remove()
            except Exception:
                pass
        self._overlay_lines = []

        def _prepare(df: Optional[pd.DataFrame]) -> Optional[Tuple[pd.Series, pd.Series]]:
            if df is None or "equity" not in df.columns:
                return None
            y = pd.to_numeric(df["equity"], errors="coerce")
            if "time" in df.columns:
                x = pd.to_datetime(df["time"], errors="coerce")
            else:
                x = pd.Series(df.index)
            mask = x.notna() & y.notna()
            if not mask.any():
                return None
            return x[mask], y[mask]

        train_data = _prepare(df_train)
        if train_data is not None:
            ln1 = ax_main.plot(
                train_data[0],
                train_data[1],
                linestyle="--",
                linewidth=1,
                color="tab:blue",
                alpha=0.7,
                label="WFO Train",
            )[0]
            self._overlay_lines.append(ln1)

        test_data = _prepare(df_test)
        if test_data is not None:
            ln2 = ax_main.plot(
                test_data[0],
                test_data[1],
                linestyle="-",
                linewidth=2,
                color="tab:red",
                alpha=0.9,
                label="WFO Test",
            )[0]
            self._overlay_lines.append(ln2)

        if self._overlay_lines:
            ax_main.legend(loc="upper left")
        self.canvas.draw_idle()
        self.canvas.flush_events()

    def plot_price_preview(self, csv_path: str, note: str = ""):
        import pandas as pd
        from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
        df = pd.read_csv(csv_path)
        if "time" in df.columns and not is_datetime64_any_dtype(df["time"]):
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
        if "close" not in df.columns:
            raise ValueError("CSVに 'close' 列がありません。")

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        price = pd.to_numeric(df["close"], errors="coerce").ffill()
        norm = price / price.iloc[0] * 100.0
        ax.plot(df["time"] if "time" in df.columns else range(len(norm)),
                norm.values, lw=1.4, label="Price (close, =100@start)")
        ax.set_title("Price Preview (from OHLCV)" + (f" — {note}" if note else ""))
        ax.set_ylabel("index (=100@start)")
        ax.set_xlabel("time")
        ax.grid(True, alpha=0.25)
        if "time" in df.columns:
            loc = AutoDateLocator()
            ax.xaxis.set_major_locator(loc)
            ax.xaxis.set_major_formatter(ConciseDateFormatter(loc))
            self.figure.autofmt_xdate()
        ax.legend(loc="best")
        self.canvas.draw_idle()
        self.ax_main = ax
        self.ax_nav = None
        self._last_kind = "price"
        self._last_csv = str(csv_path)

    def plot_heatmap(self, df, note: str = ""):
        """
        tools/backtest_run が生成する monthly_returns_*.csv の形式に対応したヒートマップ描画。

        期待カラム:
            - 'year'
            - 'month'
            - 値列: 'return' or 'ret' or 'pnl' or 'pnl_pct' のいずれか
        """
        import pandas as pd
        from matplotlib.ticker import PercentFormatter

        self.figure.clear()
        ax = self.figure.add_subplot(111)

        # --- v5 仕様: year_month にも対応させる ---
        if "year" in df.columns and "month" in df.columns:
            # 旧形式: そのまま使う
            year = df["year"].astype(int)
            month = df["month"].astype(int)
        elif "year_month" in df.columns:
            # 新形式: "2025-07" → year=2025, month=7 に分解
            ym = df["year_month"].astype(str)
            # フォーマットが "YYYY-MM" 前提
            df["year"] = ym.str.slice(0, 4).astype(int)
            df["month"] = ym.str.slice(5, 7).astype(int)
            year = df["year"]
            month = df["month"]
        else:
            QtWidgets.QMessageBox.information(
                self, "情報",
                "月次リターンCSVに 'year'/'month' も 'year_month' 列もありません。"
            )
            return
        # ここより下の処理では df['year'], df['month'] を使ってOK

        value_col = None
        for cand in ("return", "ret", "pnl_pct", "pnl"):
            if cand in df.columns:
                value_col = cand
                break

        df = df.copy()

        if value_col is not None:
            df["value"] = pd.to_numeric(df[value_col], errors="coerce")
        else:
            candidate_cols = [c for c in df.columns if c not in ("year", "month")]
            auto_col = None
            for c in candidate_cols:
                s = pd.to_numeric(df[c], errors="coerce")
                if s.notna().any():
                    auto_col = c
                    df["value"] = s
                    break

            if auto_col is None:
                QtWidgets.QMessageBox.information(
                    self,
                    "情報",
                    "月次リターンCSVに数値のリターン列が見つかりません。\n"
                    f"列一覧: {list(df.columns)}"
                )
                return

            print(f"[heatmap] auto-selected value column: {auto_col}")
        df = df.dropna(subset=["value"])

        if df.empty:
            QtWidgets.QMessageBox.information(self, "情報", "月次リターンに有効なデータがありません。")
            return

        pivot = df.pivot_table(
            index="year",
            columns="month",
            values="value",
            aggfunc="sum"
        ).reindex(columns=range(1, 13)).fillna(0.0)

        if pivot.empty:
            QtWidgets.QMessageBox.information(self, "情報", "月次リターンが空です。")
            return

        im = ax.imshow(pivot.values, aspect="auto", interpolation="nearest")

        ax.set_xticks(range(12))
        ax.set_xticklabels([f"{m:02d}" for m in range(1, 13)])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index.astype(str))

        ax.set_title("Monthly Return Heatmap" + (f" — {note}" if note else ""))
        ax.set_xlabel("month")
        ax.set_ylabel("year")

        cbar = self.figure.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.ax.yaxis.set_major_formatter(PercentFormatter(1.0))

        self.canvas.draw_idle()

    # === ���ԃW�����vAPI�i1W / 1M / ALL�j ===
    def jump_range(self, mode: str) -> None:
        """
        ポップアウト表示で 1W / 1M / ALL の X 範囲を切り替える。
        1) CSV の time 列 → 2) 描画中 xdata → 3) ファイル名のTF推定でバー数ズーム。
        """
        valid_modes = {"1W", "1M", "ALL"}
        mode = (mode or "").upper()
        if mode not in valid_modes:
            raise ValueError(f"Unknown mode: {mode}")

        # 安全フォールバック: _last_kind が None の場合は price を既定にする
        if self._last_kind not in {"equity", "price"}:
            self._last_kind = "price"

        # _last_csv が None の場合は、描画中のデータから推定を試みる
        if not self._last_csv:
            # データからCSVパスを推定できない場合は警告を出してreturn
            print(f"[pop] jump_range warn: _last_csv is None, cannot determine CSV path")
            return

        csv_path = Path(self._last_csv)
        if not csv_path.exists():
            raise RuntimeError(f"直近のCSVが見つかりません: {csv_path}")

        def _target_axes():
            axes = []
            seen: set[int] = set()
            for candidate in (getattr(self, "ax_main", None), getattr(self, "ax_nav", None)):
                if candidate is None or candidate not in self.figure.axes:
                    continue
                ident = id(candidate)
                if ident in seen:
                    continue
                seen.add(ident)
                axes.append(candidate)
            if not axes:
                for ax in self.figure.axes:
                    ident = id(ax)
                    if ident in seen:
                        continue
                    seen.add(ident)
                    axes.append(ax)
            return axes

        def _apply_xlim(xmin, xmax):
            axes = _target_axes()
            if not axes:
                raise RuntimeError("描画済みのAxesが無いためジャンプできません。")
            for ax in axes:
                ax.set_xlim(xmin, xmax)
            self.canvas.draw_idle()

        def _extract_xdata():
            for ax in (getattr(self, "ax_nav", None), getattr(self, "ax_main", None)):
                if ax is None:
                    continue
                lines = ax.get_lines()
                if lines:
                    data = lines[0].get_xdata()
                    if data is not None and len(data) >= 2:
                        return data
            for ax in self.figure.axes:
                lines = ax.get_lines()
                if lines:
                    data = lines[0].get_xdata()
                    if data is not None and len(data) >= 2:
                        return data
            return None

        def _apply_time_window(series: pd.Series) -> bool:
            series = pd.Series(series).dropna()
            if len(series) < 2:
                return False
            series = series.sort_values()
            latest = pd.Timestamp(series.iloc[-1])
            if mode == "ALL":
                earliest = pd.Timestamp(series.iloc[0])
            elif mode == "1W":
                earliest = latest - pd.Timedelta(days=7)
            else:
                earliest = latest - pd.Timedelta(days=31)
            first = pd.Timestamp(series.iloc[0])
            earliest = max(first, earliest)
            _apply_xlim(earliest.to_pydatetime(), latest.to_pydatetime())
            return True

        tf_keywords = {
            "M1": 1, "M3": 3, "M5": 5, "M10": 10, "M15": 15, "M30": 30,
            "H1": 60, "H2": 120, "H3": 180, "H4": 240, "H6": 360, "H8": 480, "H12": 720,
            "D1": 1440, "D2": 2880, "W1": 10080
        }

        def _infer_tf_minutes(path: Path) -> int | None:
            chunks = [path.stem.upper(), *(part.upper() for part in path.parts)]
            for name in chunks:
                for token, minutes in tf_keywords.items():
                    if re.search(rf"(?<![A-Z0-9]){token}(?![A-Z0-9])", name):
                        return minutes
                for part in re.split(r"[^A-Z0-9]+", name):
                    if not part:
                        continue
                    if part in tf_keywords:
                        return tf_keywords[part]
                    m = re.fullmatch(r"(M|H|D|W)(\d{1,3})", part)
                    if m:
                        unit = m.group(1)
                        value = int(m.group(2))
                        if value == 0:
                            continue
                        if unit == "M":
                            return value
                        if unit == "H":
                            return value * 60
                        if unit == "D":
                            return value * 1440
                        if unit == "W":
                            return value * 10080
            return None

        def _bars_for_mode(tf_minutes: int, total: int) -> int:
            if mode == "ALL":
                return total
            days = 7 if mode == "1W" else 31
            bars = int(days * 24 * 60 / max(tf_minutes, 1))
            return max(1, bars)

        try:
            df_time = pd.read_csv(csv_path, usecols=["time"])
        except ValueError:
            df_time = None
        except Exception as e:
            print(f"[pop] jump_range warn: failed to read time column from {csv_path}: {e}")
            df_time = None

        if df_time is not None and "time" in df_time.columns:
            times = pd.to_datetime(df_time["time"], errors="coerce")
            if _apply_time_window(times):
                return

        xdata = _extract_xdata()
        if xdata is None:
            raise RuntimeError("描画済みのXデータが無いためジャンプできません。")

        arr = np.asarray(xdata)
        if arr.size < 2:
            raise RuntimeError("描画済みデータが少なすぎます。")

        is_datetime = np.issubdtype(arr.dtype, np.datetime64)
        if not is_datetime and arr.dtype == object:
            sample = next((v for v in arr if v is not None), None)
            if isinstance(sample, (pd.Timestamp, datetime, np.datetime64)):
                is_datetime = True

        if is_datetime:
            if _apply_time_window(pd.Series(arr)):
                return

        tf_minutes = _infer_tf_minutes(csv_path)
        if tf_minutes is None:
            raise RuntimeError("ファイル名からタイムフレームを推定できません。例: *_M5_*.csv")

        total = arr.size
        bars = _bars_for_mode(tf_minutes, total)
        start_idx = 0 if mode == "ALL" else max(0, total - bars)
        xmin = arr[start_idx]
        xmax = arr[-1]
        if isinstance(xmin, np.generic):
            xmin = xmin.item()
        if isinstance(xmax, np.generic):
            xmax = xmax.item()
        _apply_xlim(xmin, xmax)

class BacktestTab(QtWidgets.QWidget):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        kpi_service: Optional[Any] = None,
        profile_name: str = "michibiki_std",
    ) -> None:
        super().__init__(parent)
        from app.services.kpi_service import KPIService

        self._kpi_service = kpi_service if kpi_service is not None else KPIService(base_dir=Path("."))
        self._profile_name = profile_name

        # === 入力フォーム ===
        self.symbol_edit = QtWidgets.QLineEdit("USDJPY-")
        self.tf_combo = QtWidgets.QComboBox(); self.tf_combo.addItems(["M5", "M15", "H1"])

        # 実行／表示モード（Backtest / Walk-Forward / Overlay）
        self.mode_bt = QtWidgets.QRadioButton("Backtest")
        self.mode_wfo = QtWidgets.QRadioButton("Walk-Forward")
        self.mode_overlay = QtWidgets.QRadioButton("Overlay")

        self.mode_bt.setChecked(True)  # デフォルトは Backtest

        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.addButton(self.mode_bt)
        self.mode_group.addButton(self.mode_wfo)
        self.mode_group.addButton(self.mode_overlay)

        # From/To 日付ピッカー
        self.start_edit = QtWidgets.QDateEdit(QtCore.QDate(2024, 1, 1))
        self.start_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_edit.setCalendarPopup(True)

        self.end_edit = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.end_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_edit.setCalendarPopup(True)

        self.capital_edit = QtWidgets.QLineEdit("100000")
        self.layout_combo = QtWidgets.QComboBox(); self.layout_combo.addItems(["per-symbol", "flat"])
        self.train_ratio_edit = QtWidgets.QLineEdit("0.7")

        form = QtWidgets.QGridLayout()
        r = 0
        form.addWidget(QtWidgets.QLabel("Symbol"), r, 0); form.addWidget(self.symbol_edit, r, 1)
        form.addWidget(QtWidgets.QLabel("Timeframe"), r, 2); form.addWidget(self.tf_combo, r, 3); r += 1

        # Mode 行（ラジオボタンを横並び）
        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.addWidget(self.mode_bt)
        mode_layout.addWidget(self.mode_wfo)
        mode_layout.addWidget(self.mode_overlay)
        mode_layout.addStretch(1)

        form.addWidget(QtWidgets.QLabel("Mode"), r, 0)
        form.addLayout(mode_layout, r, 1, 1, 3)
        r += 1

        form.addWidget(QtWidgets.QLabel("Layout"), r, 0); form.addWidget(self.layout_combo, r, 1); r += 1
        form.addWidget(QtWidgets.QLabel("Start"), r, 0); form.addWidget(self.start_edit, r, 1)
        form.addWidget(QtWidgets.QLabel("End"),   r, 2); form.addWidget(self.end_edit,   r, 3); r += 1
        form.addWidget(QtWidgets.QLabel("Initial Capital (JPY)"), r, 0); form.addWidget(self.capital_edit, r, 1)
        form.addWidget(QtWidgets.QLabel("Train Ratio (WFO)"), r, 2); form.addWidget(self.train_ratio_edit, r, 3); r += 1

        # === ボタン ===
        self.btn_update = QtWidgets.QPushButton("データ確認＆更新")
        self.btn_run    = QtWidgets.QPushButton("テスト実行")
        self.btn_popout = QtWidgets.QPushButton("別ウインドウで表示")
        self.btn_export = QtWidgets.QPushButton("結果をエクスポート(JSON)")
        self.btn_savepng= QtWidgets.QPushButton("グラフをPNG保存")
        self.btn_heatmap= QtWidgets.QPushButton("月次ヒートマップ")

        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(self.btn_update); btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_popout); btns.addWidget(self.btn_heatmap)
        btns.addStretch(1)
        btns.addWidget(self.btn_savepng); btns.addWidget(self.btn_export)

        # === 期間ジャンプ（1W / 1M / ALL） ===
        range_box = QtWidgets.QHBoxLayout()
        range_box.setContentsMargins(0, 0, 0, 0)
        range_box.setSpacing(6)
        range_box.addWidget(QtWidgets.QLabel("期間:"))

        self.btn_1w  = QtWidgets.QPushButton("1W")
        self.btn_1m  = QtWidgets.QPushButton("1M")
        self.btn_all = QtWidgets.QPushButton("ALL")
        for b in (self.btn_1w, self.btn_1m, self.btn_all):
            b.setFixedHeight(26)

        self.btn_1w.clicked.connect(partial(self._on_range_jump, "1W"))
        self.btn_1m.clicked.connect(partial(self._on_range_jump, "1M"))
        self.btn_all.clicked.connect(partial(self._on_range_jump, "ALL"))

        range_box.addWidget(self.btn_1w)
        range_box.addWidget(self.btn_1m)
        range_box.addWidget(self.btn_all)
        range_box.addStretch(1)

        # === プロット・メトリクス ===
        self.fig = Figure(figsize=(6,3), constrained_layout=True)
        self.canvas = Canvas(self.fig)
        self.label_meta = QtWidgets.QLabel("未実行"); self.label_meta.setWordWrap(True)

        # モデル情報（active_model.json）
        self.model_info = QtWidgets.QLabel("model: -")
        self.model_info.setStyleSheet("color: #666;")

        # 出力パス
        self.path_edit = QtWidgets.QLineEdit(""); self.path_edit.setReadOnly(True)
        self.btn_open  = QtWidgets.QPushButton("CSVを開く…")
        path_line = QtWidgets.QHBoxLayout()
        path_line.addWidget(QtWidgets.QLabel("Equity CSV:")); path_line.addWidget(self.path_edit, 1); path_line.addWidget(self.btn_open)

        # プログレスバー
        self.progress_bar = QtWidgets.QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        # 成果物検証表示ラベル（控えめ表示）
        self.output_status_label = QtWidgets.QLabel("")
        self.output_status_label.setWordWrap(True)
        self.output_status_label.setStyleSheet("font-size: 11px; color: #666;")
        self.output_status_label.hide()

        # ★進捗アニメーション用の状態 & タイマー
        self._progress_value = 0          # 実際にバーに表示している値
        self._progress_target = 0         # エンジンから報告された目標値
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(100)  # 0.1秒ごとにちょっとずつ動かす
        self._progress_timer.timeout.connect(self._on_progress_timer)

        # 進捗ログ
        self.progress_box = QtWidgets.QPlainTextEdit(); self.progress_box.setReadOnly(True); self.progress_box.setMaximumBlockCount(1000)

        # メトリクス表
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(140)

        # Walk-Forward Stats パネル（後で metrics_wfo.json から更新）
        self.wfo_pf_label = QtWidgets.QLabel("WFO PF: -")
        self.wfo_winrate_label = QtWidgets.QLabel("WFO Win%: -")
        self.wfo_trades_label = QtWidgets.QLabel("WFO Trades: -")
        self.wfo_maxdd_label = QtWidgets.QLabel("WFO MaxDD: -")

        wfo_stats_layout = QtWidgets.QVBoxLayout()
        wfo_stats_layout.addWidget(self.wfo_pf_label)
        wfo_stats_layout.addWidget(self.wfo_winrate_label)
        wfo_stats_layout.addWidget(self.wfo_trades_label)
        wfo_stats_layout.addWidget(self.wfo_maxdd_label)

        self.wfo_stats_group = QtWidgets.QGroupBox("Walk-Forward Stats")
        self.wfo_stats_group.setLayout(wfo_stats_layout)

        # 全体配置
        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(btns)
        lay.addLayout(range_box)          # ← 期間ボタン列を追加
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.label_meta)
        lay.addLayout(path_line)
        lay.addWidget(QtWidgets.QLabel("モデル情報:")); lay.addWidget(self.model_info)
        lay.addWidget(QtWidgets.QLabel("メトリクス:")); lay.addWidget(self.table)
        lay.addWidget(self.progress_bar)
        lay.addWidget(self.output_status_label)  # 成果物検証表示（控えめ）
        lay.addWidget(QtWidgets.QLabel("進捗ログ:")); lay.addWidget(self.progress_box, 2)
        lay.addWidget(self.wfo_stats_group)

        # 内部状態
        self.proc: QProcess | None = None
        # _last_plot_kind: ログ/表示用の状態（"equity" or "price"）
        # 注意: これはUIの見た目分岐（ボタン表示や強調）には使わない。UIはpriorityルールに従う。
        self._last_plot_kind = None   # "equity" or "price"
        self._last_plot_data = None
        self._last_plot_note = ""
        self._pop = None              # PlotWindow
        self.plot_window: PlotWindow | None = None
        self._wfo_train_df: Optional[pd.DataFrame] = None
        self._wfo_test_df: Optional[pd.DataFrame] = None
        self._wfo_overlay_lines: list = []
        self._last_monthly_returns: Path | None = None
        # 描画用データ（別ウインドウ表示用）
        self._current_equity_df: Optional[pd.DataFrame] = None
        self._current_price_df: Optional[pd.DataFrame] = None

        # シグナル
        self.btn_update.clicked.connect(self._update_data)
        self.btn_run.clicked.connect(self._run_test)
        self.btn_open.clicked.connect(self._pick_file)
        self.btn_popout.clicked.connect(self._pop_out)
        self.btn_export.clicked.connect(self._export_result_json)
        self.btn_savepng.clicked.connect(self._save_png)
        self.btn_heatmap.clicked.connect(self._show_heatmap)
        # ダブルクリックでポップアウト
        def _on_canvas_dbl(ev):
            if ev.dblclick: self._pop_out()
        self.canvas.mpl_connect("button_press_event", _on_canvas_dbl)

        # 初回モデル情報表示
        self._load_model_info()
        # モード変更時に Walk-Forward 固有の入力を ON/OFF
        self.mode_bt.toggled.connect(self._on_mode_changed)
        self.mode_wfo.toggled.connect(self._on_mode_changed)
        self.mode_overlay.toggled.connect(self._on_mode_changed)

        # 初期状態の反映
        self._on_mode_changed()

    # ------------------ ヘルパ ------------------
    def _on_progress_timer(self):
        # target に向かって 1 ずつ近づける
        if self._progress_value < self._progress_target:
            self._progress_value += 1
            self.progress_bar.setValue(self._progress_value)
        else:
            # 目標に到達していて、かつ 100 ならタイマー停止
            if self._progress_target >= 100:
                self._progress_timer.stop()
    def _append_progress(self, text: str):
        self.progress_box.appendPlainText(text.rstrip())

    def _on_mode_changed(self, checked: bool = False):
        # Walk-Forward ラジオが ON のときだけ train_ratio を有効にする
        is_wfo = self.mode_wfo.isChecked()
        self.train_ratio_edit.setEnabled(is_wfo)

    def _current_mode_text(self) -> str:
        """UI 上のモード文字列を返す（Backtest / Walk-Forward / Overlay）。"""
        if self.mode_wfo.isChecked():
            return "Walk-Forward"
        if self.mode_overlay.isChecked():
            return "Overlay"
        return "Backtest"

    def _find_latest_bt_dir(self, out_dir: Path) -> Optional[Path]:
        """backtest_* フォルダの中から最新のものを探す"""
        cands = [p for p in out_dir.glob("backtest_*") if p.is_dir()]
        if not cands:
            return None
        return max(cands, key=lambda p: p.stat().st_mtime)

    def _find_latest_wfo_dir(self) -> Optional[pathlib.Path]:
        base = pathlib.Path("logs") / "backtest"
        if not base.exists():
            return None

        candidates = list(base.rglob("metrics_wfo.json"))
        if not candidates:
            return None

        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        return latest.parent

    def _load_latest_wfo_data(self) -> Optional[Dict[str, object]]:
        wfo_dir = self._find_latest_wfo_dir()
        if wfo_dir is None:
            print("[gui] no WFO dir found under logs/backtest")
            return None

        metrics_path = wfo_dir / "metrics_wfo.json"
        train_path = wfo_dir / "equity_train.csv"
        test_path = wfo_dir / "equity_test.csv"

        if not (metrics_path.exists() and train_path.exists() and test_path.exists()):
            print("[gui] missing WFO files:", metrics_path, train_path, test_path)
            return None

        with metrics_path.open("r", encoding="utf-8") as f:
            metrics = json.load(f)

        df_train = pd.read_csv(train_path)
        df_test = pd.read_csv(test_path)

        return {"metrics": metrics, "train": df_train, "test": df_test}

    def _update_wfo_stats_panel(self, metrics: Dict[str, object]) -> None:
        test = metrics.get("test") or {}
        pf = test.get("profit_factor") or test.get("pf")
        win_rate = test.get("win_rate")
        trades = test.get("trades") or test.get("num_trades")
        max_dd = test.get("max_drawdown") or test.get("max_dd")

        if isinstance(pf, (int, float)):
            self.wfo_pf_label.setText(f"WFO PF: {pf:.2f}")
        else:
            self.wfo_pf_label.setText("WFO PF: -")

        if isinstance(win_rate, (int, float)):
            val = win_rate * 100 if win_rate <= 1 else win_rate
            self.wfo_winrate_label.setText(f"WFO Win%: {val:.1f}%")
        else:
            self.wfo_winrate_label.setText("WFO Win%: -")

        self.wfo_trades_label.setText(f"WFO Trades: {trades}" if trades is not None else "WFO Trades: -")

        if isinstance(max_dd, (int, float)):
            self.wfo_maxdd_label.setText(f"WFO MaxDD: {max_dd:.2f}")
        else:
            self.wfo_maxdd_label.setText("WFO MaxDD: -")

    def _overlay_wfo_equity(
        self,
        df_train: Optional[pd.DataFrame],
        df_test: Optional[pd.DataFrame],
    ) -> None:
        """Overlay Train/Test equity lines onto the current plot axes."""
        lines = getattr(self, "_wfo_overlay_lines", [])
        for line in lines:
            try:
                line.remove()
            except Exception:
                pass
        self._wfo_overlay_lines = []

        if not self.fig.axes:
            return
        ax = self.fig.axes[0]

        def _prepare(df: Optional[pd.DataFrame]) -> Optional[Tuple[pd.Series, pd.Series]]:
            if df is None or "equity" not in df.columns:
                return None
            y = pd.to_numeric(df["equity"], errors="coerce")
            if "time" in df.columns:
                x = pd.to_datetime(df["time"], errors="coerce")
            else:
                x = pd.Series(df.index)
            mask = x.notna() & y.notna()
            if not mask.any():
                return None
            return x[mask], y[mask]

        train_data = _prepare(df_train)
        if train_data is not None:
            line, = ax.plot(
                train_data[0], train_data[1],
                linestyle="--", linewidth=1.0, color="tab:blue",
            )
            self._wfo_overlay_lines.append(line)

        test_data = _prepare(df_test)
        if test_data is not None:
            line, = ax.plot(
                test_data[0], test_data[1],
                linestyle="-", linewidth=2.2, color="tab:red",
            )
            self._wfo_overlay_lines.append(line)

        self.canvas.draw_idle()
        self.canvas.flush_events()

    class _WFOResult(QtCore.QObject):
        """
        Walk-Forward 検証の結果セットをまとめて持つだけの小さな入れ物。
        report_json: logs/retrain/report_*.json の中身
        equity_train: equity_train_*.csv → DataFrame
        equity_test:  equity_test_*.csv → DataFrame
        run_id: "report_XXXX.json" の XXXX 部分
        """

        def __init__(
            self,
            report_json: Dict[str, Any],
            equity_train: Optional[pd.DataFrame],
            equity_test: Optional[pd.DataFrame],
            run_id: str,
            parent: Optional[QtCore.QObject] = None,
        ) -> None:
            super().__init__(parent)
            self.report_json = report_json
            self.equity_train = equity_train
            self.equity_test = equity_test
            self.run_id = run_id

    # ---------------------------------------------------------
    # ここからが実際のヘルパーメソッド
    # ---------------------------------------------------------

    def _find_latest_wfo_files(self) -> Optional["_WFOResult"]:
        """
        logs/retrain/ 配下から最新の report_*.json を探し、
        対応する equity_train_*.csv / equity_test_*.csv を読み込んで返す。
        見つからなければ None を返す。
        """
        base_dir = Path("logs") / "retrain"
        if not base_dir.exists():
            # ログフォルダ自体がない場合
            print("[WFO] logs/retrain が存在しません")
            return None

        # report_*.json を更新日時順にソート
        report_files = sorted(
            base_dir.glob("report_*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        if not report_files:
            print("[WFO] report_*.json が見つかりません")
            return None

        latest_report = report_files[-1]
        stem = latest_report.stem  # 例: "report_1762505757473982"
        parts = stem.split("_", 1)
        run_id = parts[1] if len(parts) == 2 else ""

        try:
            with latest_report.open("r", encoding="utf-8") as f:
                report_json = json.load(f)
        except Exception as e:  # noqa: BLE001
            print(f"[WFO] report JSON の読込に失敗: {latest_report} ({e})")
            return None

        def _load_equity_csv(prefix: str) -> Optional[pd.DataFrame]:
            # run_id があるときは equity_train_{id}.csv を優先
            # なければ equity_train.csv を見る
            if run_id:
                candidate = base_dir / f"{prefix}_{run_id}.csv"
                if candidate.exists():
                    path = candidate
                else:
                    # フォールバック
                    path = base_dir / f"{prefix}.csv"
            else:
                path = base_dir / f"{prefix}.csv"

            if not path.exists():
                print(f"[WFO] {path} が存在しません（スキップ）")
                return None

            try:
                df = pd.read_csv(path)
            except Exception as e:  # noqa: BLE001
                print(f"[WFO] {path} の読込に失敗 ({e})")
                return None

            # time カラムがあれば datetime にしておく（グラフ用）
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], errors="coerce")

            return df

        equity_train = _load_equity_csv("equity_train")
        equity_test = _load_equity_csv("equity_test")

        return self._WFOResult(
            report_json=report_json,
            equity_train=equity_train,
            equity_test=equity_test,
            run_id=run_id,
            parent=self,
        )

    def _debug_print_wfo_summary(self, wfo: "_WFOResult") -> None:
        """
        とりあえず「ちゃんと読めたか」を確認するために、
        コンソールにざっくりサマリを吐く。
        あとでここから GUI のラベル更新などに差し替えればOK。
        """
        print("========== [WFO Latest Result] ==========")
        print(f"run_id       : {wfo.run_id}")
        print(f"report keys  : {list(wfo.report_json.keys())}")

        train_len = len(wfo.equity_train) if wfo.equity_train is not None else 0
        test_len = len(wfo.equity_test) if wfo.equity_test is not None else 0
        print(f"equity_train : {train_len} rows")
        print(f"equity_test  : {test_len} rows")

        # よくありそうなキーがあればチラ見せ（なければスキップでOK）
        for k in ("symbol", "timeframe", "train_period", "test_period", "pf_train", "pf_test", "win_rate_train", "win_rate_test"):
            if k in wfo.report_json:
                print(f"{k:>15}: {wfo.report_json[k]}")

        print("=========================================")

    def _load_model_info(self):
        p = PROJECT_ROOT / "active_model.json"
        if p.exists():
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                name = j.get("model_name") or j.get("name") or "-"
                th   = j.get("best_threshold") or j.get("threshold") or "-"
                fh   = j.get("features_hash") or "-"
                self.model_info.setText(f"name={name}  best_threshold={th}  features_hash={fh}")
            except Exception as e:
                self.model_info.setText(f"(model info error: {e})")
        else:
            self.model_info.setText("(active_model.json が見つかりません)")

    # ------------------ データ更新 ------------------
    def _update_data(self):
        sym = self.symbol_edit.text().strip().upper()
        # "USDJPY" → "USDJPY-" に変換（symbol は 'USDJPY-' が正）
        if sym == "USDJPY":
            sym = "USDJPY-"
        tf  = self.tf_combo.currentText()
        mode_text = self._current_mode_text()
        layout = self.layout_combo.currentText()

        # start/end を日付ピッカーから "YYYY-MM-DD" 形式で取得
        if isinstance(self.start_edit, QtWidgets.QDateEdit):
            start = self.start_edit.date().toString("yyyy-MM-dd")
        else:
            start = self.start_edit.text().strip()

        if isinstance(self.end_edit, QtWidgets.QDateEdit):
            end = self.end_edit.date().toString("yyyy-MM-dd")
        else:
            end = self.end_edit.text().strip()

        # ここで ensure_data のログを出す
        self._append_progress(
            f"[gui] ensure_data sym={sym} tf={tf} start={start} end={end} layout={layout}"
        )

        try:
            csv = ensure_data(sym, tf, start, end, env="laptop", layout=layout)
            self._load_plot(csv)  # OHLCVプレビュー
            self.label_meta.setText(f"データOK: {csv}")
        except Exception as e:
            self.label_meta.setText(f"データ更新失敗: {e}")
            self._append_progress(f"[update] error: {e}")

    # ------------------ 実行（QProcess） ------------------
    def _run_test(self):
        # 成果物検証表示をリセット（前回のNGが残らないように）
        if hasattr(self, "output_status_label"):
            self.output_status_label.hide()
            self.output_status_label.setText("")

        # 多重起動防止：既にプロセスが稼働中なら return
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            state_text = {
                QProcess.ProcessState.NotRunning: "NotRunning",
                QProcess.ProcessState.Starting: "Starting",
                QProcess.ProcessState.Running: "Running",
            }.get(self.proc.state(), "Unknown")
            self._append_progress(f"[gui] すでにプロセス稼働中のため、実行をスキップします (state={state_text})")
            return

        sym = self.symbol_edit.text().strip().upper()
        # "USDJPY" → "USDJPY-" に変換（symbol は 'USDJPY-' が正）
        if sym == "USDJPY":
            sym = "USDJPY-"
        tf  = self.tf_combo.currentText()

        # モード文字列（Backtest / Walk-Forward / Overlay）
        mode_text = self._current_mode_text()
        # CLI に渡すモードフラグ（tools.backtest_run 側の想定）
        mode = "wfo" if mode_text == "Walk-Forward" else "bt"

        # 日付は QDateEdit なら date() から、そうでなければ text() から
        if isinstance(self.start_edit, QtWidgets.QDateEdit):
            start = self.start_edit.date().toString("yyyy-MM-dd")
        else:
            start = self.start_edit.text().strip()

        if isinstance(self.end_edit, QtWidgets.QDateEdit):
            end = self.end_edit.date().toString("yyyy-MM-dd")
        else:
            end = self.end_edit.text().strip()

        capital = self.capital_edit.text().strip()
        layout  = self.layout_combo.currentText()
        train_ratio = self.train_ratio_edit.text().strip() or "0.7"

        # 実行前にデータを保証
        try:
            csv = ensure_data(sym, tf, start, end, env="laptop", layout=layout)
        except Exception as e:
            self.label_meta.setText(f"データ不足: {e}")
            self._append_progress(f"[ensure_data] error before run: {e}")
            return

        # 既存プロセスがあれば殺す
        if self.proc:
            self.proc.kill()
            self.proc = None

        # すべてのモードで tools.backtest_run を使用（Walk-Forwardも含む）
        args = [
            "-m", "tools.backtest_run",
            "--csv", str(csv),
            "--start-date", start,
            "--end-date", end,
            "--capital", capital,
            "--mode", mode,
            "--symbol", sym,
            "--timeframe", tf,
            "--layout", layout,
        ]
        if mode == "wfo":
            args += ["--train-ratio", train_ratio]

        self._append_progress("[gui] run: python " + " ".join(args))

        self.proc = QProcess(self)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(args)
        self.proc.setWorkingDirectory(str(PROJECT_ROOT))  # 重要：プロジェクト直下をCWDに

        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self._on_proc_ready_read_stdout)
        self.proc.readyReadStandardError.connect(self._on_proc_ready_read_stderr)
        self.proc.finished.connect(
            lambda code, status: self._on_proc_finished(code, status, sym, tf, mode)
        )
        self.label_meta.setText("実行中…")

        # 実行中はテスト実行ボタンを disable
        self.btn_run.setEnabled(False)

        # ★進捗状態を初期化してタイマー開始
        self._progress_value = 0
        self._progress_target = 0
        self.progress_bar.setValue(0)
        self._progress_timer.start()

        self.proc.start()

        # Walk-Forward の場合は、学習側WFOの最新レポートをコンソールにサマリ表示
        if mode_text == "Walk-Forward":
            try:
                wfo = self._find_latest_wfo_files()
                if wfo is None:
                    print("[WFO] 結果ファイルが見つかりませんでした")
                else:
                    self._debug_print_wfo_summary(wfo)
            except Exception as e:  # 念のためここで例外を潰しておくとGUIごと落ちない
                print(f"[WFO] summary error: {e}")

    def _on_proc_ready_read_stdout(self):
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="ignore")

        for line in data.splitlines():
            if not line:
                continue

            # 1) "done" を検出したら bar を 100% へ強制セット
            if "done" in line.lower() and "[bt]" in line.lower():
                self._progress_target = 100
                self._progress_value = 100
                self.progress_bar.setValue(100)
                if not self._progress_timer.isActive():
                    self._progress_timer.start()

            # 2) bt_progress 行はプログレスバーだけ更新してログには出さない
            if "[bt_progress]" in line:
                m = re.search(r"\[bt_progress\]\s+(\d+)", line)
                if m:
                    try:
                        value = int(m.group(1))
                        value = max(0, min(100, value))
                        # ★ここで直接バーを動かさず、ターゲットだけ上書き
                        if value > self._progress_target:
                            self._progress_target = value
                            # 念のためタイマーが止まっていたら再開
                            if not self._progress_timer.isActive():
                                self._progress_timer.start()
                    except ValueError:
                        pass
                # 進捗行はログに書かない
                continue

            # 3) AISvc.predict 系のエラースパムは Backtest タブでは非表示
            if "AISvc.predict" in line or "app.services.ai_service:predict" in line:
                # コンソール側には出ているので GUI ログではミュート
                continue

            # それ以外だけログに出す
            self._append_progress(line)

    def _on_proc_ready_read_stderr(self):
        data = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="ignore")

        for line in data.splitlines():
            if not line:
                continue

            # 1) "done" を検出したら bar を 100% へ強制セット
            if "done" in line.lower() and "[bt]" in line.lower():
                self._progress_target = 100
                self._progress_value = 100
                self.progress_bar.setValue(100)
                if not self._progress_timer.isActive():
                    self._progress_timer.start()

            # 2) bt_progress が stderr 側に来ることはあまりないはずだけど、
            # 念のため同じ処理を入れておく
            if "[bt_progress]" in line:
                m = re.search(r"\[bt_progress\]\s+(\d+)", line)
                if m:
                    try:
                        value = int(m.group(1))
                        value = max(0, min(100, value))
                        # ★ここで直接バーを動かさず、ターゲットだけ上書き
                        if value > self._progress_target:
                            self._progress_target = value
                            # 念のためタイマーが止まっていたら再開
                            if not self._progress_timer.isActive():
                                self._progress_timer.start()
                    except ValueError:
                        pass
                continue

            # 3) AISvc.predict 系ログをミュート
            if "AISvc.predict" in line or "app.services.ai_service:predict" in line:
                continue

            self._append_progress(line)

    def _on_proc_finished(self, code: int, status: QtCore.QProcess.ExitStatus, sym: str, tf: str, mode: str):
        # 実行完了時にボタンを enable
        self.btn_run.setEnabled(True)

        # ★進捗アニメーションはここで止める
        if self._progress_timer.isActive():
            self._progress_timer.stop()

        # ★内部状態とバーを即 100 にそろえる
        self._progress_target = 100
        self._progress_value = 100
        self.progress_bar.setValue(100)

        # 詳細ログ出力（原因確定用）
        exit_status_text = "Normal" if status == QProcess.ExitStatus.NormalExit else "Crashed"
        error_code = self.proc.error()
        error_string = self.proc.errorString()
        program = self.proc.program()
        arguments = self.proc.arguments()
        proc_state = self.proc.state()
        state_text = {
            QProcess.ProcessState.NotRunning: "NotRunning",
            QProcess.ProcessState.Starting: "Starting",
            QProcess.ProcessState.Running: "Running",
        }.get(proc_state, "Unknown")

        self._append_progress(f"[gui] process finished: code={code}, status={exit_status_text}, state={state_text}")
        self._append_progress(f"[gui] exitStatus: {status} ({exit_status_text})")
        error_code_name = {
            QProcess.ProcessError.FailedToStart: "FailedToStart",
            QProcess.ProcessError.Crashed: "Crashed",
            QProcess.ProcessError.Timedout: "Timedout",
            QProcess.ProcessError.WriteError: "WriteError",
            QProcess.ProcessError.ReadError: "ReadError",
            QProcess.ProcessError.UnknownError: "UnknownError",
        }.get(error_code, f"Unknown({error_code})")
        self._append_progress(f"[gui] error: code={error_code} ({error_code_name}), string={error_string}")
        self._append_progress(f"[gui] program: {program}")
        self._append_progress(f"[gui] arguments: {' '.join(arguments)}")

        if code != 0:
            self.label_meta.setText(f"失敗(code={code}, status={exit_status_text})")
            self._append_progress(f"[gui] process failed code={code}")
            return

        # まずは毎回クリアしておく
        self._wfo_train_df = None
        self._wfo_test_df = None

        # Backtestモード用の latest を先に取得（metrics処理でも使うため）
        out_dir = PROJECT_ROOT / "logs" / "backtest" / sym / tf
        latest_bt_dir = None
        if mode == "bt":
            latest_bt_dir = self._find_latest_bt_dir(out_dir)

        if mode == "wfo":
            # Walk-Forwardモード: WFOデータのみ読み込む（equity_curve.csvは探さない）
            wfo = self._load_latest_wfo_data()
            if wfo:
                self._update_wfo_stats_panel(wfo["metrics"])
                # 最新の overlay データを属性として保持しておく
                self._wfo_train_df = wfo["train"]
                self._wfo_test_df = wfo["test"]

                # WFOのtest equityをメイン描画として表示
                # test equityをequity_dfとして保持
                if wfo["test"] is not None and "equity" in wfo["test"].columns:
                    self._current_equity_df = wfo["test"].copy()
                    if "time" in self._current_equity_df.columns and not is_datetime64_any_dtype(self._current_equity_df["time"]):
                        self._current_equity_df["time"] = pd.to_datetime(self._current_equity_df["time"], errors="coerce")
                    self._current_price_df = None

                    # _load_latest_wfo_data()が読み込んだequity_test.csvのパスを取得
                    wfo_dir = self._find_latest_wfo_dir()
                    if wfo_dir:
                        test_csv = wfo_dir / "equity_test.csv"
                        if test_csv.exists():
                            self.path_edit.setText(str(test_csv))
                            self._load_plot(str(test_csv))
                        else:
                            # CSVファイルが無い場合はDataFrameから一時CSVを作成して描画
                            import tempfile
                            with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
                                wfo["test"].to_csv(f.name, index=False)
                                self.path_edit.setText(f.name)
                                self._load_plot(f.name)
                else:
                    self._current_equity_df = None

                self._overlay_wfo_equity(wfo["train"], wfo["test"])
            else:
                # WFOだけど結果読めなかった → インライン overlay も消す
                self._overlay_wfo_equity(None, None)
                self._current_equity_df = None
                self._wfo_train_df = None
                self._wfo_test_df = None
                self.label_meta.setText("WFO結果が見つかりませんでした")
        else:
            # Backtestモード: equity_curve.csvを読み込む
            if latest_bt_dir is None:
                self.label_meta.setText(f"backtest_* フォルダが見つかりません: {out_dir}")
                self._append_progress(f"[gui] missing backtest_* dir: {out_dir}")
                return

            out_csv = latest_bt_dir / "equity_curve.csv"
            self.path_edit.setText(str(out_csv))

            if not out_csv.exists():
                self.label_meta.setText(f"出力CSVが見つかりません: {out_csv}")
                self._append_progress(f"[gui] missing file: {out_csv}")
                return

            self._load_plot(out_csv)  # Equity描画（この中で_current_equity_dfが設定される）
            # Backtestモードなど → overlay は全部クリア
            self._overlay_wfo_equity(None, None)
            self._wfo_train_df = None
            self._wfo_test_df = None

        # 新しい実装では、PlotWindowの__init__で既にデータを受け取って描画しているため、
        # overlay_wfo_equityの呼び出しは不要（互換性のためにメソッドは残している）

        # metrics と monthly_returns の処理（Backtestモードの場合は latest_bt_dir を起点にする）
        if mode == "bt":
            # Backtestモード: 最新の期間フォルダを起点にする
            if latest_bt_dir is None:
                self._append_progress(f"[gui] WARNING: backtest_* dir not found for metrics: {out_dir}")
                metrics_path = out_dir / "metrics.json"  # フォールバック
                monthly_returns_path = out_dir / "monthly_returns.csv"  # フォールバック
            else:
                metrics_path = latest_bt_dir / "metrics.json"
                monthly_returns_path = latest_bt_dir / "monthly_returns.csv"
        else:
            # WFOモード: 従来通り out_dir を起点にする
            metrics_path = out_dir / ("metrics.json" if mode=="bt" else "metrics_wfo.json")
            monthly_returns_path = out_dir / ("monthly_returns.csv" if mode=="bt" else "monthly_returns_test.csv")

        self._load_metrics(metrics_path)

        # 月次リターン保存パスを控える
        self._last_monthly_returns = monthly_returns_path

        # ▼ monthly_returns 再計算（Backtestモードのみ）
        if mode != "wfo":
            if latest_bt_dir is not None:
                out_csv = latest_bt_dir / "equity_curve.csv"
            else:
                out_csv = out_dir / "equity_curve.csv"  # フォールバック

            if out_csv.exists():
                try:
                    profile = self._profile_name
                    out_monthly_csv = Path("backtests") / profile / "monthly_returns.csv"
                    out_monthly_csv.parent.mkdir(parents=True, exist_ok=True)

                    # 強制的に monthly_returns.csv を生成
                    compute_monthly_returns(str(out_csv), str(out_monthly_csv))
                    self._append_progress(f"[gui] 集約 monthly_returns を更新しました: {out_monthly_csv}")
                except Exception as e:
                    self._append_progress(f"[gui] 集約 monthly_returns 更新に失敗しました: {e!r}")

        # decisions.jsonl の読み込み処理（あれば AIタブへ連動）
        decisions_jsonl = out_dir / "decisions.jsonl"
        if decisions_jsonl.exists():
            try:
                # AIタブへの連動処理（必要に応じて実装）
                # 現時点ではログ出力のみ
                self._append_progress(f"[gui] decisions.jsonl を検出しました: {decisions_jsonl}")
                # TODO: AIタブへの連動処理を追加（必要に応じて）
            except Exception as e:
                self._append_progress(f"[gui] decisions.jsonl 処理に失敗しました: {e!r}")

        # === monthly_returns を再読込（最後に統一） ===
        try:
            df = self._kpi_service.refresh_monthly_returns(self._profile_name)
            self._append_progress(
                f"[gui] monthly_returns を再読込しました。行数: {len(df)}"
            )
        except Exception as e:
            self._append_progress(f"[gui] monthly_returns 再読込に失敗しました: {e!r}")

        self.label_meta.setText("完了")

    # ------------------ ユーティリティUI ------------------
    def _pick_file(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Equity CSV", str(PROJECT_ROOT), "CSV (*.csv)")
        if p:
            self.path_edit.setText(p)
            self._load_plot(p)

    def _save_png(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save PNG", str(PROJECT_ROOT / "logs"), "PNG (*.png)")
        if not p: return
        try:
            self.fig.savefig(p, dpi=150)
            self._append_progress(f"[gui] saved png: {p}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "保存失敗", str(e))

    def _export_result_json(self):
        # 画面パラメータ＋メトリクス＋モデル情報をまとめて保存
        sym = self.symbol_edit.text().strip().upper()
        # "USDJPY" → "USDJPY-" に変換（symbol は 'USDJPY-' が正）
        if sym == "USDJPY":
            sym = "USDJPY-"
        tf  = self.tf_combo.currentText()
        mode = self._current_mode_text()
        payload = {
            "params": {
                "symbol": sym, "timeframe": tf,
                "mode": mode,
                "start": self.start_edit.text().strip(),
                "end": self.end_edit.text().strip(),
                "capital": float(self.capital_edit.text().strip() or 0),
                "layout": self.layout_combo.currentText(),
                "train_ratio": float(self.train_ratio_edit.text().strip() or 0.7)
            },
            "model": self.model_info.text(),
        }
        # metrics 表からも収集
        for r in range(self.table.rowCount()):
            k = self.table.item(r,0).text()
            v = self.table.item(r,1).text()
            payload.setdefault("metrics", {})[k] = v

        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export JSON", str(PROJECT_ROOT / "logs"), "JSON (*.json)")
        if not p: return
        Path(p).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_progress(f"[gui] exported: {p}")

    def _show_heatmap(self):
        if not self._last_monthly_returns or not self._last_monthly_returns.exists():
            QtWidgets.QMessageBox.information(
                self,
                "情報",
                "月次リターンがまだありません。先にテスト実行してください。"
            )
            return
        try:
            df = pd.read_csv(self._last_monthly_returns)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "エラー",
                f"月次リターンCSVの読み込みに失敗しました。\n{e}"
            )
            self._append_progress(f"[gui] heatmap load error: {e}")
            return

        if self._pop is None:
            self._pop = PlotWindow(self)
        self.plot_window = self._pop
        self._pop.show(); self._pop.raise_(); self._pop.activateWindow()
        note = self._last_monthly_returns.name
        try:
            self._pop.plot_heatmap(df, note)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "エラー",
                f"ヒートマップ描画中にエラーが発生しました。\n{e}"
            )
            self._append_progress(f"[gui] heatmap plot error: {e}")

    def _pop_out(self):
        if self._last_plot_kind is None:
            self._append_progress("[gui] popout: 直近の描画データがありません")
            QtWidgets.QMessageBox.information(self, "情報", "まだ描画されていません。先に「データ確認＆更新」または「テスト実行」をしてください。")
            return

        # 現在のmodeを取得
        mode_text = self._current_mode_text()
        mode = "wfo" if mode_text == "Walk-Forward" else "bt"

        # データを準備
        equity_df = self._current_equity_df
        price_df = self._current_price_df
        wfo_train_df = self._wfo_train_df
        wfo_test_df = self._wfo_test_df

        # 新ウインドウを生成（既存の場合は閉じて新規作成）
        if self._pop is not None:
            try:
                self._pop.close()
            except Exception:
                pass

        # データのみを新ウインドウに渡す（Figure/Axesは渡さない）
        # 直近表示種別とCSVパスも渡す（期間ジャンプ用）
        self._pop = PlotWindow(
            parent=self,
            mode=mode,
            equity_df=equity_df,
            price_df=price_df,
            wfo_train_df=wfo_train_df,
            wfo_test_df=wfo_test_df,
            last_view_kind=self._last_plot_kind,
            last_csv=self._last_plot_data,
        )
        self.plot_window = self._pop
        self._pop.show()
        self._pop.raise_()
        self._pop.activateWindow()

    # ------------------ 描画・メトリクス ------------------
    def _load_plot(self, path_or_csv):
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

                plot_equity_with_markers_to_figure(self.fig, csv_path, note=p.name)

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

