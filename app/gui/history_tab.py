from __future__ import annotations

from typing import List, Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QHeaderView, QScrollArea, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QWidget

from loguru import logger

from app.services.event_store import EVENT_STORE, UiEvent
from app.services.ops_history_service import get_ops_history_service

_COLUMNS = ["ts", "kind", "symbol", "side", "price", "sl", "tp", "profit_jpy", "reason", "notes"]


class HistoryTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # タブで分割（左：UiEvent、右：Ops履歴）
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, self)

        # 左側：既存のUiEventテーブル
        left_widget = QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        h: Optional[QHeaderView] = self.table.horizontalHeader()
        if h is not None:
            h.setStretchLastSection(True)

        v: Optional[QHeaderView] = self.table.verticalHeader()
        if v is not None:
            v.setVisible(False)

        self.btnExport = QtWidgets.QPushButton("Export CSV")
        self.btnExport.clicked.connect(self._export_csv)

        left_layout.addWidget(self.table)
        left_layout.addWidget(self.btnExport)
        splitter.addWidget(left_widget)

        # 右側：Ops履歴カード表示
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        ops_label = QLabel("Ops履歴", right_widget)
        ops_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        right_layout.addWidget(ops_label)

        # スクロール可能なカードエリア
        scroll_area = QScrollArea(right_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.ops_cards_widget = QWidget()
        self.ops_cards_layout = QVBoxLayout(self.ops_cards_widget)
        self.ops_cards_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        scroll_area.setWidget(self.ops_cards_widget)

        right_layout.addWidget(scroll_area)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addWidget(splitter)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.refresh()

    def refresh(self) -> None:
        # UiEventテーブルを更新
        events: List[UiEvent] = EVENT_STORE.recent(300)
        self.table.setRowCount(len(events))
        for r, ev in enumerate(events):
            row = [getattr(ev, col) for col in _COLUMNS]
            for c, val in enumerate(row):
                item = QtWidgets.QTableWidgetItem("" if val is None else str(val))
                self.table.setItem(r, c, item)

        # Ops履歴カードを更新
        self._refresh_ops_cards()

    def _refresh_ops_cards(self) -> None:
        """Ops履歴カードを更新する。"""
        try:
            history_service = get_ops_history_service()
            summary = history_service.summarize_ops_history()
            items = summary.get("items", [])
            last_view = summary.get("last_view")  # 最新の表示用ビュー（現在は未使用だが取得しておく）

            # 既存のカードをクリア
            while self.ops_cards_layout.count():
                child = self.ops_cards_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            # カードを生成
            for item in items:
                card = self._create_ops_card(item)
                if card:
                    self.ops_cards_layout.addWidget(card)

            # スペーサーを追加
            self.ops_cards_layout.addStretch()
        except Exception as e:
            logger.error(f"Failed to refresh ops cards: {e}")

    def _create_ops_card(self, item: dict) -> Optional[QWidget]:
        """Ops履歴カードを作成する。"""
        try:
            card = QGroupBox()
            card_layout = QVBoxLayout(card)

            # ヘッダー行（phaseバッジ + headline）
            header_layout = QHBoxLayout()

            # phaseバッジ
            phase = item.get("phase", "OTHER")
            phase_label = QLabel(phase)
            phase_label.setStyleSheet(self._get_phase_style(phase))
            header_layout.addWidget(phase_label)

            # headline
            headline = item.get("headline", "")
            headline_label = QLabel(headline)
            headline_label.setStyleSheet("font-weight: bold; font-size: 10pt;")
            header_layout.addWidget(headline_label, 1)
            header_layout.addStretch()

            card_layout.addLayout(header_layout)

            # subline
            subline = item.get("subline", "")
            if subline:
                subline_label = QLabel(subline)
                subline_label.setStyleSheet("color: #666; font-size: 9pt;")
                card_layout.addWidget(subline_label)

            # timeline
            timeline = item.get("timeline", {})
            timeline_text = self._format_timeline(timeline)
            if timeline_text:
                timeline_label = QLabel(timeline_text)
                timeline_label.setStyleSheet("color: #888; font-size: 8pt;")
                card_layout.addWidget(timeline_label)

            # diff
            diff = item.get("diff", {})
            if diff:
                diff_text = self._format_diff(diff)
                if diff_text:
                    diff_label = QLabel(diff_text)
                    diff_label.setStyleSheet("color: #0066cc; font-size: 8pt;")
                    card_layout.addWidget(diff_label)

            return card
        except Exception as e:
            logger.error(f"Failed to create ops card: {e}")
            return None

    def _get_phase_style(self, phase: str) -> str:
        """phaseに応じたスタイルを返す。"""
        styles = {
            "PROMOTED": "background-color: #ffeb3b; color: #000; padding: 2px 6px; border-radius: 3px; font-size: 8pt;",
            "APPLIED": "background-color: #4caf50; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 8pt;",
            "DONE": "background-color: #2196f3; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 8pt;",
            "FAILED": "background-color: #f44336; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 8pt;",
        }
        return styles.get(phase, "background-color: #9e9e9e; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 8pt;")

    def _format_timeline(self, timeline: dict) -> str:
        """timelineをフォーマットする。"""
        parts = []
        if timeline.get("started"):
            parts.append(f"開始: {timeline['started'][:19]}")
        if timeline.get("promoted"):
            parts.append(f"PROMOTED: {timeline['promoted'][:19]}")
        if timeline.get("applied"):
            parts.append(f"APPLIED: {timeline['applied'][:19]}")
        if timeline.get("done"):
            parts.append(f"完了: {timeline['done'][:19]}")
        return " | ".join(parts) if parts else ""

    def _format_diff(self, diff: dict) -> str:
        """diffをフォーマットする。"""
        parts = []
        for field, change in diff.items():
            from_val = change.get("from")
            to_val = change.get("to")
            if from_val != to_val:
                parts.append(f"{field}: {from_val} → {to_val}")
        return " | ".join(parts) if parts else ""

    def _export_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export history to CSV", "history.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        import csv

        events: List[UiEvent] = EVENT_STORE.recent(1000)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_COLUMNS)
            for ev in events:
                writer.writerow([getattr(ev, col) for col in _COLUMNS])
