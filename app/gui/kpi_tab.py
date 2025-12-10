# app/gui/kpi_tab.py
from __future__ import annotations

from typing import Optional
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from app.gui.widgets.kpi_dashboard import KPIDashboardWidget
from app.services.kpi_service import KPIService
from app.services.edition_guard import get_capability


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

        # EditionGuard による表示制御（将来の拡張用）
        # Expert 以上のみ追加要素を表示する場合はここで制御
        self._expert_level = get_capability("shap_level") or 0
        # 現在は KPIDashboardWidget のみ表示（EditionGuard 制御は将来の拡張用）

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

        # 既存のダッシュボード更新（ゲージ、折れ線グラフなど）
        self.kpi_dashboard.refresh()

        # トレード統計を取得して表示
        try:
            trade_stats = self.kpi_service.compute_trade_stats(self.profile_name)
            win_rate = trade_stats.get("win_rate", 0.0)
            pf = trade_stats.get("pf", 0.0)
            avg_rr = trade_stats.get("avg_rr", 0.0)
            total_trades = trade_stats.get("total_trades", 0)

            self.kpi_dashboard.set_trade_stats(
                win_rate=win_rate,
                pf=pf,
                avg_rr=avg_rr,
                total_trades=total_trades,
            )
        except Exception as e:
            print(f"[KPITab] compute_trade_stats error: {e}")
            # エラー時はデフォルト値を設定
            self.kpi_dashboard.set_trade_stats(
                win_rate=0.0,
                pf=0.0,
                avg_rr=0.0,
                total_trades=0,
            )

