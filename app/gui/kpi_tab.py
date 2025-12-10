# app/gui/kpi_tab.py
from __future__ import annotations

from typing import Optional
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from app.gui.widgets.kpi_dashboard import KPIDashboardWidget
from app.services.kpi_service import KPIService


class KPITab(QWidget):
    """
    運用KPIタブ（メインタブ）

    BacktestRun の monthly_returns.csv を元に成績を表示する。
    KPIDashboardWidget を使用して表示する。
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        kpi_service: Optional[KPIService] = None,
        profile_name: str = "michibiki_std",
    ) -> None:
        super().__init__(parent)
        self.kpi_service = kpi_service or KPIService()
        self.profile_name = profile_name

        # メインレイアウト
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # KPIDashboardWidget を使用
        self.kpi_dashboard = KPIDashboardWidget(profile=profile_name, parent=self)
        # KPIDashboardWidget 内の KPIService を共有インスタンスに置き換え
        self.kpi_dashboard.kpi_service = self.kpi_service

        layout.addWidget(self.kpi_dashboard)

    def refresh(self, profile: Optional[str] = None) -> None:
        """
        KPIダッシュボードを更新する。

        Parameters
        ----------
        profile : str, optional
            プロファイル名。指定しない場合は self.profile_name を使用。
        """
        if profile is not None:
            self.profile_name = profile
            self.kpi_dashboard.profile = profile
        self.kpi_dashboard.refresh()

