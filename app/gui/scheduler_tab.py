from __future__ import annotations

import json
import re
from functools import partial
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
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
    QTabWidget,
    QGroupBox,
)

from app.services.ai_service import get_model_metrics, get_active_model_meta
from app.services.ops_overview_facade import get_ops_overview
from app.services.condition_mining_facade import (
    get_condition_mining_ops_snapshot,
    get_condition_mining_window_settings,
    set_condition_mining_window_settings,
)
from app.services.recent_kpi import KPIService as RecentKPIService
from app.services.scheduler_facade import (
    get_scheduler_snapshot,
    add_scheduler_job,
    remove_scheduler_job,
    run_scheduler_job_now,
    start_scheduler_daemon,
    stop_scheduler_daemon,
    get_scheduler_daemon_status,
    open_scheduler_daemon_log,
)

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


def _status_icon_sp_from_next_action(next_action):
    # GUI表示専用: next_action(dict/str) から Statusカード用の標準アイコンを選ぶ（表示のみ）
    na = next_action
    if isinstance(na, dict):
        na = na.get('kind') or na.get('action') or na.get('next_action')
    if na is None:
        na = ''
    na = str(na).upper().strip()
    if na == 'PROMOTE':
        return 'SP_DialogApplyButton'
    if na == 'HOLD':
        return 'SP_MessageBoxInformation'
    if na == 'BLOCKED':
        return 'SP_MessageBoxCritical'
    return 'SP_MessageBoxQuestion'


