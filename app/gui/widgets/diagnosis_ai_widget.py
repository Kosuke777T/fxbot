# app/gui/widgets/diagnosis_ai_widget.py
import json
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit


class DiagnosisAIWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout()

        self.title = QLabel("診断AI（v0）")
        self.title.setStyleSheet("font-size: 18px; font-weight: bold;")

        self.info_label = QLabel("※Pro/Expert 限定機能\n分析データはまだありません。")
        self.info_label.setStyleSheet("color: gray;")

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setPlaceholderText("ここに診断AIの結果が表示されます（v0）")

        layout.addWidget(self.title)
        layout.addWidget(self.info_label)
        layout.addWidget(self.text_area)

        self.setLayout(layout)

    def update_data(self, data: dict):
        """診断結果をテキストエリアに反映する"""

        if not data:
            self.text_area.setPlainText("診断データがありません。")
            return

        try:
            pretty = json.dumps(data, ensure_ascii=False, indent=2)
        except TypeError:
            # 念のため、シリアライズできない場合は str() にフォールバック
            pretty = str(data)

        self.text_area.setPlainText(pretty)

