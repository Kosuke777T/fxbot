# app/gui/ops_tab.py
from __future__ import annotations

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

from app.services.ops_service import get_ops_service
from app.services.profiles_store import load_profiles


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

        # 結果表示セクション（Splitter で分割）
        splitter = QSplitter(Qt.Orientation.Vertical, self)

        # JSON結果ツリー
        grp_result = QGroupBox("実行結果（JSON）", self)
        lay_result = QVBoxLayout(grp_result)
        self.tree_result = QTreeWidget(grp_result)
        self.tree_result.setColumnCount(2)
        self.tree_result.setHeaderLabels(["キー", "値"])
        self.tree_result.setColumnWidth(0, 200)
        lay_result.addWidget(self.tree_result)
        splitter.addWidget(grp_result)

        # stdout/stderr 表示
        grp_output = QGroupBox("出力（stdout / stderr）", self)
        lay_output = QVBoxLayout(grp_output)
        self.text_output = QTextEdit(grp_output)
        self.text_output.setReadOnly(True)
        self.text_output.setFontFamily("Consolas")
        lay_output.addWidget(self.text_output)
        splitter.addWidget(grp_output)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        # レイアウトに積む
        root.addWidget(grp_input)
        root.addWidget(splitter)

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
