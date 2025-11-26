# app/gui/ai_tab.py
from __future__ import annotations
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QPushButton,
    QHBoxLayout,
    QTabWidget,
)

import joblib
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from loguru import logger

from app.services.ai_service import AISvc
from app.services.recent_kpi import compute_recent_kpi_from_decisions
from app.gui.widgets.feature_importance import FeatureImportanceWidget
from app.gui.widgets.shap_bar import ShapBarWidget
from app.gui.widgets.monthly_returns_widget import MonthlyReturnsWidget
from app.core.strategy_profile import get_profile


class AITab(QWidget):
    def __init__(self, ai_service: AISvc | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ai_service = ai_service or AISvc()
        self.profile = get_profile()

        try:
            p = Path("models/LightGBM_clf.pkl")
            if p.exists():
                obj = joblib.load(p)
                model = obj.get("model", obj) if isinstance(obj, dict) else obj
                self.ai_service.models.setdefault("lgbm_cls", model)
        except Exception as e:
            print(f"[AITab] model autoload skipped: {e}")

        main_layout = QVBoxLayout(self)
        self.tab_widget = QTabWidget(self)
        main_layout.addWidget(self.tab_widget, 1)

        self.tab_kpi = QWidget(self.tab_widget)
        kpi_layout = QVBoxLayout(self.tab_kpi)

        self.recent_kpi_group = QGroupBox("Recent Trades KPI", self.tab_kpi)
        kpi_form = QFormLayout(self.recent_kpi_group)

        self.spin_recent_n = QSpinBox(self.recent_kpi_group)
        self.spin_recent_n.setRange(1, 100000)
        self.spin_recent_n.setValue(100)

        self.lbl_n_trades = QLabel("-", self.recent_kpi_group)
        self.lbl_win_rate = QLabel("-", self.recent_kpi_group)
        self.lbl_profit_factor = QLabel("-", self.recent_kpi_group)
        self.lbl_max_drawdown = QLabel("0.0", self.recent_kpi_group)
        self.lbl_max_dd_ratio = QLabel("N/A", self.recent_kpi_group)
        self.lbl_best_streaks = QLabel("0 / 0", self.recent_kpi_group)

        self.btn_refresh_kpi = QPushButton("Refresh", self.recent_kpi_group)

        kpi_form.addRow("Window (N trades)", self.spin_recent_n)
        kpi_form.addRow("Total trades", self.lbl_n_trades)
        kpi_form.addRow("Win rate", self.lbl_win_rate)
        kpi_form.addRow("Profit factor", self.lbl_profit_factor)
        kpi_form.addRow("Max drawdown", self.lbl_max_drawdown)
        kpi_form.addRow("Max DD ratio", self.lbl_max_dd_ratio)
        kpi_form.addRow("Best win / loss streak", self.lbl_best_streaks)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_refresh_kpi)
        kpi_form.addRow(btn_row)

        # --- 月次リターン（backtest）を表示するグラフ ---
        self.monthly_group = QGroupBox("Monthly returns (backtest, %)", self.tab_kpi)
        monthly_layout = QVBoxLayout(self.monthly_group)
        self.monthly_widget = MonthlyReturnsWidget(self.monthly_group)
        monthly_layout.addWidget(self.monthly_widget)

        kpi_layout.addWidget(self.recent_kpi_group)
        kpi_layout.addWidget(self.monthly_group)
        kpi_layout.addStretch(1)

        self.tab_widget.addTab(self.tab_kpi, "KPI")

        # --- Monthly Returns tab (matplotlib chart) ---
        self.tab_monthly = QWidget(self.tab_widget)
        monthly_layout = QVBoxLayout(self.tab_monthly)

        self.fig_monthly = Figure(figsize=(6, 4))
        self.canvas_monthly = FigureCanvas(self.fig_monthly)
        monthly_layout.addWidget(self.canvas_monthly)

        self.tab_widget.addTab(self.tab_monthly, "Monthly Returns")

        self.tab_fi = QWidget(self.tab_widget)
        fi_layout = QVBoxLayout(self.tab_fi)
        self.feature_importance = FeatureImportanceWidget(self.ai_service, self.tab_fi)
        fi_layout.addWidget(self.feature_importance)
        self.tab_widget.addTab(self.tab_fi, "Feature Importance")

        self.tab_shap = QWidget(self.tab_widget)
        shap_layout = QVBoxLayout(self.tab_shap)

        self.shap_group = QGroupBox("SHAP Global Importance", self.tab_shap)
        shap_group_layout = QVBoxLayout(self.shap_group)

        self.shap_widget = ShapBarWidget(self.ai_service, self.shap_group)
        shap_group_layout.addWidget(self.shap_widget)

        shap_layout.addWidget(self.shap_group)
        shap_layout.addStretch(1)

        self.tab_widget.addTab(self.tab_shap, "SHAP")

        self.btn_refresh_kpi.clicked.connect(self.refresh_kpi)
        self.refresh_kpi()
        self._update_monthly_returns_chart()

    def refresh_kpi(self) -> None:
        """
        recent_kpi.compute_recent_kpi_from_decisions を呼び出し、
        ラベルに KPI を表示する。
        """
        limit = self.spin_recent_n.value()

        try:
            result = compute_recent_kpi_from_decisions(
                limit=limit,
                starting_equity=100000.0,
            )
        except Exception as e:
            self.lbl_n_trades.setText("Error")
            self.lbl_win_rate.setText("Error")
            self.lbl_profit_factor.setText("Error")
            self.lbl_max_drawdown.setText("Error")
            self.lbl_max_dd_ratio.setText("Error")
            self.lbl_best_streaks.setText("Error")

            print(f"[AITab] refresh_kpi error: {e!r}")
            return

        self.lbl_n_trades.setText(str(result.n_trades))

        if result.win_rate is not None:
            self.lbl_win_rate.setText(f"{result.win_rate * 100:.1f} %")
        else:
            self.lbl_win_rate.setText("N/A")

        if result.profit_factor is not None:
            self.lbl_profit_factor.setText(f"{result.profit_factor:.2f}")
        else:
            self.lbl_profit_factor.setText("N/A")

        self.lbl_max_drawdown.setText(f"{result.max_drawdown:.1f}")

        if result.max_drawdown_ratio is not None:
            self.lbl_max_dd_ratio.setText(f"{result.max_drawdown_ratio * 100:.2f} %")
        else:
            self.lbl_max_dd_ratio.setText("N/A")

        self.lbl_best_streaks.setText(
            f"{result.best_win_streak} / {result.best_loss_streak}"
        )

        # SHAP はウィジェット初期化時の refresh に任せる

    # ------------------------------------------------------
    # Monthly Returns グラフ更新（NEW）
    # ------------------------------------------------------
    def _update_monthly_returns_chart(self) -> None:
        """
        backtests/{profile}/monthly_returns.csv から
        月次リターン（％）の折れ線グラフを描画する。
        """
        if not hasattr(self, "fig_monthly") or not hasattr(self, "canvas_monthly"):
            return  # __init__ 途中で呼ばれた場合の保険

        self.fig_monthly.clear()
        ax = self.fig_monthly.add_subplot(111)

        path = self.profile.monthly_returns_path

        if not path.exists():
            ax.text(
                0.5,
                0.5,
                "monthly_returns.csv not found",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            ax.set_axis_off()
            self.canvas_monthly.draw_idle()
            return

        try:
            df = pd.read_csv(path)
        except Exception as e:  # pragma: no cover - GUI用の保険
            ax.text(
                0.5,
                0.5,
                f"read error: {e}",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            ax.set_axis_off()
            self.canvas_monthly.draw_idle()
            return

        required = {"year", "month", "return_pct"}
        if df.empty or not required.issubset(df.columns):
            ax.text(
                0.5,
                0.5,
                "no monthly returns",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            ax.set_axis_off()
            self.canvas_monthly.draw_idle()
            return

        # 年月順に並べる
        df = df.sort_values(["year", "month"])
        years = df["year"].astype(int).tolist()
        months = df["month"].astype(int).tolist()
        rets = df["return_pct"].astype(float).tolist()

        labels = [f"{y}-{m}" for y, m in zip(years, months)]

        ax.plot(labels, rets, marker="o")

        # KPI目標は「月次 +3%」で固定
        target_line = 3.0
        ax.axhline(target_line, linestyle="--", linewidth=1.0)

        ax.set_title("Monthly Returns (%)")  # 英語にしてフォント警告を回避
        ax.set_ylabel("Return [%]")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right")

        self.fig_monthly.tight_layout()
        self.canvas_monthly.draw_idle()
