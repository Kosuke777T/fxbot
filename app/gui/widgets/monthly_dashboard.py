from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGroupBox
from PyQt6.QtCore import Qt

from app.gui.widgets.monthly_returns_widget import MonthlyReturnsWidget
from app.services import kpi_service as kpi_mod


class MonthlyDashboardGroup(QGroupBox):
    """月次3%ダッシュボード（AIタブ用）"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("月次 3% ダッシュボード", parent)
        self.setMinimumHeight(320)

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # KPI ラベル
        self.label_kpi = QLabel("今月: ---% / 目標: 3% / 達成率: ---")
        self.label_kpi.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # グラフ（既存）
        self.monthly_widget = MonthlyReturnsWidget(self)
        self.monthly_widget.setMinimumHeight(260)

        layout.addWidget(self.label_kpi)
        layout.addWidget(self.monthly_widget, stretch=1)

        self.setLayout(layout)

    # -----------------------------------------------------
    # ダッシュボード更新
    # -----------------------------------------------------
    def refresh(self) -> None:
        """今月リターン（backtest + live があれば）と3%目標の比較、グラフ更新"""

        # 1) 目標月次リターン [%]（バックテストKPIから取得）
        goal_pct = 3.0  # フォールバック
        try:
            summary = kpi_mod.load_backtest_kpi_summary()
            # target_monthly_return_pct は「%単位」（例: 3.0）
            goal_pct = float(getattr(summary, "target_monthly_return_pct", goal_pct))
        except Exception as e:
            print("[MonthlyDashboard] load_backtest_kpi_summary error:", e)

        # 2) 今月リターン [%]（バックテスト＋runtime/metrics.json があれば）
        cur_pct = 0.0
        try:
            kpi_dict: dict = {}

            # パターン1: KPIService クラスが実装されている場合
            if hasattr(kpi_mod, "KPIService"):
                svc = kpi_mod.KPIService()
                kpi_dict = svc.get_kpi()

            # パターン2: モジュール関数 get_kpi() がある場合
            elif hasattr(kpi_mod, "get_kpi"):
                kpi_dict = kpi_mod.get_kpi()

            # どちらかで取れたら current_month_return_pct を使う
            if kpi_dict:
                cur_pct = float(kpi_dict.get("current_month_return_pct", 0.0))

        except Exception as e:
            print("[MonthlyDashboard] KPI fetch error:", e)

        ach_pct = (cur_pct / goal_pct * 100.0) if goal_pct != 0 else 0.0

        self.label_kpi.setText(
            f"今月: {cur_pct:.2f}% / 目標: {goal_pct:.1f}% / 達成率: {ach_pct:.1f}%"
        )

        # 3) グラフ更新（月次リターン系列）
        try:
            self.monthly_widget.refresh()
        except Exception as e:
            print("[MonthlyDashboard] monthly_widget.refresh error:", e)
