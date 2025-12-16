# app/gui/ops_tab.py
from __future__ import annotations

import copy
from typing import Optional, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QMessageBox,
    QSplitter,
)

from loguru import logger

from app.services.ops_service import get_ops_service
from app.services.profiles_store import load_profiles
from app.services.ops_history_service import get_ops_history_service, replay_from_record


class OpsTab(QWidget):
    """Ops実行タブ（tools/ops_start.ps1 の実行と結果表示）"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._setup_ui()
        self._load_defaults()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # 入力セクション
        grp_input = QGroupBox("実行パラメータ", self)
        lay_input = QFormLayout(grp_input)

        self.ed_symbol = QLineEdit(grp_input)
        self.ed_symbol.setPlaceholderText("USDJPY-")
        lay_input.addRow("Symbol:", self.ed_symbol)

        self.chk_dry = QCheckBox("Dry run", grp_input)
        lay_input.addRow("", self.chk_dry)

        self.chk_close_now = QCheckBox("Close now", grp_input)
        self.chk_close_now.setChecked(True)
        lay_input.addRow("", self.chk_close_now)

        # プロファイル選択（単一 or 複数）
        self.ed_profile = QLineEdit(grp_input)
        self.ed_profile.setPlaceholderText("単一プロファイル名（例: michibiki_std）")
        lay_input.addRow("Profile (単一):", self.ed_profile)

        self.ed_profiles = QLineEdit(grp_input)
        self.ed_profiles.setPlaceholderText("複数プロファイル（カンマ区切り、例: p1,p2）")
        lay_input.addRow("Profiles (複数):", self.ed_profiles)

        # 実行ボタン
        self.btn_run = QPushButton("実行", grp_input)
        self.btn_run.clicked.connect(self._on_run_clicked)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_run)
        btn_row.addStretch()
        lay_input.addRow("", btn_row)

        # 結果表示セクション（水平Splitterで履歴と結果を分割）
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # 左側：履歴セクション
        grp_history = QGroupBox("履歴", self)
        lay_history = QVBoxLayout(grp_history)
        self.list_history = QTreeWidget(grp_history)
        self.list_history.setColumnCount(4)
        self.list_history.setHeaderLabels(["時刻", "Symbol", "Status", "Step"])
        self.list_history.setColumnWidth(0, 180)
        self.list_history.setColumnWidth(1, 100)
        self.list_history.setColumnWidth(2, 80)
        self.list_history.setColumnWidth(3, 200)
        self.list_history.itemSelectionChanged.connect(self._on_history_selected)
        lay_history.addWidget(self.list_history)

        # 再実行ボタン
        self.btn_replay = QPushButton("この条件で再実行", grp_history)
        self.btn_replay.clicked.connect(self._replay_selected)
        self.btn_replay.setEnabled(False)  # 選択されるまで無効
        lay_history.addWidget(self.btn_replay)

        # PROMOTE/RETRYボタン（動的表示）
        btn_action_row = QHBoxLayout()
        self.btn_promote = QPushButton("PROMOTE", grp_history)
        self.btn_promote.clicked.connect(self._on_promote_clicked)
        self.btn_promote.setVisible(False)
        btn_action_row.addWidget(self.btn_promote)

        self.btn_retry = QPushButton("RETRY", grp_history)
        self.btn_retry.clicked.connect(self._on_retry_clicked)
        self.btn_retry.setVisible(False)
        btn_action_row.addWidget(self.btn_retry)
        btn_action_row.addStretch()
        lay_history.addLayout(btn_action_row)

        main_splitter.addWidget(grp_history)

        # 選択中レコードを保持（一時的に）
        self._selected_record: Optional[dict] = None

        # 最新Opsレコードを保持（表示用、PROMOTE/RETRYボタン用）
        self._current_ops_record: Optional[dict] = None

        # 右側：既存の結果表示（垂直Splitter）
        result_splitter = QSplitter(Qt.Orientation.Vertical, self)

        # JSON結果ツリー
        grp_result = QGroupBox("実行結果（JSON）", self)
        lay_result = QVBoxLayout(grp_result)
        self.tree_result = QTreeWidget(grp_result)
        self.tree_result.setColumnCount(2)
        self.tree_result.setHeaderLabels(["キー", "値"])
        self.tree_result.setColumnWidth(0, 200)
        lay_result.addWidget(self.tree_result)
        result_splitter.addWidget(grp_result)

        # stdout/stderr 表示
        grp_output = QGroupBox("出力（stdout / stderr）", self)
        lay_output = QVBoxLayout(grp_output)
        self.text_output = QTextEdit(grp_output)
        self.text_output.setReadOnly(True)
        self.text_output.setFontFamily("Consolas")
        lay_output.addWidget(self.text_output)
        result_splitter.addWidget(grp_output)

        result_splitter.setStretchFactor(0, 2)
        result_splitter.setStretchFactor(1, 1)
        main_splitter.addWidget(result_splitter)

        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 2)

        # レイアウトに積む
        root.addWidget(grp_input)
        root.addWidget(main_splitter)

        # 履歴を読み込む
        self._load_history()

    def _load_defaults(self) -> None:
        """デフォルト値を読み込む。"""
        self.ed_symbol.setText("USDJPY-")
        try:
            profiles = load_profiles()
            if profiles:
                if len(profiles) == 1:
                    self.ed_profile.setText(profiles[0])
                else:
                    self.ed_profiles.setText(",".join(profiles))
        except Exception:
            pass

    def _on_run_clicked(self) -> None:
        """実行ボタン押下時。"""
        symbol = self.ed_symbol.text().strip() or "USDJPY-"
        dry = self.chk_dry.isChecked()
        close_now = self.chk_close_now.isChecked()

        profile = self.ed_profile.text().strip() or None
        profiles_text = self.ed_profiles.text().strip()
        profiles = [p.strip() for p in profiles_text.split(",") if p.strip()] if profiles_text else None

        # profile と profiles の両方が指定されている場合は警告
        if profile and profiles:
            QMessageBox.warning(
                self,
                "パラメータエラー",
                "Profile と Profiles は同時に指定できません。",
            )
            return

        # 実行
        self.btn_run.setEnabled(False)
        self.btn_run.setText("実行中...")
        self.tree_result.clear()
        self.text_output.clear()

        try:
            ops_service = get_ops_service()
            result = ops_service.run_ops_start(
                symbol=symbol,
                dry=dry,
                close_now=close_now,
                profile=profile,
                profiles=profiles,
            )

            # 結果を表示
            self._display_result(result)

            # 履歴を更新
            self._load_history()
            # PROMOTE/RETRYボタンの表示も更新
            self._refresh_ops_actions()

        except Exception as e:
            QMessageBox.critical(
                self,
                "実行エラー",
                f"実行中にエラーが発生しました。\n\n{e}",
            )
        finally:
            self.btn_run.setEnabled(True)
            self.btn_run.setText("実行")

    def _display_result(self, result: dict) -> None:
        """結果を表示する。"""
        # stdout/stderr を表示（meta から取得、なければトップレベルから）
        output_lines = []
        meta = result.get("meta", {})
        stdout = meta.get("stdout") or result.get("stdout", "")
        stderr = meta.get("stderr") or result.get("stderr", "")
        returncode = meta.get("returncode")
        if returncode is None:
            returncode = result.get("returncode")

        if stdout:
            output_lines.append("=== stdout ===")
            output_lines.append(stdout)
            output_lines.append("")
        if stderr:
            output_lines.append("=== stderr ===")
            output_lines.append(stderr)
            output_lines.append("")
        if returncode is not None:
            output_lines.append(f"=== returncode: {returncode} ===")

        self.text_output.setPlainText("\n".join(output_lines))

        # JSON結果をツリー表示
        self.tree_result.clear()

        # 失敗判定: ok=False かつ error を持つ場合のみ
        is_failure = (
            isinstance(result, dict)
            and result.get("ok") is False
            and result.get("error") is not None
        )

        if is_failure:
            # 失敗時: error中心に表示（必要なら result も）
            if result.get("error"):
                error_item = QTreeWidgetItem(self.tree_result.invisibleRootItem())
                error_item.setText(0, "error")
                self._populate_tree(error_item, result["error"])

            # パースできた場合は result も表示
            if result.get("result"):
                result_item = QTreeWidgetItem(self.tree_result.invisibleRootItem())
                result_item.setText(0, "result")
                self._populate_tree(result_item, result["result"])
        else:
            # 成功時（ok=True を含む通常JSON）: result 全体をツリー表示（meta も含む）
            root = self.tree_result.invisibleRootItem()
            self._populate_tree(root, result)

        self.tree_result.expandAll()

    def _populate_tree(self, parent: QTreeWidgetItem, data: Any, key: str = "") -> None:
        """
        dict/list を再帰的にツリーに追加する。

        Args:
            parent: 親アイテム
            data: 追加するデータ（dict/list/その他）
            key: このデータのキー名（表示用）
        """
        if isinstance(data, dict):
            for k, v in data.items():
                item = QTreeWidgetItem(parent)
                item.setText(0, str(k))
                if isinstance(v, (dict, list)):
                    item.setText(1, type(v).__name__)
                    self._populate_tree(item, v, k)
                else:
                    item.setText(1, str(v))
        elif isinstance(data, list):
            for i, v in enumerate(data):
                item = QTreeWidgetItem(parent)
                item.setText(0, f"[{i}]")
                if isinstance(v, (dict, list)):
                    item.setText(1, type(v).__name__)
                    self._populate_tree(item, v, f"[{i}]")
                else:
                    item.setText(1, str(v))
        else:
            # スカラー値
            item = QTreeWidgetItem(parent)
            item.setText(0, key if key else "value")
            item.setText(1, str(data))

    def _load_history(self) -> None:
        """履歴を読み込んで表示する。"""
        try:
            history_service = get_ops_history_service()
            records = history_service.load_ops_history(limit=200)

            self.list_history.clear()
            for rec in records:
                started_at = rec.get("started_at", "")
                symbol = rec.get("symbol", "")
                ok = rec.get("ok", False)
                step = rec.get("step", "")

                # 時刻を短縮表示
                if started_at:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        time_str = started_at[:19] if len(started_at) >= 19 else started_at
                else:
                    time_str = ""

                # ステータス表示
                status = "OK" if ok else "NG"

                item = QTreeWidgetItem(self.list_history)
                item.setText(0, time_str)
                item.setText(1, symbol)
                item.setText(2, status)
                item.setText(3, step)
                # レコード全体を item に保存（選択時に使用）
                item.setData(0, Qt.ItemDataRole.UserRole, rec)

            self.list_history.sortItems(0, Qt.SortOrder.DescendingOrder)

            # 最新Opsレコードを取得してnext_actionを判定（PROMOTE/RETRYボタン表示用）
            self._refresh_ops_actions()
        except Exception as e:
            logger.error(f"Failed to load history: {e}")

    def _on_history_selected(self) -> None:
        """履歴が選択されたときに結果を再表示する。"""
        selected_items = self.list_history.selectedItems()
        if not selected_items:
            self._selected_record = None
            self.btn_replay.setEnabled(False)
            return

        item = selected_items[0]
        rec = item.data(0, Qt.ItemDataRole.UserRole)
        if rec:
            # 選択中レコードを保持
            self._selected_record = rec
            self.btn_replay.setEnabled(True)
            # 既存の表示関数に渡して再表示
            self._display_result(rec)
        else:
            self._selected_record = None
            self.btn_replay.setEnabled(False)

    def _replay_selected(self) -> None:
        """選択中の履歴レコードを再実行する。"""
        if not self._selected_record:
            QMessageBox.warning(
                self,
                "エラー",
                "履歴が選択されていません。",
            )
            return

        # 最終確認
        reply = QMessageBox.question(
            self,
            "再実行確認",
            "選択中の条件で再実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from app.services.ops_history_service import replay_from_record

            # 再実行（run=True、overridesなし）
            # corr_id/source_record_idはservices側で自動設定される
            result = replay_from_record(self._selected_record, run=True)

            # 結果のsummaryとnext_actionを取得
            summary = result.get("summary", {})
            title = summary.get("title", "再実行結果")
            hint = summary.get("hint", "")
            stderr_tail = summary.get("stderr_tail", [])
            stderr_full = result.get("stderr_full", [])
            stderr_lines = summary.get("stderr_lines", 0)
            next_action = result.get("next_action", {})

            # next_actionの情報をテキストで追加
            next_action_text = ""
            if next_action:
                kind = next_action.get("kind", "")
                reason = next_action.get("reason", "")
                if kind and reason:
                    next_action_text = f"\n\n次のアクション: {kind}\n{reason}"

            # 結果を表示（summary.titleを大きく表示）
            if result["ok"]:
                msg = f"{title}\n\n{hint}{next_action_text}"
                if result.get("stdout"):
                    stdout_preview = result["stdout"][:200] if len(result["stdout"]) > 200 else result["stdout"]
                    msg += f"\n\n出力（要約）:\n{stdout_preview}"
                QMessageBox.information(self, "再実行完了", msg)
            else:
                msg = f"{title}\n\n{hint}{next_action_text}"

                # stderr_tailを表示（折りたたみ時の要約）
                if stderr_tail:
                    msg += f"\n\nエラー出力（末尾）:\n" + "\n".join(stderr_tail[-3:])

                # 詳細表示用のダイアログ（折りたたみ）
                if stderr_lines > 0:
                    detail_msg = f"エラー出力（全{stderr_lines}行）:\n\n" + "\n".join(stderr_full)
                    if len(detail_msg) > 2000:
                        detail_msg = detail_msg[:2000] + "\n\n... (省略)"

                    # 詳細表示ボタン付きダイアログ
                    detail_dialog = QMessageBox(self)
                    detail_dialog.setWindowTitle("再実行失敗 - 詳細")
                    detail_dialog.setText(msg)
                    detail_dialog.setDetailedText(detail_msg)
                    detail_dialog.setIcon(QMessageBox.Icon.Critical)

                    # next_action.kind == "PROMOTE_DRY_TO_RUN" のときだけボタンを追加
                    if next_action.get("kind") == "PROMOTE_DRY_TO_RUN":
                        btn_promote = detail_dialog.addButton("実際の実行を試す", QMessageBox.ButtonRole.ActionRole)
                        detail_dialog.exec()
                        if detail_dialog.clickedButton() == btn_promote:
                            # paramsを使って再実行
                            self._replay_with_params(self._selected_record, next_action.get("params", {}))
                            return
                    else:
                        detail_dialog.exec()
                else:
                    # stderr_lines == 0 の場合も next_action ボタンを追加できるように
                    msg_box = QMessageBox(self)
                    msg_box.setWindowTitle("再実行失敗")
                    msg_box.setText(msg)
                    msg_box.setIcon(QMessageBox.Icon.Critical)

                    # next_action.kind == "PROMOTE_DRY_TO_RUN" のときだけボタンを追加
                    if next_action.get("kind") == "PROMOTE_DRY_TO_RUN":
                        btn_promote = msg_box.addButton("実際の実行を試す", QMessageBox.ButtonRole.ActionRole)
                        msg_box.exec()
                        if msg_box.clickedButton() == btn_promote:
                            # paramsを使って再実行
                            self._replay_with_params(self._selected_record, next_action.get("params", {}))
                            return
                    else:
                        msg_box.exec()

            # UIイベントはservices側で記録される（GUI側では記録しない）

            # 履歴を更新
            self._load_history()

        except Exception as e:
            logger.exception("Failed to replay: %s", e)
            QMessageBox.critical(
                self,
                "再実行エラー",
                f"再実行中にエラーが発生しました。\n\n{e}",
            )

    def _replay_with_params(self, record: dict, params: dict) -> None:
        """
        paramsを使って再実行する。

        Args:
            record: 元のレコード
            params: next_action.params（例: {"dry": False}）
        """
        if not record or not params:
            return

        # 確認ダイアログ
        reply = QMessageBox.question(
            self,
            "再実行確認",
            f"パラメータを変更して再実行しますか？\n\n変更内容: {params}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from app.services.ops_history_service import replay_from_record

            # overridesを渡して再実行（GUI側でrecordを編集しない）
            result = replay_from_record(record, run=True, overrides=params)

            # 結果のsummaryとnext_actionを取得
            summary = result.get("summary", {})
            title = summary.get("title", "再実行結果")
            hint = summary.get("hint", "")

            # 結果を表示
            if result["ok"]:
                msg = f"{title}\n\n{hint}"
                QMessageBox.information(self, "再実行完了", msg)
            else:
                msg = f"{title}\n\n{hint}"
                QMessageBox.critical(self, "再実行失敗", msg)

            # UIイベントはservices側で記録される（GUI側では記録しない）

            # 履歴を更新
            self._load_history()

        except Exception as e:
            logger.exception("Failed to replay with params: %s", e)
            QMessageBox.critical(
                self,
                "再実行エラー",
                f"再実行中にエラーが発生しました。\n\n{e}",
            )

    def _refresh_ops_actions(self) -> None:
        """
        最新Opsレコードを取得してnext_actionを判定し、PROMOTE/RETRYボタンの表示を更新する。
        """
        try:
            history_service = get_ops_history_service()
            summary = history_service.summarize_ops_history()

            # lastを取得
            last = summary.get("last")
            if not last:
                # lastが無い場合はitemsの先頭を取得
                items = summary.get("items", [])
                if items:
                    last = items[0]
                else:
                    last = None

            self._current_ops_record = last

            # lastが無い場合はボタンを非表示
            if not last:
                self.btn_promote.setVisible(False)
                self.btn_retry.setVisible(False)
                return

            # replay_from_recordをrun=Falseで呼んでnext_actionを取得
            # ただし、lastにはrecord_idが含まれているが、完全なrecordが必要な場合があるので
            # load_ops_historyから最新の完全なrecordを取得する
            records = history_service.load_ops_history(limit=1)
            if records:
                full_record = records[0]
            else:
                full_record = last

            # run=Falseでnext_actionを取得
            result = replay_from_record(copy.deepcopy(full_record), run=False)
            next_action = result.get("next_action", {})
            kind = (next_action.get("kind") or "").upper()

            # ボタンの表示/非表示を制御
            self.btn_promote.setVisible(kind == "PROMOTE" or kind == "PROMOTE_DRY_TO_RUN")
            self.btn_retry.setVisible(kind == "RETRY")

            # reasonがあればツールチップに表示（任意）
            reason = next_action.get("reason", "")
            if reason:
                self.btn_promote.setToolTip(reason)
                self.btn_retry.setToolTip(reason)

        except Exception as e:
            logger.error(f"Failed to refresh ops actions: {e}")
            # エラー時はボタンを非表示
            self.btn_promote.setVisible(False)
            self.btn_retry.setVisible(False)

    def _on_promote_clicked(self) -> None:
        """PROMOTEボタン押下時のハンドラ。"""
        if not self._current_ops_record:
            QMessageBox.warning(
                self,
                "エラー",
                "Opsレコードがありません。",
            )
            return

        try:
            # replay_from_recordをoverrides={"action":"PROMOTE"}で呼ぶ
            result = replay_from_record(
                copy.deepcopy(self._current_ops_record),
                run=True,
                overrides={"action": "PROMOTE"},
            )

            # 結果を表示
            summary = result.get("summary", {})
            title = summary.get("title", "PROMOTE実行結果")
            hint = summary.get("hint", "")

            if result["ok"]:
                msg = f"{title}\n\n{hint}"
                QMessageBox.information(self, "PROMOTE完了", msg)
            else:
                msg = f"{title}\n\n{hint}"
                QMessageBox.critical(self, "PROMOTE失敗", msg)

            # 履歴を更新
            self._load_history()

        except Exception as e:
            logger.exception("Failed to promote: %s", e)
            QMessageBox.critical(
                self,
                "PROMOTEエラー",
                f"PROMOTE実行中にエラーが発生しました。\n\n{e}",
            )

    def _on_retry_clicked(self) -> None:
        """RETRYボタン押下時のハンドラ。"""
        if not self._current_ops_record:
            QMessageBox.warning(
                self,
                "エラー",
                "Opsレコードがありません。",
            )
            return

        try:
            # replay_from_recordをoverrides={"action":"RETRY"}で呼ぶ
            result = replay_from_record(
                copy.deepcopy(self._current_ops_record),
                run=True,
                overrides={"action": "RETRY"},
            )

            # 結果を表示
            summary = result.get("summary", {})
            title = summary.get("title", "RETRY実行結果")
            hint = summary.get("hint", "")

            if result["ok"]:
                msg = f"{title}\n\n{hint}"
                QMessageBox.information(self, "RETRY完了", msg)
            else:
                msg = f"{title}\n\n{hint}"
                QMessageBox.critical(self, "RETRY失敗", msg)

            # 履歴を更新
            self._load_history()

        except Exception as e:
            logger.exception("Failed to retry: %s", e)
            QMessageBox.critical(
                self,
                "RETRYエラー",
                f"RETRY実行中にエラーが発生しました。\n\n{e}",
            )