class SchedulerTab(QWidget):

    def _make_ops_card(self, title: str, icon_sp: str = "SP_MessageBoxInformation"):
        # UI helper: Ops Overview の見出しカード（表示のみ）
        from PyQt6.QtWidgets import QGroupBox, QVBoxLayout, QHBoxLayout, QLabel
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtCore import Qt

        box = QGroupBox("", self)
        box.setStyleSheet(
            "QGroupBox { border: 1px solid #cfcfcf; border-radius: 6px; }"
        )

        root = QVBoxLayout(box)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # header row (icon + title)
        h = QHBoxLayout()
        h.setSpacing(8)

        try:
            sp = getattr(self.style().StandardPixmap, icon_sp)
            ico = self.style().standardIcon(sp).pixmap(16, 16)
        except Exception:
            ico = QPixmap()

        lbl_icon = QLabel(self)
        lbl_icon.setPixmap(ico)
        lbl_icon.setFixedSize(16, 16)

        lbl_title = QLabel(title, self)
        f = lbl_title.font()
        f.setBold(True)
        lbl_title.setFont(f)

        h.addWidget(lbl_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(lbl_title, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addStretch(1)

        root.addLayout(h)

        # content layout returned to caller
        v = QVBoxLayout()
        v.setSpacing(6)
        root.addLayout(v)

        return box, v

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

        # ---- Sub Tabs (T-43-3 Step2-12) ----
        self.tabs = QTabWidget(self)
        self.tab_overview = QWidget(self)
        self.tab_condition_mining = QWidget(self)
        self.tab_logs = QWidget(self)

        self.tabs.addTab(self.tab_overview, "Overview")
        self.tabs.addTab(self.tab_condition_mining, "Condition Mining")
        # ---- Condition Mining Settings UI (T-43-3 Step2-12) ----
        cm_root = QVBoxLayout(self.tab_condition_mining)
        cm_root.setContentsMargins(0, 0, 0, 0)
        cm_root.setSpacing(8)

        cm_form = QFormLayout()
        cm_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.cm_profile = QComboBox(self.tab_condition_mining)
        self.cm_profile.addItems(["demo", "real"])

        self.cm_recent = QSpinBox(self.tab_condition_mining)
        self.cm_recent.setRange(0, 10080)
        self.cm_recent.setSuffix(" min")

        self.cm_past = QSpinBox(self.tab_condition_mining)
        self.cm_past.setRange(0, 10080)
        self.cm_past.setSuffix(" min")

        self.cm_past_offset = QSpinBox(self.tab_condition_mining)
        self.cm_past_offset.setRange(0, 10080)
        self.cm_past_offset.setSuffix(" min")

        cm_form.addRow("profile", self.cm_profile)
        cm_form.addRow("recent_minutes", self.cm_recent)
        cm_form.addRow("past_minutes", self.cm_past)
        cm_form.addRow("past_offset_minutes", self.cm_past_offset)
        cm_root.addLayout(cm_form)

        self.cm_save_btn = QPushButton("保存してsnapshot即反映", self.tab_condition_mining)
        cm_root.addWidget(self.cm_save_btn)

        self.cm_snapshot = QPlainTextEdit(self.tab_condition_mining)
        self.cm_snapshot.setReadOnly(True)
        self.cm_snapshot.setMaximumBlockCount(2000)
        self.cm_snapshot.setMinimumHeight(220)
        cm_root.addWidget(self.cm_snapshot, 1)

        self.cm_profile.currentTextChanged.connect(self._cm_load_settings)
        self.cm_save_btn.clicked.connect(self._cm_save_settings)

        # 初期ロード
        self._cm_load_settings(self.cm_profile.currentText())
        # ---- /Condition Mining Settings UI ----

        self.tabs.addTab(self.tab_logs, "Logs")

        ov_root = QVBoxLayout(self.tab_overview)
        ov_root.setContentsMargins(0, 0, 0, 0)
        ov_root.setSpacing(6)

        lg_root = QVBoxLayout(self.tab_logs)
        lg_root.setContentsMargins(0, 0, 0, 0)
        lg_root.setSpacing(6)

        root.addWidget(self.tabs, 1)
        # ---- /Sub Tabs ----

        # ---- Overview Panel (T-42-3-13) ----
        self.overview_group = QGroupBox("Overview", self)
        # ---- T-43-3 Step2-13 UI: collapse Overview summary (display only) ----
        self.overview_group.setCheckable(True)
        self.overview_group.setChecked(False)

        def _on_overview_summary_toggled(on: bool):
            # UI only: really collapse by changing max height
            if on:
                # show contents
                for w in self.overview_group.findChildren(QWidget):
                    w.setVisible(True)
                self.overview_group.setMaximumHeight(16777215)
                self.overview_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            else:
                # hide contents
                for w in self.overview_group.findChildren(QWidget):
                    w.setVisible(False)
                # keep only header height
                header_h = self.overview_group.fontMetrics().height() + 18
                self.overview_group.setMaximumHeight(header_h)
                self.overview_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self.overview_group.toggled.connect(_on_overview_summary_toggled)
        _on_overview_summary_toggled(False)
        # ---- /collapse Overview summary ----
        overview_layout = QHBoxLayout()

        # Opsセクション
        ops_layout = QVBoxLayout()
        ops_label = QLabel("Ops", self)
        ops_label.setStyleSheet("font-weight: bold;")
        self.lbl_ops_month = QLabel("今月損益: -", self)
        self.lbl_ops_progress = QLabel("進捗: -", self)
        ops_layout.addWidget(ops_label)
        ops_layout.addWidget(self.lbl_ops_month)
        ops_layout.addWidget(self.lbl_ops_progress)

        # Schedulerセクション
        sched_layout = QVBoxLayout()
        sched_label = QLabel("Scheduler", self)
        sched_label.setStyleSheet("font-weight: bold;")
        self.lbl_sched_daemon = QLabel("daemon: -", self)
        self.lbl_sched_editable = QLabel("編集可否: -", self)
        self.lbl_sched_jobs = QLabel("ジョブ数: -", self)
        self.lbl_sched_next = QLabel("次回実行: -", self)
        sched_layout.addWidget(sched_label)
        sched_layout.addWidget(self.lbl_sched_daemon)
        sched_layout.addWidget(self.lbl_sched_editable)
        sched_layout.addWidget(self.lbl_sched_jobs)
        sched_layout.addWidget(self.lbl_sched_next)

        # AIセクション
        ai_layout = QVBoxLayout()
        ai_label = QLabel("AI", self)
        ai_label.setStyleSheet("font-weight: bold;")
        self.lbl_ai_model = QLabel("model: -", self)
        self.lbl_ai_trained = QLabel("trained_at: -", self)
        self.lbl_ai_threshold = QLabel("threshold: -", self)
        self.lbl_ai_features = QLabel("features: -", self)
        ai_layout.addWidget(ai_label)
        ai_layout.addWidget(self.lbl_ai_model)
        ai_layout.addWidget(self.lbl_ai_trained)
        ai_layout.addWidget(self.lbl_ai_threshold)
        ai_layout.addWidget(self.lbl_ai_features)

        overview_layout.addLayout(ops_layout)
        overview_layout.addSpacing(12)
        overview_layout.addLayout(sched_layout)
        overview_layout.addSpacing(12)
        overview_layout.addLayout(ai_layout)
        overview_layout.addStretch(1)

        self.overview_group.setLayout(overview_layout)
        ov_root.addWidget(self.overview_group)
        # ---- /Overview Panel ----

        # ---- Ops Overview Panel (T-42-3-14) ----
        self.ops_overview_box = QGroupBox("Ops Overview", self)
        # ---- T-43-3 Step2-13 UI: collapse Ops Overview details (display only) ----
        self.ops_overview_box.setCheckable(True)
        # 初期は「詳細を閉じる」：next_action / warnings だけ見せる
        self.ops_overview_box.setChecked(False)

        def _set_form_row_visible(form, row: int, visible: bool):
            # UI only: hide both label+field widgets so the row truly collapses
            try:
                lbl_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                fld_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                if lbl_item and lbl_item.widget():
                    lbl_item.widget().setVisible(visible)
                if fld_item and fld_item.widget():
                    fld_item.widget().setVisible(visible)
            except Exception:
                pass

        # 折りたたむ対象キー（フォームの左ラベル文字列で判定）
        _detail_keys = {
            "wfo_stability",
            "latest_retrain",
            "generated_at",
            "cm_recent_min_stats",
            "cm_recent_candidates",
            "cm_past_min_stats",
            "cm_past_candidates",
        }

        def _on_ops_overview_toggled(on: bool):
            # 表示のみ：OFF時は Status だけ、ON時は Status + Model Stability
            try:
                # checkable QGroupBox の「子をdisable」挙動を打ち消す（薄くなるのを防ぐ）
                self.ops_overview_box.setEnabled(True)
                for w in self.ops_overview_box.findChildren(QWidget):
                    w.setEnabled(True)
            except Exception:
                pass

            status = getattr(self, "ops_card_status", None)
            wfo = getattr(self, "ops_card_wfo", None)

            if status is not None:
                status.setVisible(True)
            if wfo is not None:
                wfo.setVisible(bool(on))

        self.ops_overview_box.toggled.connect(_on_ops_overview_toggled)
        _on_ops_overview_toggled(False)
        # ---- /collapse Ops Overview details ----

        # ---- T-43-3 Step2-13 UI only: ensure ops labels exist ----
        self.lbl_wfo = QLabel('-', self)
        self.lbl_retrain = QLabel('-', self)
        self.lbl_generated = QLabel('-', self)
        # ---- /ensure ops labels ----

        # ---- T-43-3 Step2-14: ensure next_action / warnings labels exist (display only) ----
        self.lbl_next_action = QLabel("-", self)
        self.lbl_next_action.setWordWrap(True)
        self.lbl_warnings = QLabel("warnings: -", self)
        self.lbl_warnings.setWordWrap(True)
        # ---- /ensure next_action / warnings ----

        self.lbl_cm_recent = QLabel("-", self)
        self.lbl_cm_recent_cand = QLabel("-", self)
        self.lbl_cm_past = QLabel("-", self)
        self.lbl_cm_past_cand = QLabel("-", self)


        # ---- T-43-3 Step2-14 UI: Ops Overview Card Layout (display only / CM separated) ----
        cards_root = QVBoxLayout(self.ops_overview_box)
        cards_root.setSpacing(8)

        # Status card: next_action + warnings + 次の一手（表示のみ）
        try:
            ops_snapshot = get_ops_overview() or {}
        except Exception:
            ops_snapshot = {}
        status_icon_sp = _status_icon_sp_from_next_action(ops_snapshot.get('next_action'))
        self.ops_card_status, v_status = self._make_ops_card("Status",
            icon_sp=status_icon_sp)
        v_status.addWidget(self.lbl_next_action)
        v_status.addWidget(self.lbl_warnings)

        na_row = QHBoxLayout()
        na_row.setSpacing(8)

        self.btn_next_action = QPushButton("次の一手", self)
        self.btn_next_action.setObjectName("btn_next_action")
        self.btn_next_action.setFlat(True)
        self.btn_next_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next_action.setEnabled(False)
        self.btn_next_action.setToolTip("表示のみ（将来：next_action 詳細へ誘導）")
        self.btn_next_action.setStyleSheet("QPushButton#btn_next_action { border: none; background: transparent; text-decoration: underline; padding: 0; }")
        na_row.addWidget(self.btn_next_action)

        self.btn_ops_open_logs = QPushButton("ログを開く", self)
        self.btn_ops_open_logs.setEnabled(False)
        self.btn_ops_open_logs.setToolTip("表示のみ（将来：Logsタブやlogs/を開く導線に接続）")

        self.btn_ops_open_settings = QPushButton("設定へ", self)
        self.btn_ops_open_settings.setEnabled(False)
        self.btn_ops_open_settings.setToolTip("表示のみ（将来：設定タブ導線に接続）")

        na_row.addWidget(self.btn_ops_open_logs)
        na_row.addWidget(self.btn_ops_open_settings)
        na_row.addStretch(1)
        v_status.addLayout(na_row)

        # Model card
        self.ops_card_wfo, v_wfo = self._make_ops_card("Model Stability", "SP_DriveHDIcon")
        v_wfo.addWidget(self.lbl_wfo)
        v_wfo.addWidget(self.lbl_retrain)
        v_wfo.addWidget(self.lbl_generated)

        cards_root.addWidget(self.ops_card_status)
        cards_root.addWidget(self.ops_card_wfo)
        cards_root.addStretch(1)

        ov_root.addWidget(self.ops_overview_box)
        # ---- /Ops Overview Card Layout ----
        # ---- /Ops Overview Panel ----

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
        # moved: ov_root.addLayout(row)
        # ---- Daemon Control UI (T-42-3-12) ----
        self.daemon_group = QGroupBox("常駐デーモン", self)
        dg = QHBoxLayout()

        self.btn_daemon_start = QPushButton("常駐開始", self)
        self.btn_daemon_stop = QPushButton("常駐停止", self)
        self.btn_daemon_log = QPushButton("ログを開く", self)

        self.lbl_daemon_running = QLabel("running: -", self)
        self.lbl_daemon_pid = QLabel("pid: -", self)
        self.lbl_daemon_started = QLabel("started_at: -", self)

        dg.addWidget(self.btn_daemon_start)
        dg.addWidget(self.btn_daemon_stop)
        dg.addWidget(self.btn_daemon_log)
        dg.addSpacing(12)
        dg.addWidget(self.lbl_daemon_running)
        dg.addWidget(self.lbl_daemon_pid)
        dg.addWidget(self.lbl_daemon_started)
        dg.addStretch(1)

        self.daemon_group.setLayout(dg)
        ov_root.addWidget(self.daemon_group)
        # ---- /Daemon Control UI ----

        # メインスプリッター（水平分割：左=ジョブテーブル、右=詳細ビュー）
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        lg_root.addWidget(main_splitter, 1)  # stretch=1 を明示
        # 左側：ジョブテーブル（タブ分割：スケジュール / 常時実行）
        jobs_tabs = QTabWidget(self)
        jobs_left = QWidget(self)
        jobs_left_layout = QVBoxLayout(jobs_left)
        jobs_left_layout.setContentsMargins(0, 0, 0, 0)
        jobs_left_layout.setSpacing(6)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_edit)
        row.addWidget(self.btn_remove)
        row.addStretch(1)
        jobs_left_layout.addLayout(row)

        jobs_left_layout.addWidget(jobs_tabs, 1)

        main_splitter.addWidget(jobs_left)
        # --- Tab: スケジュールジョブ ---
        tab_scheduled = QWidget(self)
        scheduled_layout = QVBoxLayout(tab_scheduled)
        scheduled_layout.setContentsMargins(0, 0, 0, 0)
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
        scheduled_layout.addWidget(self.table_scheduled, 1)

        jobs_tabs.addTab(tab_scheduled, "Scheduled")

        # --- Tab: 常時実行ジョブ ---
        tab_always = QWidget(self)
        always_layout = QVBoxLayout(tab_always)
        always_layout.setContentsMargins(0, 0, 0, 0)
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
        always_layout.addWidget(self.table_always, 1)

        jobs_tabs.addTab(tab_always, "Always")

        # デーモン操作のシグナル接続
        self.btn_daemon_start.clicked.connect(self._on_daemon_start)
        self.btn_daemon_stop.clicked.connect(self._on_daemon_stop)
        self.btn_daemon_log.clicked.connect(self._on_daemon_open_log)

        # デーモン状態の自動更新（QTimer）
        # タイマーは showEvent で start / hideEvent で stop
        self._daemon_timer = QTimer(self)
        self._daemon_timer.setInterval(1000)  # 1s
        self._daemon_timer.timeout.connect(self._refresh_daemon_status)

        # 初回表示用に1回だけ
        self._refresh_daemon_status()

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
        main_splitter.setSizes([300, 200])  # ジョブテーブル:詳細 = 3:2

        # 最後のsnapshotを保持（詳細表示用）
        self._last_snapshot: dict | None = None

        # KPIサービスを初期化
        self._recent_kpi = RecentKPIService()

        # 初回描画
        self.refresh()

    def _fmt_candidates(self, items):
        try:
            arr = items or []
            top = arr[:3]
            if not top:
                return "-"
            parts = []
            for x in top:
                if isinstance(x, dict):
                    parts.append(str(x.get("reason")) + "(" + str(x.get("count")) + ")")
            return " / ".join(parts) if parts else "-"
        except Exception:
            return "-"

    # ----------------------------
    # Condition Mining tab helpers
    # ----------------------------
    def _cm_load_settings(self, profile: str) -> None:
        """profile別 window 設定を読み込み、SpinBoxへ反映し、snapshotも更新する。"""
        try:
            win = get_condition_mining_window_settings(profile=profile) or {}
        except Exception:
            win = {}

        rv = win.get("recent_minutes")
        pv = win.get("past_minutes")
        ov = win.get("past_offset_minutes")

        if rv is not None:
            self.cm_recent.setValue(int(rv))
        if pv is not None:
            self.cm_past.setValue(int(pv))
        if ov is not None:
            self.cm_past_offset.setValue(int(ov))

        self._cm_refresh_snapshot()

    def _cm_save_settings(self) -> None:
        """保存→snapshot再取得→即反映（GUI再起動なし）。"""
        profile = (self.cm_profile.currentText() or "").strip() or None
        patch = {
            "recent_minutes": int(self.cm_recent.value()),
            "past_minutes": int(self.cm_past.value()),
            "past_offset_minutes": int(self.cm_past_offset.value()),
        }
        try:
            set_condition_mining_window_settings(patch=patch, profile=profile)
        except Exception as e:
            self.cm_snapshot.setPlainText(f"[save_failed] {e}")
            return

        self._cm_refresh_snapshot()

    def _cm_refresh_snapshot(self) -> None:
        """現在の設定で snapshot を取得し、JSONとして表示。"""
        profile = (self.cm_profile.currentText() or "").strip() or None
        try:
            snap = get_condition_mining_ops_snapshot(symbol="USDJPY-", profile=profile) or {}
        except Exception as e:
            self.cm_snapshot.setPlainText(f"[snapshot_failed] {e}")
            return

        try:
            txt = json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            txt = str(snap)
        self.cm_snapshot.setPlainText(txt)

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

        # Overviewパネルを更新
        self._refresh_overview(snap)

        # Ops Overviewパネルを更新
        self._refresh_ops_overview()

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

                self.tabs.setCurrentWidget(self.tab_logs)
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

    # ---- Overview Panel (T-42-3-13) ----
    def _refresh_overview(self, snap: dict) -> None:
        """Overviewパネルを更新（Ops/Scheduler/AI）"""
        # Ops: 今月の運用KPI
        try:
            kpi = self._recent_kpi.get_kpi(profile="default")
            month_ret = kpi.get("current_month_return_pct", 0.0)
            max_dd = kpi.get("max_monthly_dd_pct", 0.0)
            self.lbl_ops_month.setText(f"今月損益: {month_ret:.2f}% (DD: {max_dd:.2f}%)")
            # 進捗は簡易的に月次リターン系列の長さで表現（必要に応じて拡張）
            monthly_list = kpi.get("monthly_returns", [])
            progress = len(monthly_list)
            self.lbl_ops_progress.setText(f"進捗: {progress}ヶ月分")
        except Exception as e:
            self.lbl_ops_month.setText("今月損益: (n/a)")
            self.lbl_ops_progress.setText(f"進捗: (error: {e})")

        # Scheduler: daemon稼働/編集可否/ジョブ数/次回実行
        try:
            daemon_status = get_scheduler_daemon_status()
            daemon_running = bool(daemon_status.get("running"))
            self.lbl_sched_daemon.setText(f"daemon: {'稼働中' if daemon_running else '停止'}")

            can_edit = bool(snap.get("can_edit"))
            self.lbl_sched_editable.setText(f"編集可否: {'可' if can_edit else '不可'}")

            jobs = snap.get("jobs", [])
            self.lbl_sched_jobs.setText(f"ジョブ数: {len(jobs)}")

            # 次回実行時刻（最も近いnext_run_atを探す）
            next_runs = [j.get("next_run_at") for j in jobs if j.get("next_run_at")]
            if next_runs:
                next_run = min(next_runs)
                self.lbl_sched_next.setText(f"次回実行: {next_run}")
            else:
                self.lbl_sched_next.setText("次回実行: -")
        except Exception as e:
            self.lbl_sched_daemon.setText("daemon: (n/a)")
            self.lbl_sched_editable.setText("編集可否: (n/a)")
            self.lbl_sched_jobs.setText("ジョブ数: (n/a)")
            self.lbl_sched_next.setText("次回実行: (n/a)")

        # AI: active model情報
        try:
            model_info = get_model_metrics() or {}
            meta = get_active_model_meta() or {}

            # 表示名: model_name があればそれ、無ければ model_path の末尾などを表示
            model_name = model_info.get("model_name")
            model_path = model_info.get("model_path")
            if model_name:
                model_disp = str(model_name)
            elif model_path:
                model_disp = str(model_path)
            else:
                model_disp = "(n/a)"
            self.lbl_ai_model.setText(f"model: {model_disp}")

            trained_at = model_info.get("trained_at") or "(n/a)"
            self.lbl_ai_trained.setText(f"trained_at: {trained_at}")

            threshold = model_info.get("best_threshold")
            if threshold is not None:
                self.lbl_ai_threshold.setText(f"threshold: {float(threshold):.3f}")
            else:
                self.lbl_ai_threshold.setText("threshold: (n/a)")

            # features: active meta の feature_order / features を最優先
            ef = meta.get("feature_order") or meta.get("features")
            if isinstance(ef, list):
                self.lbl_ai_features.setText(f"features: {len(ef)}")
            else:
                # フォールバック（あれば）
                features_count = meta.get("expected_features_count")
                if features_count is None:
                    features_count = meta.get("n_features")
                if features_count is not None:
                    self.lbl_ai_features.setText(f"features: {features_count}")
                else:
                    self.lbl_ai_features.setText("features: (n/a)")
        except Exception:
            self.lbl_ai_model.setText("model: (n/a)")
            self.lbl_ai_trained.setText("trained_at: (n/a)")
            self.lbl_ai_threshold.setText("threshold: (n/a)")
            self.lbl_ai_features.setText("features: (n/a)")
    # ---- /Overview Panel ----

    # ---- Ops Overview Panel (T-42-3-14) ----
    def _refresh_ops_overview(self) -> None:
        """Ops Overviewパネルを更新（next_action/wfo_stability/latest_retrain）"""
        try:
            o = get_ops_overview()
            # --- T-42-3-18 Step 4-3: condition_mining min_stats ---
            try:
                symbol = "USDJPY-"  # 仕様: symbol は USDJPY-
                win = get_condition_mining_window_settings()
                out = get_condition_mining_ops_snapshot(
                    symbol=symbol,
                    **win,
                )
                # --- T-43-3 Step2-10: show all-fallback & window mismatch in UI (labels) ---
                evw = ((out.get("evidence") or {}).get("window") or {})
                cm_mode = evw.get("mode")  # "recent_past" / "all_fallback"
                cm_warn_mismatch = "window_range_mismatch" in (out.get("warnings") or [])

                r = (out.get("recent") or {}).get("min_stats") or {}
                p2 = (out.get("past") or {}).get("min_stats") or {}

                def _fmt(ms: dict) -> str:
                    total = ms.get("total", 0)
                    fpc = ms.get("filter_pass_count", 0)
                    fpr = float(ms.get("filter_pass_rate", 0.0))
                    ec = ms.get("entry_count", 0)
                    er = float(ms.get("entry_rate", 0.0))
                    return f"total={total}  filter_pass={fpc} ({fpr:.1%})  entry={ec} ({er:.1%})"

                txt_r = _fmt(r) if r else "-"
                tags = []
                if cm_mode == "all_fallback":
                    tags.append("[ALL]")
                if cm_warn_mismatch:
                    tags.append("[WARN]")
                if tags:
                    txt_r = txt_r + " " + " ".join(tags)
                self.lbl_cm_recent.setText(txt_r)
                txt_p = _fmt(p2) if p2 else "-"
                tags = []
                if cm_mode == "all_fallback":
                    tags.append("[ALL]")
                if cm_warn_mismatch:
                    tags.append("[WARN]")
                if tags:
                    txt_p = txt_p + " " + " ".join(tags)
                self.lbl_cm_past.setText(txt_p)
                # --- T-42-3-22: condition_mining candidates ---
                try:
                    # get_condition_candidates は未実装のため、エラーハンドリングで対応
                    # 将来的に実装された場合はここで呼び出す
                    self.lbl_cm_recent_cand.setText("-")
                    self.lbl_cm_past_cand.setText("-")
                except Exception:
                    self.lbl_cm_recent_cand.setText("-")
                    self.lbl_cm_past_cand.setText("-")
                # --- /T-42-3-22 ---
            except Exception:
                self.lbl_cm_recent.setText("-")
                self.lbl_cm_past.setText("-")
                self.lbl_cm_recent_cand.setText("-")
                self.lbl_cm_past_cand.setText("-")
            # --- /T-42-3-18 Step 4-3 ---
        except Exception as e:
            self.lbl_next_action.setText(f"ERROR: {e}")
            self.lbl_wfo.setText("-")
            self.lbl_retrain.setText("-")
            self.lbl_generated.setText("-")
            self.lbl_warnings.setText("warnings: (ERROR)")
            self.lbl_warnings.setStyleSheet("border-radius:10px; padding:6px 10px; font-weight:700; background:#633; color:#ffe;")
            return

        na = o.get("next_action") or {}
        ws = o.get("wfo_stability") or {}
        lr = o.get("latest_retrain") or {}

        # next_action
        kind = na.get("kind", "-")

        # ---- T-43-3 Step2-13 UI: warnings highlight (display only) ----
        warnings = o.get("warnings") or []
        n_warn = len(warnings)
        if n_warn == 0:
            self.lbl_warnings.setText("warnings: 0 (OK)")
            self.lbl_warnings.setStyleSheet("border-radius:10px; padding:6px 10px; font-weight:700; background:#233; color:#e8f6ff;")
        else:
            head = str(warnings[0])
            self.lbl_warnings.setText(f"warnings: {n_warn}\n- {head}")
            self.lbl_warnings.setStyleSheet("border-radius:10px; padding:6px 10px; font-weight:700; background:#633; color:#ffe;")


        # ---- T-43-3 Step2-13 UI: next_action badge color (display only) ----
        try:
            _k = str(kind)
            if _k == "PROMOTE":
                self.lbl_next_action.setStyleSheet("border-radius:10px; padding:6px 10px; font-weight:700; background:#163; color:#efe;")
            elif _k == "HOLD":
                self.lbl_next_action.setStyleSheet("border-radius:10px; padding:6px 10px; font-weight:700; background:#542; color:#ffe;")
            elif _k == "BLOCKED":
                self.lbl_next_action.setStyleSheet("border-radius:10px; padding:6px 10px; font-weight:700; background:#611; color:#fee;")
            else:
                self.lbl_next_action.setStyleSheet("border-radius:10px; padding:6px 10px; font-weight:700; background:#333; color:#eee;")
        except Exception:
            pass


        priority = na.get("priority", "-")
        reason = na.get("reason", "")
        self.lbl_next_action.setText(f"{kind} (prio={priority})\n{reason}")

        # wfo_stability
        reasons = ws.get("reasons") or []
        reasons_s = "; ".join([str(x) for x in reasons]) if reasons else "-"
        stable = ws.get("stable", "-")
        score = ws.get("score", "-")
        run_id = ws.get("run_id", "-")
        self.lbl_wfo.setText(f"stable={stable} score={score} run_id={run_id}\nreasons: {reasons_s}")

        # latest_retrain
        # data_range / threshold は dict のまま来る可能性があるので JSON 文字列にして潰す
        dr = lr.get("data_range")
        th = lr.get("threshold")
        dr_s = json.dumps(dr, ensure_ascii=False) if dr is not None else "-"
        th_s = json.dumps(th, ensure_ascii=False) if th is not None else "-"
        lr_run_id = lr.get("run_id", "-")
        self.lbl_retrain.setText(f"run_id={lr_run_id}\ndata_range={dr_s}\nthreshold={th_s}")

        self.lbl_generated.setText(str(o.get("generated_at", "-")))
    # ---- /Ops Overview Panel ----

    # ---- Daemon Control Handlers (T-42-3-12) ----
    def _refresh_daemon_status(self) -> None:
        """デーモンの状態を取得してUIに反映"""
        st = get_scheduler_daemon_status() or {}
        running = bool(st.get("running"))
        pid = st.get("pid")
        started_at = st.get("started_at")

        self.lbl_daemon_running.setText(f"running: {running}")
        self.lbl_daemon_pid.setText(f"pid: {pid if pid is not None else '-'}")
        self.lbl_daemon_started.setText(f"started_at: {started_at or '-'}")

        # ボタン活性も自然に
        self.btn_daemon_start.setEnabled(not running)
        self.btn_daemon_stop.setEnabled(running)

    def _on_daemon_start(self) -> None:
        """常駐デーモンを開始"""
        res = start_scheduler_daemon()
        if not res.get("ok"):
            error = res.get("error", "unknown error")
            QMessageBox.warning(self, "Scheduler", f"常駐開始に失敗: {error}")
        else:
            QMessageBox.information(self, "Scheduler", "常駐デーモンを開始しました")
        self._refresh_daemon_status()

    def _on_daemon_stop(self) -> None:
        """常駐デーモンを停止"""
        res = stop_scheduler_daemon()
        if not res.get("ok"):
            error = res.get("error", "unknown error")
            QMessageBox.warning(self, "Scheduler", f"常駐停止に失敗: {error}")
        else:
            QMessageBox.information(self, "Scheduler", "常駐デーモンを停止しました")
        self._refresh_daemon_status()

    def _on_daemon_open_log(self) -> None:
        """デーモンログを開く"""
        res = open_scheduler_daemon_log()
        if not res.get("ok"):
            error = res.get("error", "unknown error")
            QMessageBox.warning(self, "Scheduler", f"ログを開けませんでした: {error}")
    # ---- /Daemon Control Handlers ----

    def showEvent(self, event) -> None:
        """タブが表示されたときにタイマーを開始"""
        super().showEvent(event)
        if hasattr(self, "_daemon_timer") and self._daemon_timer is not None:
            if not self._daemon_timer.isActive():
                self._daemon_timer.start()
        # 表示直後に最新化
        self._refresh_daemon_status()
        self._refresh_ops_overview()

    def hideEvent(self, event) -> None:
        """タブが非表示になったときにタイマーを停止"""
        super().hideEvent(event)
        if hasattr(self, "_daemon_timer") and self._daemon_timer is not None:
            if self._daemon_timer.isActive():
                self._daemon_timer.stop()
