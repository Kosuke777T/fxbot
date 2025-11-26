# app/gui/widgets/monthly_returns_widget.py
from __future__ import annotations

from typing import Optional
from pathlib import Path

import pandas as pd
from PyQt6 import QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from app.core.strategy_profile import get_profile


class MonthlyReturnsWidget(QtWidgets.QWidget):
    """
    backtests/{profile}/monthly_returns.csv を読み込んで、
    月次リターン（％）＋ 3％ ラインを表示するシンプルなウィジェット。

    前提:
      - StrategyProfile.monthly_returns_path が
        backtests/{profile}/monthly_returns.csv を指している。
      - CSV には最低限 year, month, return_pct 列が含まれている。
        return_pct は「％」単位（+3.0 なら +3％）を想定。
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self.figure = Figure(figsize=(6, 3), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)

        self.btn_reload = QtWidgets.QPushButton("Reload")
        self.lbl_info = QtWidgets.QLabel("")
        self.lbl_info.setWordWrap(True)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_reload)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(btn_row)
        layout.addWidget(self.lbl_info)

        self.btn_reload.clicked.connect(self.refresh)

        # 初回ロード
        self.refresh()

    # ----------------- データ読み込みまわり -----------------

    def _load_monthly_df(self) -> Optional[pd.DataFrame]:
        """
        StrategyProfile から monthly_returns.csv を探して読み込む。
        問題があれば None を返して lbl_info にメッセージを出す。
        """
        try:
            profile = get_profile()
        except Exception as e:
            self.lbl_info.setText(f"プロファイル取得エラー: {e}")
            return None

        path: Path = profile.monthly_returns_path
        if not path.exists():
            self.lbl_info.setText(f"monthly_returns.csv が見つかりません: {path}")
            return None

        try:
            df = pd.read_csv(path)
        except Exception as e:
            self.lbl_info.setText(f"CSV 読み込みエラー: {e}")
            return None

        required = {"year", "month", "return_pct"}
        missing = required.difference(df.columns)
        if missing:
            self.lbl_info.setText(f"monthly_returns.csv に必要列が不足: {missing}")
            return None

        df = df.copy()
        # 数値化 & 欠損除去
        df["year"] = pd.to_numeric(df["year"], errors="coerce")
        df["month"] = pd.to_numeric(df["month"], errors="coerce")
        df["return_pct"] = pd.to_numeric(df["return_pct"], errors="coerce")

        df = df.dropna(subset=["year", "month", "return_pct"])
        if df.empty:
            self.lbl_info.setText("月次リターンの有効な行がありません。")
            return None

        # ラベル（YYYY-MM）
        df["year"] = df["year"].astype(int)
        df["month"] = df["month"].astype(int)
        df["label"] = df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2)

        return df

    # ----------------- 描画 -----------------

    def refresh(self) -> None:
        """
        monthly_returns.csv を読み込み直してグラフを更新。
        return_pct（％）の棒グラフ + 3％ ライン + 0％ ライン。
        """
        df = self._load_monthly_df()

        self.figure.clear()
        ax = self.figure.add_subplot(111)

        if df is None or df.empty:
            ax.set_title("Monthly returns (no data)")
            ax.axhline(0.0, linestyle="--", linewidth=1.0)
            self.canvas.draw_idle()
            return

        x = range(len(df))
        y = df["return_pct"].values
        labels = df["label"].tolist()

        # 棒グラフ
        ax.bar(x, y)

        # 0％ ライン
        ax.axhline(0.0, color="black", linestyle="--", linewidth=1.0)
        # 3％ ライン
        ax.axhline(3.0, color="red", linestyle="--", linewidth=1.0, label="Target 3%")

        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("Monthly return [%]")
        ax.set_title("Monthly returns vs 3% target")

        # 凡例は 3％ ラインだけ
        ax.legend(loc="upper left")

        # ちょっと余裕を持って y 範囲を設定
        ymin = min(y.min(), 0.0, -10.0)
        ymax = max(y.max(), 3.0, 10.0)
        if ymin == ymax:
            ymin -= 1.0
            ymax += 1.0
        ax.set_ylim(ymin - 1.0, ymax + 1.0)

        self.lbl_info.setText(
            f"データ件数: {len(df)} 期間: {labels[0]} 〜 {labels[-1]}"
        )

        self.canvas.draw_idle()
