from __future__ import annotations

import json
import re
from functools import partial
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
    QPlainTextEdit,
    QSplitter,
)

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

from app.services.scheduler_facade import (
    get_scheduler_snapshot,
    add_scheduler_job,
    remove_scheduler_job,
    run_scheduler_job_now,
)


class AddJobDialog(QDialog):
    """Schedulerジョブ追加/編集（最小UI）"""

    def __init__(self, parent: Optional[QWidget] = None, job: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(parent)
        is_edit = job is not None
        self.setWindowTitle("ジョブ編集" if is_edit else "ジョブ追加")
        self.setModal(True)

        form = QFormLayout(self)

        self.id_edit = QLineEdit(self)
        self.id_edit.setReadOnly(is_edit)  # 編集時はidを変更不可
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

        # 既存ジョブの値をプリセット
        if job:
            self.id_edit.setText(str(job.get("id", "")))
            self.enabled_cb.setChecked(bool(job.get("enabled", True)))
            self.command_edit.setText(str(job.get("command", "")))

            # weekday: scheduleから取得、なければトップレベルから
            sch = job.get("schedule") or {}
            weekday = sch.get("weekday") if sch.get("weekday") is not None else job.get("weekday")
            if weekday is not None:
                idx = self.weekday_combo.findData(weekday)
                if idx >= 0:
                    self.weekday_combo.setCurrentIndex(idx)

            # hour: scheduleから取得、なければトップレベルから
            hour = sch.get("hour") if sch.get("hour") is not None else job.get("hour")
            if hour is not None:
                self.hour_spin.setValue(int(hour))

            # minute: scheduleから取得、なければトップレベルから
            minute = sch.get("minute") if sch.get("minute") is not None else job.get("minute")
            if minute is not None:
                self.minute_spin.setValue(int(minute))

            # scheduler_level
            level = job.get("scheduler_level")
            if level is not None:
                self.level_spin.setValue(int(level))

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

    def _warn(self, msg: str) -> None:
        """バリデーションエラー時の警告表示"""
        QMessageBox.warning(self, "入力エラー", msg)

    def get_value(self) -> dict | None:
        """ダイアログを表示し、入力値を返す。キャンセル時は None。"""
        if self.exec() != QDialog.DialogCode.Accepted:
            return None

        # 入力値の取得とトリム
        job_id = (self.id_edit.text() or "").strip()
        cmd = (self.command_edit.text() or "").strip()

        weekday = self.weekday_combo.currentData()  # None or int 0..6
        hour = int(self.hour_spin.value())
        minute = int(self.minute_spin.value())

        # バリデーション
        if not job_id:
            self._warn("id は必須です。")
            return None
        if not _JOB_ID_RE.match(job_id):
            self._warn("id は英数字・_・- のみで、1〜64文字にしてください。")
            return None
        if not cmd:
            self._warn("command は必須です。")
            return None
        if weekday is not None and (not isinstance(weekday, int) or weekday < 0 or weekday > 6):
            self._warn("weekday が不正です（None または 0..6）。")
            return None
        if hour < 0 or hour > 23:
            self._warn("hour が不正です（0..23）。")
            return None
        if minute < 0 or minute > 59:
            self._warn("minute が不正です（0..59）。")
            return None

        # バリデーションOKならjob dictを組み立て
        job = {
            "id": job_id,
            "enabled": bool(self.enabled_cb.isChecked()),
            "command": cmd,
            # ★services側が期待していそうなトップレベル（yamlもこの形）
            "weekday": weekday,
            "hour": hour,
            "minute": minute,
            # ★UI/表示側との整合用に schedule も同梱（害はない）
            "schedule": {"weekday": weekday, "hour": hour, "minute": minute},
            "scheduler_level": int(self.level_spin.value()),
        }
        return job


class SchedulerTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)  # 上を詰める
        root.setSpacing(6)

        # ヘッダ（scheduler_level 表示）
        self.header = QLabel("Scheduler", self)
        self.header.setWordWrap(True)
        self.header.setContentsMargins(0, 0, 0, 0)
        from PyQt6.QtWidgets import QSizePolicy
        self.header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        root.addWidget(self.header)

        # ボタン行
        row = QHBoxLayout()
        self.btn_refresh = QPushButton("更新", self)
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_refresh)

        self.btn_add = QPushButton("追加", self)
        self.btn_add.clicked.connect(self._on_add)
        row.addWidget(self.btn_add)

        self.btn_edit = QPushButton("編集", self)
        self.btn_edit.clicked.connect(self._on_edit)
        row.addWidget(self.btn_edit)

        self.btn_remove = QPushButton("削除", self)
        self.btn_remove.clicked.connect(self._on_remove)
        row.addWidget(self.btn_remove)
        row.addStretch(1)
        root.addLayout(row)

        # メインスプリッター（水平分割：左=ジョブテーブル、右=詳細ビュー）
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        root.addWidget(main_splitter, 1)  # stretch=1 を明示

        # 左側：ジョブテーブル（垂直分割：上=スケジュール、下=常時実行）
        jobs_splitter = QSplitter(Qt.Orientation.Vertical, self)
        main_splitter.addWidget(jobs_splitter)

        # スケジュールジョブテーブル
        scheduled_group = QWidget(self)
        scheduled_layout = QVBoxLayout(scheduled_group)
        scheduled_label = QLabel("スケジュールジョブ", self)
        scheduled_layout.addWidget(scheduled_label)
        self.table_scheduled = QTableWidget(self)
        self.table_scheduled.setColumnCount(8)
        self.table_scheduled.setHorizontalHeaderLabels([
            "実行",
            "id",
            "enabled",
            "schedule",
            "next_run_at",
            "state",
            "last_run_at",
            "last_result",
        ])
        self.table_scheduled.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_scheduled.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_scheduled.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table_scheduled.cellDoubleClicked.connect(self._on_double_click)
        self.table_scheduled.itemSelectionChanged.connect(self._on_job_selected)
        scheduled_layout.addWidget(self.table_scheduled)
        jobs_splitter.addWidget(scheduled_group)

        # 常時実行ジョブテーブル
        always_group = QWidget(self)
        always_layout = QVBoxLayout(always_group)
        always_label = QLabel("常時実行ジョブ", self)
        always_layout.addWidget(always_label)
        self.table_always = QTableWidget(self)
        self.table_always.setColumnCount(8)
        self.table_always.setHorizontalHeaderLabels([
            "実行",
            "id",
            "enabled",
            "schedule",
            "next_run_at",
            "state",
            "last_run_at",
            "last_result",
        ])
        self.table_always.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_always.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_always.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table_always.cellDoubleClicked.connect(self._on_double_click)
        self.table_always.itemSelectionChanged.connect(self._on_job_selected)
        always_layout.addWidget(self.table_always)
        jobs_splitter.addWidget(always_group)

        # 右側：詳細ビュー（実行ログ）
        detail_group = QWidget(self)
        detail_layout = QVBoxLayout(detail_group)
        detail_label = QLabel("実行ログ（選択中）", self)
        detail_layout.addWidget(detail_label)
        self.detail_text = QPlainTextEdit(self)
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumBlockCount(1000)  # メモリ保護
        detail_layout.addWidget(self.detail_text)
        main_splitter.addWidget(detail_group)

        # スプリッターのサイズ比率
        jobs_splitter.setSizes([200, 100])  # スケジュール:常時実行 = 2:1
        main_splitter.setSizes([300, 200])  # ジョブテーブル:詳細 = 3:2

        # 最後のsnapshotを保持（詳細表示用）
        self._last_snapshot: dict | None = None

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
        self.btn_edit.setEnabled(can_edit)
        self.btn_remove.setEnabled(can_edit)
        self.header.setText(f"Scheduler（scheduler_level={level}） 生成: {gen}")

        # snapshotを保持（詳細表示用）
        self._last_snapshot = snap

        jobs: List[Dict[str, Any]] = snap.get("jobs") or []

        # ジョブを2つに分ける
        scheduled_jobs: List[Dict[str, Any]] = []
        always_jobs: List[Dict[str, Any]] = []

        for j in jobs:
            sch = j.get("schedule") or {}
            weekday = sch.get("weekday")
            hour = sch.get("hour")
            minute = sch.get("minute")
            run_always = bool(j.get("run_always", False))

            # weekday/hour/minute が全部 None かつ run_always=True なら常時実行
            if weekday is None and hour is None and minute is None and run_always:
                always_jobs.append(j)
            else:
                scheduled_jobs.append(j)

        # スケジュールジョブテーブルを更新
        self._populate_table(self.table_scheduled, scheduled_jobs)

        # 常時実行ジョブテーブルを更新
        self._populate_table(self.table_always, always_jobs)

    def _populate_table(self, table: QTableWidget, jobs: List[Dict[str, Any]]) -> None:
        """テーブルにジョブを表示（共通処理）"""
        table.setRowCount(len(jobs))

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

            # 「今すぐ実行」ボタン（col=0、一番左）
            btn = QPushButton("実行", self)
            btn.clicked.connect(partial(self._on_run_now_clicked, job_id))
            table.setCellWidget(r, 0, btn)

            # 以降、列を1つ右へずらす
            self._set_item(table, r, 1, job_id)
            self._set_item(table, r, 2, enabled)
            self._set_item(table, r, 3, schedule_str)
            self._set_item(table, r, 4, str(next_run_at))
            self._set_item(table, r, 5, state)
            self._set_item(table, r, 6, str(last_run_at))
            self._set_item(table, r, 7, last_result_str)

        table.resizeColumnsToContents()

        # 実行列（0列目）を固定幅にして見切れ/揺れを防止
        from PyQt6.QtWidgets import QHeaderView
        table.setColumnWidth(0, 60)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)

    def _set_item(self, table: QTableWidget, row: int, col: int, text: str) -> None:
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, col, it)

    def _summarize_result(self, res: Dict[str, Any]) -> str:
        # jobs_state に入る last_result の想定: {"ok":bool,"rc":int,"stdout":str,"stderr":str,"error":...}
        ok = res.get("ok")
        rc = res.get("rc")
        err = res.get("error")
        if err:
            code = err.get("code") if isinstance(err, dict) else str(err)
            return f"ok={ok} rc={rc} err={code}"
        return f"ok={ok} rc={rc}"

    def _get_selected_job_id(self) -> str:
        """選択中のジョブIDを取得（scheduled優先、無ければalways）"""
        for table in (self.table_scheduled, self.table_always):
            row = table.currentRow()
            if row >= 0:
                it = table.item(row, 1)  # id列は1（実行ボタンが0）
                if it and it.text():
                    return it.text()
        return ""

    def _on_job_selected(self) -> None:
        """ジョブ選択時の詳細表示更新"""
        if not self._last_snapshot:
            self.detail_text.clear()
            return

        job_id = self._get_selected_job_id()
        if not job_id:
            self.detail_text.clear()
            return

        try:

            # snapshotから該当ジョブを検索
            jobs = self._last_snapshot.get("jobs") or []
            job = next((j for j in jobs if str(j.get("id", "")) == job_id), None)
            if not job:
                self.detail_text.clear()
                return
            job_id = str(job.get("id") or "")
            state = str(job.get("state") or "")
            last_run_at = str(job.get("last_run_at") or "-")
            next_run_at = str(job.get("next_run_at") or "-")
            last_result = job.get("last_result")

            # 詳細表示の整形
            lines = []
            lines.append(f"id: {job_id}")
            lines.append(f"state: {state}")
            lines.append(f"last_run_at: {last_run_at}")
            lines.append(f"next_run_at: {next_run_at}")
            lines.append("")

            if last_result:
                ok = last_result.get("ok")
                rc = last_result.get("rc")
                error = last_result.get("error")
                stdout = last_result.get("stdout", "")
                stderr = last_result.get("stderr", "")

                lines.append(f"last_result.ok: {ok}")
                lines.append(f"last_result.rc: {rc}")
                if error:
                    error_code = error.get("code", "") if isinstance(error, dict) else str(error)
                    error_msg = error.get("message", "") if isinstance(error, dict) else ""
                    lines.append(f"last_result.error: {error_code} - {error_msg}")
                lines.append("")

                if stdout:
                    lines.append("--- stdout ---")
                    lines.append(str(stdout))
                    lines.append("")

                if stderr:
                    lines.append("--- stderr ---")
                    lines.append(str(stderr))
                    lines.append("")
            else:
                lines.append("(no last_result)")

            self.detail_text.setPlainText("\n".join(lines))

        except Exception as e:
            self.detail_text.setPlainText(f"詳細表示エラー: {e}")

    def _on_double_click(self, row: int, col: int) -> None:
        # last_result を詳細表示（stdout/stderr/error）
        try:
            # どちらのテーブルからダブルクリックされたか判定
            sender_table = self.sender()
            if sender_table == self.table_scheduled:
                table = self.table_scheduled
            elif sender_table == self.table_always:
                table = self.table_always
            else:
                return

            # job_idを取得（id列は1）
            job_id_item = table.item(row, 1)
            job_id = job_id_item.text() if job_id_item else ""
            if not job_id:
                return

            snap = get_scheduler_snapshot()
            jobs = snap.get("jobs") or []
            j = next((j for j in jobs if str(j.get("id", "")) == job_id), None)
            if not j:
                return

            title = f"Job detail: {j.get('id')}"
            msg = json.dumps(j.get("last_result"), indent=2, ensure_ascii=False)
            if not msg or msg == "null":
                msg = "(no last_result)"
            QMessageBox.information(self, title, msg)
        except Exception as e:
            QMessageBox.warning(self, "Scheduler", f"詳細表示に失敗: {e}")

    def _on_run_now_clicked(self, job_id: str) -> None:
        """「今すぐ実行」ボタンのハンドラ"""
        try:
            res = run_scheduler_job_now(job_id)
            if res.get("ok"):
                result = res.get("result") or {}
                rc = result.get("rc", -1)
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")

                # 詳細ビューに追記
                lines = [f"[run_now] {job_id} ok=True rc={rc}"]
                if stdout:
                    lines.append("--- stdout ---")
                    lines.append(stdout.rstrip())
                if stderr:
                    lines.append("--- stderr ---")
                    lines.append(stderr.rstrip())
                self.detail_text.appendPlainText("\n".join(lines))

                # 実行後スナップショットで再描画
                snap = res.get("snapshot")
                if snap:
                    self.refresh(snap=snap)
                    QMessageBox.information(self, "Scheduler", f"ジョブ '{job_id}' を実行しました")
            else:
                error = res.get("error", "unknown error")
                QMessageBox.warning(self, "Scheduler", f"実行に失敗: {error}")
        except Exception as e:
            QMessageBox.warning(self, "Scheduler", f"実行エラー: {e}")

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

    def _on_edit(self) -> None:
        """ジョブ編集ハンドラ（facade経由）"""
        job_id = self._get_selected_job_id()
        if not job_id:
            QMessageBox.warning(self, "Scheduler", "編集する行を選択してください")
            return

        # 選択中のジョブを取得
        try:
            snap = get_scheduler_snapshot()
            jobs = snap.get("jobs") or []

            # jobsリストから該当ジョブを検索
            job = next((j for j in jobs if str(j.get("id", "")) == job_id), None)
            if not job:
                QMessageBox.warning(self, "Scheduler", f"ジョブ '{job_id}' が見つかりません")
                return

            # 編集用に、snapshotのjobから完全なjob定義を構築
            # snapshotにはcommandとscheduler_levelが含まれている（T-42-3-6で追加）
            edit_job = {
                "id": job.get("id"),
                "enabled": job.get("enabled"),
                "command": job.get("command", ""),
                "weekday": job.get("schedule", {}).get("weekday"),
                "hour": job.get("schedule", {}).get("hour"),
                "minute": job.get("schedule", {}).get("minute"),
                "schedule": job.get("schedule", {}),
                "scheduler_level": job.get("scheduler_level"),
            }

        except Exception as e:
            QMessageBox.warning(self, "Scheduler", f"ジョブ情報の取得に失敗: {e}")
            return

        # ダイアログを表示（既存ジョブを渡す）
        dlg = AddJobDialog(self, job=edit_job)
        updated_job = dlg.get_value()
        if not updated_job:
            return

        # 既存のidを維持（編集ではidは変更しない）
        updated_job["id"] = job_id

        # T-42-3-4 で接続済みの facade を使用（services層）
        # add_scheduler_jobは既にupdateも対応している（JobScheduler.add_jobが既存ジョブを置き換える）
        res = add_scheduler_job(updated_job)  # 戻り: {'ok': True, 'snapshot': {...}} を想定
        if res.get("ok"):
            QMessageBox.information(self, "Scheduler", f"ジョブ '{job_id}' を更新しました")
            snap = res.get("snapshot")
            self.refresh(snap=snap)  # T-42-3-4 の refresh(snap=...) 対応済み前提
        else:
            # facade が返す情報を落とさず見える化
            error = res.get("error", "unknown error")
            detail = json.dumps(res, ensure_ascii=False, indent=2)
            QMessageBox.warning(self, "Scheduler", f"更新に失敗: {error}\n\n{detail}")

    def _on_remove(self) -> None:
        """ジョブ削除ハンドラ（facade経由）"""
        job_id = self._get_selected_job_id()
        if not job_id:
            QMessageBox.warning(self, "Scheduler", "削除する行を選択してください")
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
            snap = r.get("snapshot")
            self.refresh(snap=snap)  # 削除後に即更新（最重要、戻り値のsnapshotを使用）
        else:
            error = r.get("error", "unknown error")
            QMessageBox.warning(self, "Scheduler", f"削除に失敗: {error}")


