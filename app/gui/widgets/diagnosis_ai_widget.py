# app/gui/widgets/diagnosis_ai_widget.py
import json
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QTextEdit


class DiagnosisAIWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        main_layout = QVBoxLayout(self)

        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
/* --- 診断AI内タブ（2段目） --- */
QTabBar::tab {
    background: #F6F6F6;        /* 非選択タブ：薄めのグレー（標準UIと似た色） */
    border: 1px solid #CCCCCC;
    padding: 6px 12px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

/* 選択タブ（薄い水色） */
QTabBar::tab:selected {
    background: #D7EEFF;
    border: 1px solid #A0C8E8;
}

/* ホバー */
QTabBar::tab:hover {
    background: #E9F5FF;
}
""")
        main_layout.addWidget(self.tab_widget)

        # 1) 時間帯 × 相場タイプ
        time_page = QWidget()
        time_layout = QVBoxLayout(time_page)
        self.text_time = QTextEdit()
        self.text_time.setReadOnly(True)
        time_layout.addWidget(self.text_time)
        self.tab_widget.addTab(time_page, "時間帯 × 相場タイプ")

        # 2) 勝率が高い条件
        win_page = QWidget()
        win_layout = QVBoxLayout(win_page)
        self.text_win = QTextEdit()
        self.text_win.setReadOnly(True)
        win_layout.addWidget(self.text_win)
        self.tab_widget.addTab(win_page, "勝率が高い条件")

        # 3) DD直前の特徴
        dd_page = QWidget()
        dd_layout = QVBoxLayout(dd_page)
        self.text_dd = QTextEdit()
        self.text_dd.setReadOnly(True)
        dd_layout.addWidget(self.text_dd)
        self.tab_widget.addTab(dd_page, "DD直前の特徴")

        # 4) 異常点の検出
        anom_page = QWidget()
        anom_layout = QVBoxLayout(anom_page)
        self.text_anom = QTextEdit()
        self.text_anom.setReadOnly(True)
        anom_layout.addWidget(self.text_anom)
        self.tab_widget.addTab(anom_page, "異常点の検出")

    def update_data(self, data: dict | None) -> None:
        """診断結果を各タブに反映する"""

        if not data:
            msg = "診断データがありません。"
            for text in (self.text_time, self.text_win, self.text_dd, self.text_anom):
                text.setPlainText(msg)
            return

        def to_json(value):
            try:
                return json.dumps(value, ensure_ascii=False, indent=2)
            except TypeError:
                return str(value)

        self.text_time.setPlainText(
            to_json(data.get("time_of_day_stats", {}))
        )
        self.text_win.setPlainText(
            to_json(data.get("winning_conditions", {}))
        )
        self.text_dd.setPlainText(
            to_json(data.get("dd_pre_signal", {}))
        )
        self.text_anom.setPlainText(
            to_json(data.get("anomalies", []))
        )

