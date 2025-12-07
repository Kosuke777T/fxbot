# app/gui/widgets/future_scenario_widget.py
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QGroupBox, QVBoxLayout, QLabel


class FutureScenarioWidget(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__("来週のシナリオ", parent)

        self._label = QLabel("データがありません。", self)
        self._label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(self._label)
        self.setLayout(layout)

    def update_data(self, data: Optional[dict]) -> None:
        if not data or not isinstance(data, dict):
            self._label.setText("データがありません。")
            return

        bias = data.get("next_week_bias") or data.get("bias")
        vol = data.get("expected_volatility") or data.get("volatility")
        risk = data.get("risk_zone")
        conf = data.get("confidence")

        lines: list[str] = []

        if bias is not None:
            lines.append(f"バイアス: {bias}")
        if vol is not None:
            lines.append(f"ボラティリティ: {vol}")
        if risk is not None:
            lines.append(f"リスクゾーン: {risk}")
        if conf is not None:
            if isinstance(conf, (int, float)):
                lines.append(f"信頼度: {conf:.2f}")
            else:
                lines.append(f"信頼度: {conf}")

        if lines:
            self._label.setText("\n".join(lines))
        else:
            self._label.setText("データがありません。")

