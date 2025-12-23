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
    QDialog,
    QFormLayout,
    QLineEdit,
    QCheckBox,
    QComboBox,
    QSpinBox,
    QDialogButtonBox,
)

from app.services.scheduler_facade import (
    get_scheduler_snapshot,
    add_scheduler_job,
    remove_scheduler_job,
)


class AddJobDialog(QDialog):
    """Schedulerジョブ追加（最小UI）"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ジョブ追加")
        self.setModal(True)

        form = QFormLayout(self)

        self.id_edit = QLineEdit(self)
        self.enabled_cb = QCheckBox(self)
        self.enabled_cb.setChecked(True)

        self.command_edit = QLineEdit(self)
        self.command_edit.setPlaceholderText("例: python -m scripts.walkforward_retrain --profile michibiki_std")

        self.weekday_combo = QComboBox(self)
        self.weekday_combo.addItem("毎日（指定なし）", None)
        names = ["月(0)", "火(1)", "水(2)", "木(3)", "金(4)", "土(5)", "日(6)"]
        for i, n in enumerate(names):
            self.weekday_combo.addItem(n, i)

        self.hour_spin = QSpinBox(self)
        self.hour_spin.setRange(0, 23)
        self.hour_spin.setValue(0)

        self.minute_spin = QSpinBox(self)
        self.minute_spin.setRange(0, 59)
        self.minute_spin.setValue(0)

        self.level_spin = QSpinBox(self)
        self.level_spin.setRange(0, 3)
        self.level_spin.setValue(3)

        form.addRow("id（必須）", self.id_edit)
        form.addRow("enabled", self.enabled_cb)
        form.addRow("command（必須）", self.command_edit)
        form.addRow("weekday", self.weekday_combo)
        form.addRow("hour", self.hour_spin)
        form.addRow("minute", self.minute_spin)
        form.addRow("scheduler_level", self.level_spin)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def get_value(self) -> dict | None:
        """ダイアログを表示し、入力値を返す。キャンセル時は None。"""
        if self.exec() != QDialog.DialogCode.Accepted:
            return None

        job_id = (self.id_edit.text() or "").strip()
        cmd = (self.command_edit.text() or "").strip()
        if not job_id or not cmd:
            QMessageBox.warning(self, "入力エラー", "id と command は必須です。")
            return None

        weekday = self.weekday_combo.currentData()  # None or int 0..6
        job = {
            "id": job_id,
            "enabled": bool(self.enabled_cb.isChecked()),
            "command": cmd,
            "schedule": {
                "weekday": weekday,  # None or int 0..6
                "hour": int(self.hour_spin.value()),
                "minute": int(self.minute_spin.value()),
            },
            "scheduler_level": int(self.level_spin.value()),
        }
        return job


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

    def refresh(self, checked: bool = False, snap: dict | None = None) -> None:
        # clicked(bool) から来た checked が snap に入らないよう防御（保険）
        if snap is not None and not isinstance(snap, dict):
            snap = None

        try:
            if snap is None:
                snap = get_scheduler_snapshot()
        except Exception as e:
            QMessageBox.critical(self, "Scheduler", f"snapshot取得に失敗: {e}")
            return

        level = snap.get("scheduler_level")
        gen = snap.get("generated_at")
        can_edit = bool(snap.get("can_edit"))
        # Expertのみ編集UIを有効化
        self.btn_add.setEnabled(can_edit)
        self.btn_remove.setEnabled(can_edit)
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
        """ジョブ追加ハンドラ（facade経由）"""
        dlg = AddJobDialog(self)
        job = dlg.get_value()
        if not job:
            return

        # T-42-3-4 で接続済みの facade を使用（services層）
        res = add_scheduler_job(job)  # 戻り: {'ok': True, 'snapshot': {...}} を想定
        if res.get("ok"):
            job_id = job.get("id", "?")
            QMessageBox.information(self, "Scheduler", f"ジョブ '{job_id}' を追加しました")
            snap = res.get("snapshot")
            self.refresh(snap=snap)  # T-42-3-4 の refresh(snap=...) 対応済み前提
        else:
            # facade が返す情報を落とさず見える化
            error = res.get("error", "unknown error")
            detail = json.dumps(res, ensure_ascii=False, indent=2)
            QMessageBox.warning(self, "Scheduler", f"追加に失敗: {error}\n\n{detail}")

    def _on_remove(self) -> None:
        """ジョブ削除ハンドラ（facade経由）"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Scheduler", "削除する行を選択してください")
            return

        job_id_item = self.table.item(row, 0)
        job_id = job_id_item.text() if job_id_item else ""
        if not job_id:
            QMessageBox.warning(self, "Scheduler", "ジョブIDが取得できません")
            return

        # 確認ダイアログ
        reply = QMessageBox.question(
            self,
            "ジョブ削除",
            f"ジョブ '{job_id}' を削除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        r = remove_scheduler_job(job_id)
        if r.get("ok"):
            removed = r.get("removed", False)
            if removed:
                QMessageBox.information(self, "Scheduler", f"ジョブ '{job_id}' を削除しました")
            else:
                QMessageBox.warning(self, "Scheduler", f"ジョブ '{job_id}' が見つかりませんでした")
            self.refresh(r.get("snapshot"))  # 削除後に即更新（最重要、戻り値のsnapshotを使用）
        else:
            error = r.get("error", "unknown error")
            QMessageBox.warning(self, "Scheduler", f"削除に失敗: {error}")


