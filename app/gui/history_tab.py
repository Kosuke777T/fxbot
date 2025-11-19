from __future__ import annotations

from typing import List, Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QHeaderView

from app.services.event_store import EVENT_STORE, UiEvent

_COLUMNS = ["ts", "kind", "symbol", "side", "price", "sl", "tp", "profit_jpy", "reason", "notes"]


class HistoryTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
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

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(self.btnExport)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.refresh()

    def refresh(self) -> None:
        events: List[UiEvent] = EVENT_STORE.recent(300)
        self.table.setRowCount(len(events))
        for r, ev in enumerate(events):
            row = [getattr(ev, col) for col in _COLUMNS]
            for c, val in enumerate(row):
                item = QtWidgets.QTableWidgetItem("" if val is None else str(val))
                self.table.setItem(r, c, item)

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
