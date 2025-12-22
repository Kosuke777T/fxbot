from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
)

from app.services.scheduler_facade import get_scheduler_snapshot


class SchedulerTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)

        # ヘッダ（scheduler_level 表示）
        self.header = QLabel("Scheduler", self)
        self.header.setWordWrap(True)
        root.addWidget(self.header)

        # ボタン行
        row = QHBoxLayout()
        self.btn_refresh = QPushButton("更新", self)
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_refresh)

        self.btn_add = QPushButton("追加", self)
        self.btn_add.clicked.connect(self._on_add)
        row.addWidget(self.btn_add)

        self.btn_remove = QPushButton("削除", self)
        self.btn_remove.clicked.connect(self._on_remove)
        row.addWidget(self.btn_remove)
        row.addStretch(1)
        root.addLayout(row)

        # テーブル（ジョブ一覧）
        self.table = QTableWidget(self)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "id",
            "enabled",
            "schedule",
            "next_run_at",
            "state",
            "last_run_at",
            "last_result",
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        root.addWidget(self.table)

        # 初回描画
        self.refresh()

    def refresh(self) -> None:
        try:
            snap = get_scheduler_snapshot()
        except Exception as e:
            QMessageBox.critical(self, "Scheduler", f"snapshot取得に失敗: {e}")
            return

        level = snap.get("scheduler_level")
        gen = snap.get("generated_at")
        can_edit = bool(snap.get("can_edit"))
        # Expertのみ編集UIを表示
        self.btn_add.setVisible(can_edit)
        self.btn_remove.setVisible(can_edit)
        self.header.setText(f"Scheduler（scheduler_level={level}） 生成: {gen}")

        jobs: List[Dict[str, Any]] = snap.get("jobs") or []
        self.table.setRowCount(len(jobs))

        for r, j in enumerate(jobs):
            job_id = str(j.get("id") or "")
            enabled = "true" if bool(j.get("enabled")) else "false"

            sch = j.get("schedule") or {}
            schedule_str = f'wd={sch.get("weekday")} h={sch.get("hour")} m={sch.get("minute")}'
            next_run_at = j.get("next_run_at") or "-"

            state = str(j.get("state") or "")
            last_run_at = j.get("last_run_at") or "-"

            last_result = j.get("last_result")
            last_result_str = "-" if not last_result else self._summarize_result(last_result)

            self._set_item(r, 0, job_id)
            self._set_item(r, 1, enabled)
            self._set_item(r, 2, schedule_str)
            self._set_item(r, 3, str(next_run_at))
            self._set_item(r, 4, state)
            self._set_item(r, 5, str(last_run_at))
            self._set_item(r, 6, last_result_str)

        self.table.resizeColumnsToContents()

    def _set_item(self, row: int, col: int, text: str) -> None:
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, col, it)

    def _summarize_result(self, res: Dict[str, Any]) -> str:
        # jobs_state に入る last_result の想定: {"ok":bool,"rc":int,"stdout":str,"stderr":str,"error":...}
        ok = res.get("ok")
        rc = res.get("rc")
        err = res.get("error")
        if err:
            code = err.get("code") if isinstance(err, dict) else str(err)
            return f"ok={ok} rc={rc} err={code}"
        return f"ok={ok} rc={rc}"

    def _on_double_click(self, row: int, col: int) -> None:
        # last_result を詳細表示（stdout/stderr/error）
        try:
            snap = get_scheduler_snapshot()
            jobs = snap.get("jobs") or []
            if row < 0 or row >= len(jobs):
                return
            j = jobs[row]
            title = f"Job detail: {j.get('id')}"
            msg = json.dumps(j.get("last_result"), indent=2, ensure_ascii=False)
            if not msg or msg == "null":
                msg = "(no last_result)"
            QMessageBox.information(self, title, msg)
        except Exception as e:
            QMessageBox.warning(self, "Scheduler", f"詳細表示に失敗: {e}")

    def _on_add(self) -> None:
        # T-42-3-3 で実装（保存含む）
        QMessageBox.information(self, "Scheduler", "追加は Expert 機能（T-42-3-3 で実装します）")

    def _on_remove(self) -> None:
        # T-42-3-3 で実装（保存含む）
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Scheduler", "削除する行を選択してください")
            return
        job_id_item = self.table.item(row, 0)
        job_id = job_id_item.text() if job_id_item else ""
        QMessageBox.information(self, "Scheduler", f"削除は Expert 機能（T-42-3-3 で実装します）\\njob_id={job_id}")

