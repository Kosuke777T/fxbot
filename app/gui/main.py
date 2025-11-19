import sys
import traceback

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget

from app.core import logger as app_logger
from app.gui.control_tab import ControlTab
from app.gui.dashboard_tab_qt import DashboardTab
from app.gui.history_tab import HistoryTab
from app.services.execution_stub import evaluate_and_log_once
from app.gui.ai_tab import AITab
from app.gui.backtest_tab import BacktestTab
from app.gui.settings_tab import SettingsTab

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FX AI Bot Control Panel")
        self.resize(980, 640)

        tabs = QTabWidget()
        tabs.addTab(DashboardTab(), "Dashboard")
        tabs.addTab(ControlTab(), "Control")
        tabs.addTab(HistoryTab(), "History")
        tabs.addTab(AITab(), "AI")
        tabs.addTab(BacktestTab(), "Backtest")
        tabs.addTab(SettingsTab(), "Settings")
        self.setCentralWidget(tabs)

        app_logger.setup()

        self.timer = QTimer(self)

        def _tick_safe():
            try:
                evaluate_and_log_once()
            except Exception:
                print("[gui.timer] evaluate failed:\n" + traceback.format_exc())

        self.timer.timeout.connect(_tick_safe)
        self.timer.start(3000)
        _tick_safe()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
