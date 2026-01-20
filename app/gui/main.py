import sys
import traceback
import threading
from typing import Optional

from PyQt6.QtCore import QTimer, QObject
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
from app.gui.ops_tab import OpsTab
from app.gui.scheduler_tab import SchedulerTab
from app.gui.visualize_tab import VisualizeTab
from app.services.kpi_service import KPIService
from app.services.scheduler_facade import get_scheduler
from loguru import logger


class SchedulerTickRunner(QObject):
    """GUI起動中に JobScheduler.run_pending() を定期実行するランナー"""

    def __init__(self, parent=None, interval_ms: int = 10_000):
        super().__init__(parent)
        # scheduler_facade のシングルトンを使用（二重生成を防止）
        self._scheduler = get_scheduler()
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()
        logger.info("[GUI][scheduler] tick runner started interval_ms={} (using singleton scheduler)", interval_ms)

    def _on_tick(self):
        # 連続起動を防ぐ（run_pendingが重い可能性があるため）
        if self._lock.locked():
            return
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        with self._lock:
            try:
                self._scheduler.run_pending()
            except Exception as e:
                logger.exception("[GUI][scheduler] run_pending failed: {}", e)


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
        self.tabs.addTab(VisualizeTab(), "Visualize")

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
        self.tabs.addTab(SchedulerTab(), "Scheduler")
        self.tabs.addTab(OpsTab(), "Ops")

        # QTabWidget をメインウィンドウにセット
        self.setCentralWidget(self.tabs)

        # タブ切り替えシグナルにハンドラを接続
        self.tabs.currentChanged.connect(self._on_tab_changed)

        app_logger.setup()

        # --- スケジューラTick起動（GUI起動中に run_pending() を定期実行） ---
        self._scheduler_tick = SchedulerTickRunner(self, interval_ms=10_000)

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
