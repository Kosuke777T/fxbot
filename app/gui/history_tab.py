from __future__ import annotations

from typing import Callable, List, Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QHeaderView, QScrollArea, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QWidget

from loguru import logger

from app.services.event_store import EVENT_STORE, UiEvent
from app.services.ops_history_service import get_ops_history_service
from app.gui.ops_ui_rules import (
    format_action_hint_text,
    format_condition_mining_evidence_text,
    ui_for_next_action,
    get_action_priority,
)

_COLUMNS = ["ts", "kind", "symbol", "side", "price", "sl", "tp", "profit_jpy", "reason", "notes"]


class _OpsHistoryFetchWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(object)  # items
    failed = QtCore.pyqtSignal(str)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.finished.emit(self._fn())
        except Exception as e:
            self.failed.emit(str(e))


class HistoryTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # T-43-8 Step3(A): CM evidence display in History can be heavy; default OFF.
        self._cm_heavy_enabled: bool = False
        # 表示のみ：CM整形を最新N件に限定（重さ軽減）
        self._cm_render_limit: int = 5
        self._cm_fetch_inflight: bool = False
        self._cm_thread: Optional[QtCore.QThread] = None
        self._cm_worker: Optional[_OpsHistoryFetchWorker] = None
        self._cm_loading_timer: Optional[QtCore.QElapsedTimer] = None
        self._cm_last_updated_str: str = ""

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

        # Controls row (display-only)
        controls_row = QtWidgets.QHBoxLayout()
        self.chk_cm_heavy = QtWidgets.QCheckBox("CM表示（重い）")
        self.chk_cm_heavy.setChecked(False)
        self.chk_cm_heavy.toggled.connect(self._on_toggle_cm_heavy)
        controls_row.addWidget(self.chk_cm_heavy)

        # 左下（チェック行）にローディング表示：確実に目に入る位置
        self.lbl_cm_loading_left = QLabel("CM読込中…")
        self.lbl_cm_loading_left.setStyleSheet("color: #888; font-size: 9pt;")
        self.lbl_cm_loading_left.setVisible(False)
        controls_row.addWidget(self.lbl_cm_loading_left)

        lbl_cm_limit = QLabel("CM表示件数")
        lbl_cm_limit.setToolTip("CM整形を行うカード件数。反映は次回『CM再読込』から")
        controls_row.addWidget(lbl_cm_limit)

        self.spn_cm_limit = QtWidgets.QSpinBox()
        self.spn_cm_limit.setRange(1, 50)
        self.spn_cm_limit.setSingleStep(1)
        self.spn_cm_limit.setValue(int(getattr(self, "_cm_render_limit", 5)))
        self.spn_cm_limit.setToolTip("CM整形を行うカード件数。反映は次回『CM再読込』から")
        self.spn_cm_limit.valueChanged.connect(self._on_change_cm_render_limit)
        controls_row.addWidget(self.spn_cm_limit)

        # CM ON中のみ手動で再読込（QThread heavy fetch を再実行）
        self.btn_cm_reload = QtWidgets.QPushButton("CM再読込")
        self.btn_cm_reload.setEnabled(False)
        self.btn_cm_reload.clicked.connect(self._on_click_cm_reload)
        controls_row.addWidget(self.btn_cm_reload)

        # CM ON中は自動更新停止＋最終更新を表示（表示のみ）
        self.lbl_cm_status = QLabel("", self)
        self.lbl_cm_status.setStyleSheet("color: #888; font-size: 9pt;")
        self.lbl_cm_status.setVisible(False)
        controls_row.addWidget(self.lbl_cm_status)

        controls_row.addStretch(1)

        self.btnExport = QtWidgets.QPushButton("Export CSV")
        self.btnExport.clicked.connect(self._export_csv)
        controls_row.addWidget(self.btnExport)

        left_layout.addWidget(self.table)
        left_layout.addLayout(controls_row)
        splitter.addWidget(left_widget)

        # 右側：Ops履歴カード表示
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        ops_label = QLabel("Ops履歴", right_widget)
        ops_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        right_layout.addWidget(ops_label)

        self.lbl_cm_loading = QLabel("CM読込中…", right_widget)
        self.lbl_cm_loading.setStyleSheet("color: #888; font-size: 9pt;")
        self.lbl_cm_loading.setVisible(False)
        right_layout.addWidget(self.lbl_cm_loading)

        # スクロール可能なカードエリア
        scroll_area = QScrollArea(right_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)  # 暫定：必要なら出す

        self.ops_cards_widget = QWidget()
        self.ops_cards_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum)
        self.ops_cards_layout = QVBoxLayout(self.ops_cards_widget)
        self.ops_cards_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.ops_cards_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area.setWidget(self.ops_cards_widget)

        right_layout.addWidget(scroll_area)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addWidget(splitter)

        self._timer = QtCore.QTimer(self)
        # T-43-6: GUIの定期更新は秒単位で叩かない（UIフリーズ防止）。
        # UiEventは1秒でも良いが、ops_history要約は重くなり得るため間引く。
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.refresh()

    def _on_toggle_cm_heavy(self, checked: bool) -> None:
        """UI-only: toggle heavy CM evidence fetch for History cards."""
        logger.info(f"[history] cm_heavy_toggled checked={checked} inflight={self._cm_fetch_inflight}")
        self._cm_heavy_enabled = bool(checked)
        # CM ON中は定期更新を抑制（UIフリーズ再発防止）
        try:
            self._timer.setInterval(30000 if checked else 5000)
        except Exception:
            pass
        # CM再読込ボタンの有効/無効
        try:
            self.btn_cm_reload.setEnabled(bool(checked) and (not self._cm_fetch_inflight))
        except Exception:
            pass
        # 自動更新停止ステータスの表示
        try:
            if checked:
                last = self._cm_last_updated_str or "未更新"
                self.lbl_cm_status.setText(f"CM表示中：自動更新停止（最終更新: {last}）")
                self.lbl_cm_status.setVisible(True)
            else:
                self.lbl_cm_status.setVisible(False)
        except Exception:
            pass
        if checked:
            self._start_cm_heavy_fetch()
            return

        # OFF時は従来通り軽量（同期）で即時反映
        self._hide_cm_loading_with_min_duration()
        try:
            self._refresh_ops_cards()
        except Exception as e:
            logger.error(f"Failed to refresh ops cards after CM toggle: {e}")

    def _on_click_cm_reload(self) -> None:
        """CM ON中のみ、heavy fetch を手動で再実行する（表示のみ）。"""
        logger.info(f"[history] cm_heavy_reload_click inflight={self._cm_fetch_inflight}")
        try:
            if not self.chk_cm_heavy.isChecked():
                return
        except Exception:
            return
        # トグル処理は呼ばず、fetchだけ再実行する
        self._start_cm_heavy_fetch()

    def _on_change_cm_render_limit(self, v: int) -> None:
        """表示のみ：CM整形を行うカード件数（次回再読込から反映）。"""
        try:
            self._cm_render_limit = int(v)
        except Exception:
            self._cm_render_limit = 5
        logger.info(f"[history] cm_render_limit_changed v={v} (apply on next reload)")

    def _on_cm_thread_finished(self) -> None:
        self._cm_thread = None
        self._cm_worker = None

    def _start_cm_heavy_fetch(self) -> None:
        """CM heavy fetch（QThread）開始。トグル副作用は持たない。"""
        if self._cm_fetch_inflight:
            return
        self._cm_fetch_inflight = True
        try:
            self.chk_cm_heavy.setEnabled(False)
        except Exception:
            pass
        try:
            self.btn_cm_reload.setEnabled(False)
        except Exception:
            pass
        try:
            # 左側ラベルを優先して表示（見えない問題の確実な解消）
            self.lbl_cm_loading_left.setVisible(True)
            # 右側は残すが、主表示は左側
            self.lbl_cm_loading.setVisible(True)
            try:
                # Ensure the loading label is painted before starting the background thread.
                QtWidgets.QApplication.processEvents()
            except Exception:
                pass
            self._cm_loading_timer = QtCore.QElapsedTimer()
            self._cm_loading_timer.start()
        except Exception:
            pass

        def _fetch_items() -> object:
            logger.info("[history] cm_heavy_fetch: summarize_ops_history(include_cm=True) begin")
            history_service = get_ops_history_service()
            summary = history_service.summarize_ops_history(
                cache_sec=60,
                include_condition_mining=True,
            )
            items = summary.get("items", [])
            try:
                n = len(items)  # type: ignore[arg-type]
            except Exception:
                n = -1
            logger.info(f"[history] cm_heavy_fetch: summarize_ops_history end items={n}")
            return items

        try:
            thread = QtCore.QThread(self)
            worker = _OpsHistoryFetchWorker(_fetch_items)
            worker.moveToThread(thread)

            thread.started.connect(worker.run)
            worker.finished.connect(self._on_cm_fetch_done)
            worker.failed.connect(self._on_cm_fetch_fail)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            thread.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._on_cm_thread_finished)

            self._cm_thread = thread
            self._cm_worker = worker
            logger.info("[history] cm_heavy_fetch: starting QThread")
            thread.start()
            logger.info("[history] cm_heavy_fetch: QThread started")
            return
        except Exception as e:
            # 例外安全：起動に失敗してもUIを復帰させる
            self._cm_fetch_inflight = False
            try:
                self.chk_cm_heavy.setEnabled(True)
            except Exception:
                pass
            self._hide_cm_loading_with_min_duration()
            logger.error(f"Failed to start CM fetch thread: {e}")
            # 起動失敗時は重い表示に入らない（同期heavyに落ちて固まるのを防ぐ）
            self._on_cm_fetch_fail(str(e))

    def _hide_cm_loading_with_min_duration(self) -> None:
        """CM読込中…を最低0.5秒は表示した上で隠す（UI復帰は遅延しない）。"""
        try:
            t = self._cm_loading_timer
            elapsed = int(t.elapsed()) if t is not None else 10_000
        except Exception:
            elapsed = 10_000
        remaining = 500 - elapsed
        if remaining > 0:
            # 500ms経つまでは必ず表示を維持（OFF/完了/失敗で先に消されないようにする）
            try:
                self.lbl_cm_loading_left.setVisible(True)
            except Exception:
                pass
            try:
                self.lbl_cm_loading.setVisible(True)
            except Exception:
                pass
            QtCore.QTimer.singleShot(
                remaining,
                lambda: self._hide_cm_loading_with_min_duration(),
            )
            return

        self._cm_loading_timer = None
        try:
            self.lbl_cm_loading_left.setVisible(False)
        except Exception:
            pass
        try:
            self.lbl_cm_loading.setVisible(False)
        except Exception:
            pass

    def _on_cm_fetch_done(self, items_obj: object) -> None:
        try:
            n = len(items_obj)  # type: ignore[arg-type]
        except Exception:
            n = -1
        try:
            checked_now = self.chk_cm_heavy.isChecked()
        except Exception:
            checked_now = False
        logger.info(f"[history] cm_heavy_fetch_done items={n} checked_now={checked_now}")
        self._cm_fetch_inflight = False
        try:
            self.chk_cm_heavy.setEnabled(True)
        except Exception:
            pass
        try:
            self.btn_cm_reload.setEnabled(bool(self._cm_heavy_enabled))
        except Exception:
            pass
        self._hide_cm_loading_with_min_duration()

        # 状態尊重：完了時点でOFFなら結果は捨てて軽量refreshへ
        try:
            if not self.chk_cm_heavy.isChecked():
                self._cm_heavy_enabled = False
                self._refresh_ops_cards()
                return
        except Exception:
            self._cm_heavy_enabled = False
            try:
                self._refresh_ops_cards()
            except Exception:
                pass
            return

        items = items_obj if isinstance(items_obj, list) else []
        try:
            logger.info(f"[history] cm_render_limit={self._cm_render_limit} items={len(items)}")
        except Exception:
            pass
        # 表示のみ：最終更新時刻を更新
        try:
            self._cm_last_updated_str = QtCore.QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
            self.lbl_cm_status.setText(f"CM表示中：自動更新停止（最終更新: {self._cm_last_updated_str}）")
            self.lbl_cm_status.setVisible(True)
        except Exception:
            pass
        try:
            self._render_ops_cards(items)
        except Exception as e:
            logger.error(f"Failed to render ops cards after CM fetch: {e}")

    def _on_cm_fetch_fail(self, msg: str) -> None:
        logger.error(f"[history] cm_heavy_fetch_fail: {msg}")
        self._cm_fetch_inflight = False
        try:
            self.chk_cm_heavy.setEnabled(True)
        except Exception:
            pass
        try:
            self.btn_cm_reload.setEnabled(False)
        except Exception:
            pass
        self._hide_cm_loading_with_min_duration()

        logger.error(f"CM fetch failed: {msg}")
        try:
            self.chk_cm_heavy.setToolTip(f"CM取得失敗: {msg}")
        except Exception:
            pass

        # 分かりやすさ優先：失敗時はOFFに戻す（例外で落とさない）
        try:
            blocker = QtCore.QSignalBlocker(self.chk_cm_heavy)
            self.chk_cm_heavy.setChecked(False)
            del blocker
        except Exception:
            try:
                self.chk_cm_heavy.setChecked(False)
            except Exception:
                pass

        self._cm_heavy_enabled = False
        # シグナルをブロックしてOFFに戻すため、ここでintervalも復帰させる
        try:
            self._timer.setInterval(5000)
        except Exception:
            pass
        try:
            self.lbl_cm_status.setVisible(False)
        except Exception:
            pass
        try:
            self._refresh_ops_cards()
        except Exception as e:
            logger.error(f"Failed to refresh ops cards after CM fetch fail: {e}")

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
        # CM ON中は定期更新で重いservices呼び出しをしない（UiEventのみ更新）
        if bool(getattr(self, "_cm_heavy_enabled", False)):
            return
        self._refresh_ops_cards()

    def _refresh_ops_cards(self) -> None:
        """Ops履歴カードを更新する。"""
        # バックグラウンド取得中は定期更新が割り込まないようにする（多重実行防止）
        if self._cm_fetch_inflight and bool(getattr(self, "_cm_heavy_enabled", False)):
            return
        try:
            history_service = get_ops_history_service()
            # T-43-8: default OFF for CM evidence (heavy); enable only by explicit user toggle.
            include_cm = bool(getattr(self, "_cm_heavy_enabled", False))
            cache_sec = 60 if include_cm else 5
            summary = history_service.summarize_ops_history(
                cache_sec=cache_sec,
                include_condition_mining=include_cm,
            )
            items = summary.get("items", [])
            last_view = summary.get("last_view")  # 最新の表示用ビュー（現在は未使用だが取得しておく）

            # keep last_view local for now (unused); avoids breaking existing behavior
            _ = last_view
            self._render_ops_cards(items)
        except Exception as e:
            logger.error(f"Failed to refresh ops cards: {e}")

    def _render_ops_cards(self, items: list) -> None:
        # 既存のカードをクリア
        while self.ops_cards_layout.count():
            child = self.ops_cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # itemsはservices層で既にソート済み（priority降順、started_at降順、record_idで安定化）
        # カードを生成（services層のソート順を維持）
        for idx, item in enumerate(items):
            card = self._create_ops_card(item, idx=idx)
            if card:
                self.ops_cards_layout.addWidget(card)

        # スペーサーを追加
        self.ops_cards_layout.addStretch()

    def _create_ops_card(self, item: dict, idx: int = 0) -> Optional[QWidget]:
        """Ops履歴カードを作成する。"""
        try:
            card = QGroupBox()
            card.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
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
            headline_label.setWordWrap(True)
            headline_label.setMinimumWidth(0)
            headline_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
            header_layout.addWidget(headline_label, 1)

            card_layout.addLayout(header_layout)

            # subline
            subline = item.get("subline", "")
            if subline:
                subline_label = QLabel(subline)
                subline_label.setStyleSheet("color: #666; font-size: 9pt;")
                subline_label.setWordWrap(True)
                subline_label.setMinimumWidth(0)
                subline_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
                card_layout.addWidget(subline_label)

            # timeline
            timeline = item.get("timeline", {})
            timeline_text = self._format_timeline(timeline)
            if timeline_text:
                timeline_label = QLabel(timeline_text)
                timeline_label.setStyleSheet("color: #888; font-size: 8pt;")
                timeline_label.setWordWrap(True)
                timeline_label.setMinimumWidth(0)
                timeline_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
                card_layout.addWidget(timeline_label)

            # diff
            diff = item.get("diff", {})
            if diff:
                diff_text = self._format_diff(diff)
                if diff_text:
                    diff_label = QLabel(diff_text)
                    diff_label.setStyleSheet("color: #0066cc; font-size: 8pt;")
                    diff_label.setWordWrap(True)
                    diff_label.setMinimumWidth(0)
                    diff_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
                    card_layout.addWidget(diff_label)

            # 行動ヒント（next_action）
            next_action = item.get("next_action")
            if next_action:
                spec = ui_for_next_action(next_action)
                if spec.visible:
                    # 表示テキストを生成（reasonは説明表示にのみ使用）
                    hint_text = format_action_hint_text(next_action)
                    if hint_text:
                        include_cm = bool(getattr(self, "_cm_heavy_enabled", False))
                        cm_limit = int(getattr(self, "_cm_render_limit", 5))
                        allow_cm_render = bool(include_cm and (idx < cm_limit))

                        # T-43-8 Step3(A): unify CM evidence rendering with Ops (Scheduler)
                        # - Add-only: if CM evidence exists, append summary to the one-line text.
                        # - Never crash UI if evidence is missing/partial.
                        cm_view: Optional[dict] = None
                        if allow_cm_render:
                            try:
                                cm_view = format_condition_mining_evidence_text(next_action, top_n=3) or {}
                            except Exception:
                                cm_view = {}

                        disp_text = str(hint_text)
                        try:
                            if allow_cm_render and isinstance(cm_view, dict) and bool(cm_view.get("has")):
                                cm_sum = cm_view.get("summary")
                                if isinstance(cm_sum, str) and cm_sum.strip():
                                    disp_text = disp_text + " | " + cm_sum.strip()
                        except Exception:
                            pass

                        next_action_label = QLabel(hint_text)
                        next_action_label.setStyleSheet(spec.style + " font-size: 9pt;")
                        next_action_label.setWordWrap(True)
                        next_action_label.setMinimumWidth(0)
                        next_action_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
                        # tooltipにreasonを設定
                        reason = next_action.get("reason", "")
                        try:
                            tip_lines: list[str] = []
                            if reason:
                                tip_lines.append(f"{spec.tooltip_prefix}{reason}")

                            # Append CM details into tooltip (top_lines + warnings), same formatter as Ops.
                            if allow_cm_render and isinstance(cm_view, dict) and bool(cm_view.get("has")):
                                top_lines = cm_view.get("top_lines") or []
                                warn_lines = cm_view.get("warnings") or []
                                if not isinstance(top_lines, list):
                                    top_lines = []
                                if not isinstance(warn_lines, list):
                                    warn_lines = []

                                if top_lines:
                                    tip_lines.append("")
                                    tip_lines.append("CM:")
                                    for x in top_lines[:5]:
                                        tip_lines.append("- " + str(x))
                                if warn_lines:
                                    tip_lines.append("")
                                    tip_lines.append("CM warnings:")
                                    for x in warn_lines[:5]:
                                        tip_lines.append("- " + str(x))

                            if tip_lines:
                                next_action_label.setToolTip("\n".join([str(x) for x in tip_lines if x is not None]))
                        except Exception:
                            # fallback to legacy tooltip
                            if reason:
                                next_action_label.setToolTip(f"{spec.tooltip_prefix}{reason}")

                        # set final display text (may include CM summary)
                        try:
                            next_action_label.setText(disp_text)
                        except Exception:
                            pass
                        card_layout.addWidget(next_action_label)

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
