# app/gui/kpi_tab.py
from __future__ import annotations

from typing import Optional
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QFormLayout,
)
from app.gui.widgets.kpi_dashboard import KPIDashboardWidget
from app.services.kpi_service import KPIService
from app.services.edition_guard import get_capability
from app.services.ops_history_service import get_ops_history_service


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

        # 再学習実績セクション（上部）
        self.ops_stats_group = QGroupBox("再学習実績", self)
        ops_stats_layout = QFormLayout(self.ops_stats_group)

        self.lbl_week_stats = QLabel("-", self.ops_stats_group)
        self.lbl_month_stats = QLabel("-", self.ops_stats_group)
        self.lbl_consecutive_failures = QLabel("-", self.ops_stats_group)
        self.lbl_last_model_update = QLabel("-", self.ops_stats_group)

        ops_stats_layout.addRow("週次:", self.lbl_week_stats)
        ops_stats_layout.addRow("月次:", self.lbl_month_stats)
        ops_stats_layout.addRow("連続失敗:", self.lbl_consecutive_failures)
        ops_stats_layout.addRow("最終モデル更新:", self.lbl_last_model_update)

        layout.addWidget(self.ops_stats_group)

        # KPIDashboardWidget を使用
        self.kpi_dashboard = KPIDashboardWidget(profile=profile_name, parent=self)
        # KPIDashboardWidget 内の KPIService を共有インスタンスに置き換え
        self.kpi_dashboard.kpi_service = self.kpi_service

        layout.addWidget(self.kpi_dashboard)

        # EditionGuard による表示制御（将来の拡張用）
        # Expert 以上のみ追加要素を表示する場合はここで制御
        self._expert_level = get_capability("shap_level") or 0
        # 現在は KPIDashboardWidget のみ表示（EditionGuard 制御は将来の拡張用）

        # 初期表示時に再学習実績を更新
        self._update_ops_stats()

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

        # 再学習実績を更新
        self._update_ops_stats()

    def _update_ops_stats(self) -> None:
        """再学習実績を更新する。"""
        try:
            history_service = get_ops_history_service()
            # 現在選択中のsymbolを取得（プロファイルから推定、またはNone）
            symbol = None  # 必要に応じてプロファイルからsymbolを取得
            summary = history_service.summarize_ops_history(symbol=symbol)

            # 週次統計
            week_total = summary.get("week_total", 0)
            week_ok_rate = summary.get("week_ok_rate", 0.0)
            week_model_updates = summary.get("week_model_updates", 0)
            week_text = f"{week_total}件 / OK率 {week_ok_rate:.0%} / 更新 {week_model_updates}回"
            self.lbl_week_stats.setText(week_text)

            # 月次統計
            month_total = summary.get("month_total", 0)
            month_ok_rate = summary.get("month_ok_rate", 0.0)
            month_model_updates = summary.get("month_model_updates", 0)
            month_text = f"{month_total}件 / OK率 {month_ok_rate:.0%} / 更新 {month_model_updates}回"
            self.lbl_month_stats.setText(month_text)

            # 連続失敗
            consecutive_failures = summary.get("consecutive_failures", 0)
            self.lbl_consecutive_failures.setText(f"{consecutive_failures}回")

            # 最終モデル更新
            last_model_update = summary.get("last_model_update")
            if last_model_update:
                started_at = last_model_update.get("started_at", "")
                if started_at:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        time_str = started_at[:19] if len(started_at) >= 19 else started_at
                else:
                    time_str = "-"

                model_path = last_model_update.get("model_path") or ""
                # 長いパスの場合は末尾だけ表示
                if model_path and len(model_path) > 50:
                    model_path = "..." + model_path[-47:]

                if model_path:
                    update_text = f"{time_str} ({model_path})"
                else:
                    update_text = time_str

                self.lbl_last_model_update.setText(update_text)
            else:
                self.lbl_last_model_update.setText("-")
        except Exception as e:
            print(f"[KPITab] _update_ops_stats error: {e}")
            # エラー時はデフォルト値を設定
            self.lbl_week_stats.setText("-")
            self.lbl_month_stats.setText("-")
            self.lbl_consecutive_failures.setText("-")
            self.lbl_last_model_update.setText("-")
