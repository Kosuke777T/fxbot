# app/gui/widgets/kpi_dashboard.py
# 正式KPIダッシュボード（T-10 STEP2）
from __future__ import annotations
from typing import Optional
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QProgressBar,
    QFormLayout,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from app.services.kpi_service import KPIService




class KPIDashboardWidget(QWidget):
    """
    正式KPIダッシュボード（v5.1 仕様準拠）

    - 12ヶ月折れ線グラフ（月次return_pct）
    - 3%に対する進捗ゲージ（progress_pct）
    - 今月のリターン（current_month_return）
    - 最大DD（max_dd_pct）
    - PF平均（avg_pf）
    """

    def __init__(self, profile: str = "std", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.profile = profile
        self.kpi_service = KPIService()

        # メインレイアウト
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # タイトルとReloadボタン
        header_layout = QHBoxLayout()
        title_label = QLabel("月次KPIダッシュボード")
        title_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self.btn_reload = QPushButton("Reload")
        self.btn_reload.clicked.connect(self.refresh)

        header_layout.addWidget(title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.btn_reload)
        main_layout.addLayout(header_layout)

        # データなしメッセージ（初期は非表示）
        self.lbl_no_data = QLabel("データがありません")
        self.lbl_no_data.setStyleSheet("color: gray; font-size: 12pt; padding: 20px;")
        self.lbl_no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_no_data.hide()
        main_layout.addWidget(self.lbl_no_data)

        # グラフエリア
        self.figure = Figure(figsize=(10, 4), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(300)
        main_layout.addWidget(self.canvas, 1)

        # 進捗ゲージとKPIカード
        metrics_layout = QHBoxLayout()

        # 左側: 進捗ゲージ
        progress_group = QGroupBox("今月の進捗（目標: 3%）")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 200)  # 0〜200%
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid grey;
                border-radius: 5px;
                text-align: center;
                font-size: 12pt;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
            }
        """)

        self.lbl_current_return = QLabel("今月のリターン: ---")
        self.lbl_current_return.setAlignment(Qt.AlignmentFlag.AlignCenter)

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.lbl_current_return)
        metrics_layout.addWidget(progress_group, 1)

        # 右側: KPIカード
        kpi_group = QGroupBox("KPI指標")
        kpi_form = QFormLayout(kpi_group)

        self.lbl_max_dd = QLabel("---")
        self.lbl_avg_pf = QLabel("---")
        self.lbl_win_rate = QLabel("---")
        self.lbl_pf = QLabel("---")
        self.lbl_avg_rr = QLabel("---")
        self.lbl_total_trades = QLabel("---")

        kpi_form.addRow("最大DD（12ヶ月）", self.lbl_max_dd)
        kpi_form.addRow("PF平均（月次）", self.lbl_avg_pf)
        kpi_form.addRow("勝率", self.lbl_win_rate)
        kpi_form.addRow("PF（全トレード）", self.lbl_pf)
        kpi_form.addRow("平均RR", self.lbl_avg_rr)
        kpi_form.addRow("総トレード数", self.lbl_total_trades)
        metrics_layout.addWidget(kpi_group, 1)

        main_layout.addLayout(metrics_layout)

        # 初回ロード
        self.refresh()

    def refresh(self) -> None:
        """KPIServiceからデータを取得してダッシュボードを更新"""
        try:
            data = self.kpi_service.load_backtest_kpi_summary(self.profile)
        except Exception as e:
            print(f"[KPIDashboard] load_backtest_kpi_summary error: {e}")
            self._show_no_data()
            return

        if not data.get("has_backtest", False):
            self._show_no_data()
            return

        self._show_data(data)

    def _show_no_data(self) -> None:
        """データがない場合の表示"""
        self.lbl_no_data.show()
        self.canvas.hide()
        self.progress_bar.hide()
        self.lbl_current_return.hide()
        self.lbl_max_dd.setText("---")
        self.lbl_avg_pf.setText("---")
        self.lbl_win_rate.setText("---")
        self.lbl_pf.setText("---")
        self.lbl_avg_rr.setText("---")
        self.lbl_total_trades.setText("---")

    def _show_data(self, data: dict) -> None:
        """データがある場合の表示"""
        self.lbl_no_data.hide()
        self.canvas.show()
        self.progress_bar.show()
        self.lbl_current_return.show()

        # グラフ描画
        self._draw_chart(data)

        # 進捗ゲージ（current_month_progress は 0.0〜2.0、×100 してパーセント表示）
        progress = data.get("current_month_progress", 0.0)
        progress_value = int(progress * 100)  # 0.0〜2.0 → 0〜200
        self.progress_bar.setValue(min(max(progress_value, 0), 200))

        # 今月のリターン
        current_return = data.get("current_month_return", 0.0)
        self.lbl_current_return.setText(
            f"今月のリターン: {current_return * 100:.2f}%"
        )

        # 最大DD（12ヶ月の monthly から計算）
        monthly = data.get("monthly", [])
        if monthly:
            max_dd_pct = min(m["max_dd_pct"] for m in monthly)
            self.lbl_max_dd.setText(f"{max_dd_pct:.2f}%")
        else:
            self.lbl_max_dd.setText("N/A")

        # PF平均（12ヶ月の monthly から計算、0より大きいもののみ）
        if monthly:
            pf_vals = [m["pf"] for m in monthly if m["pf"] > 0]
            if pf_vals:
                avg_pf = sum(pf_vals) / len(pf_vals)
                self.lbl_avg_pf.setText(f"{avg_pf:.2f}")
            else:
                self.lbl_avg_pf.setText("N/A")
        else:
            self.lbl_avg_pf.setText("N/A")

    def set_trade_stats(self, win_rate: float, pf: float, avg_rr: float, total_trades: int) -> None:
        """
        トレード統計を表示する。

        Parameters
        ----------
        win_rate : float
            勝率（0.0〜1.0）
        pf : float
            プロフィットファクター（無限大の場合は float("inf")）
        avg_rr : float
            平均リスクリワード比
        total_trades : int
            総トレード数
        """
        self.lbl_win_rate.setText(f"{win_rate * 100:.1f}%")
        if pf == float("inf"):
            self.lbl_pf.setText("∞")
        else:
            self.lbl_pf.setText(f"{pf:.2f}")
        self.lbl_avg_rr.setText(f"{avg_rr:.2f}")
        self.lbl_total_trades.setText(str(total_trades))

    def _draw_chart(self, data: dict) -> None:
        """12ヶ月折れ線グラフを描画"""
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        monthly = data.get("monthly", [])
        if not monthly:
            ax.set_title("Monthly Returns (no data)")
            ax.axhline(0.0, linestyle="--", linewidth=1.0, color="gray")
            self.canvas.draw_idle()
            return

        months = [m["year_month"] for m in monthly]
        returns = [m["return_pct"] for m in monthly]
        target = 0.03  # 固定値

        if not months or not returns:
            ax.set_title("Monthly Returns (no data)")
            ax.axhline(0.0, linestyle="--", linewidth=1.0, color="gray")
            self.canvas.draw_idle()
            return

        # 月ラベル（短縮形: "2025-01" → "01"）
        month_labels = [m.split("-")[1] if "-" in m else m for m in months]

        # 折れ線グラフ
        x = range(len(months))
        ax.plot(x, [r * 100 for r in returns], marker="o", linewidth=2, markersize=6, label="月次リターン")

        # 3%目標線
        ax.axhline(target * 100, color="red", linestyle="--", linewidth=2, label="目標 3%")

        # 0%ライン
        ax.axhline(0.0, color="black", linestyle="--", linewidth=1, alpha=0.5)

        # X軸設定
        ax.set_xticks(x)
        ax.set_xticklabels(month_labels, rotation=45, ha="right")
        ax.set_xlabel("月")
        ax.set_ylabel("月次リターン [%]")
        ax.set_title("過去12ヶ月の月次リターン")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper left")

        # Y軸範囲を適切に設定
        if returns:
            y_min = min([r * 100 for r in returns] + [0.0, -10.0])
            y_max = max([r * 100 for r in returns] + [target * 100, 10.0])
            ax.set_ylim(y_min - 1.0, y_max + 2.0)

        self.canvas.draw_idle()
