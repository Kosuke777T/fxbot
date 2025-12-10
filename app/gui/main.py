import sys
import traceback
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QLabel,
)

from pathlib import Path

from app.core import logger as app_logger
from app.gui.control_tab import ControlTab
from app.gui.dashboard_tab_qt import DashboardTab
from app.gui.history_tab import HistoryTab
from app.services.execution_stub import evaluate_and_log_once
from app.gui.ai_tab import AITab
from app.gui.backtest_tab import BacktestTab
from app.gui.kpi_tab import KPITab
from app.gui.settings_tab import SettingsTab
from app.services.kpi_service import KPIService


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FX AI Bot Control Panel")
        self.resize(980, 640)

        # --- QTabWidget をインスタンス変数として保持 ---
        self.tabs = QTabWidget(self)

        # === 1段目タブ（メインタブ）の色 + 角丸スタイル ===
        self.tabs.setStyleSheet("""
QTabBar::tab {
    background: #F0F0F0;              /* 非選択：薄い灰色 */
    padding: 6px 12px;
    border: 1px solid #CCCCCC;
    border-top-left-radius: 4px;      /* ← 角丸 */
    border-top-right-radius: 4px;     /* ← 角丸 */
}

QTabBar::tab:selected {
    background: #D7EEFF;              /* 選択：薄い水色 */
    border: 1px solid #A0C8E8;
}

QTabBar::tab:hover {
    background: #E5F4FF;
}
""")

        # まずは軽いタブだけ即座に生成
        self.tabs.addTab(DashboardTab(), "Dashboard")
        self.tabs.addTab(ControlTab(), "Control")
        self.tabs.addTab(HistoryTab(), "History")

        # --- AIタブはプレースホルダを入れておき、実体は後で生成 ---
        # プレースホルダ用ウィジェット
        ai_placeholder = QWidget(self.tabs)
        ph_layout = QVBoxLayout(ai_placeholder)
        ph_label = QLabel("AIタブは選択時に読み込みます（起動を軽くするための仕様）", ai_placeholder)
        ph_label.setWordWrap(True)
        ph_layout.addStretch(1)
        ph_layout.addWidget(ph_label)
        ph_layout.addStretch(1)

        # プレースホルダタブを追加し、そのインデックスを保存
        self._ai_tab: Optional[AITab] = None
        self._ai_tab_index = self.tabs.addTab(ai_placeholder, "AI")

        # KPI サービスを生成（BacktestTab と KPITab で使用）
        self.kpi_service = KPIService(base_dir=Path("."))

        # 残りのタブを追加
        self.tabs.addTab(
            BacktestTab(
                parent=self,
                kpi_service=self.kpi_service,
                profile_name="michibiki_std",
            ),
            "Backtest"
        )
        self.tabs.addTab(
            KPITab(
                parent=self,
                kpi_service=self.kpi_service,
                profile_name="michibiki_std",
            ),
            "運用KPI"
        )
        self.tabs.addTab(SettingsTab(), "Settings")

        # QTabWidget をメインウィンドウにセット
        self.setCentralWidget(self.tabs)

        # タブ切り替えシグナルにハンドラを接続
        self.tabs.currentChanged.connect(self._on_tab_changed)

        app_logger.setup()

        # --- ★GUI軽量化：ドライランタイマーはデフォルト無効 ---
        ENABLE_DRYRUN_TIMER = False  # 必要になったら True に変更

        if ENABLE_DRYRUN_TIMER:
            self.timer = QTimer(self)

            def _tick_safe():
                try:
                    evaluate_and_log_once()
                except Exception:
                    print("[gui.timer] evaluate failed:\\n" + traceback.format_exc())

            self.timer.timeout.connect(_tick_safe)
            self.timer.start(3000)
            _tick_safe()

    def _on_tab_changed(self, index: int) -> None:
        """
        タブが切り替わったときに呼ばれる。
        初めて AI タブが選択されたときにだけ AITab を生成して差し替える。
        """
        # まだ AI タブを生成しておらず、かつ AI タブのインデックスが選択されたときだけ実行
        if self._ai_tab is None and index == self._ai_tab_index:
            # 本物の AITab を生成
            self._ai_tab = AITab()

            # いま入っているプレースホルダタブを削除し、
            # 同じ位置に AITab を挿入（インデックスも更新）
            self.tabs.removeTab(self._ai_tab_index)
            self._ai_tab_index = self.tabs.insertTab(index, self._ai_tab, "AI")

            # 念のため、フォーカスも AI タブに合わせておく
            self.tabs.setCurrentIndex(index)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
