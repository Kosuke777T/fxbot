# app/gui/settings_tab.py
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QMessageBox,
    QSpacerItem,
    QSizePolicy,
    QListWidget,
)

from app.services import mt5_account_store, mt5_selftest
from app.services.profiles_store import load_profiles, save_profiles


class SettingsTab(QWidget):
    """MT5 口座設定タブ。

    - プロファイル（例: demo / real）ごとに login / password / server を保存
    - 「この口座に切り替え」ボタンで active_profile を変更し、
      カレントプロセスの環境変数 MT5_LOGIN/PASSWORD/SERVER も更新する
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._setup_ui()
        self._load_profiles()
        self._load_strategy_profiles()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # プロファイル選択
        grp_profile = QGroupBox("MT5 口座プロファイル", self)
        lay_p = QFormLayout(grp_profile)

        self.cmb_profile = QComboBox(grp_profile)
        # 新しい名前（例: demo2, demo_2026 など）も入力できるよう editable に
        self.cmb_profile.setEditable(True)
        self.cmb_profile.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cmb_profile.currentTextChanged.connect(self._on_profile_changed)

        lay_p.addRow("プロファイル名（例: demo / real）:", self.cmb_profile)

        # 認証情報
        grp_auth = QGroupBox("ログイン情報", self)
        lay_auth = QFormLayout(grp_auth)

        self.ed_login = QLineEdit(grp_auth)
        self.ed_login.setPlaceholderText("口座番号（数字）")

        self.ed_password = QLineEdit(grp_auth)
        self.ed_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_password.setPlaceholderText("パスワード")

        self.ed_server = QLineEdit(grp_auth)
        self.ed_server.setPlaceholderText("サーバ名（例: GaitameFinest-Demo）")

        lay_auth.addRow("ログインID:", self.ed_login)
        lay_auth.addRow("パスワード:", self.ed_password)
        lay_auth.addRow("サーバ:", self.ed_server)

        # ボタン
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("保存（このプロファイルを更新）", self)
        self.btn_switch = QPushButton("この口座に切り替え", self)

        self.btn_save.clicked.connect(self._on_save_clicked)
        self.btn_switch.clicked.connect(self._on_switch_clicked)

        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_switch)
        btn_row.addStretch()

        # 情報表示
        self.lbl_active = QLabel("", self)
        self.lbl_active.setWordWrap(True)

        # 接続テストボタン（自己診断）
        self.btn_selftest = QPushButton("MT5 接続テスト（自己診断）", self)
        self.btn_selftest.setToolTip(
            "現在のアクティブ口座プロファイルを使って MT5 への接続とログイン状態を自己診断します。"
        )
        self.btn_selftest.clicked.connect(self._on_selftest_clicked)

        # テスト発注ボタン（selftest_order_flow）
        self.btn_orderflow_test = QPushButton("テスト発注（selftest_order_flow）", self)
        self.btn_orderflow_test.setToolTip(
            "scripts.selftest_order_flow を実行して、0.01 lot の成行発注→即決済フローをテストします。\n"
            "必ずデモ口座で実行してください。"
        )
        self.btn_orderflow_test.clicked.connect(self._on_orderflow_selftest_clicked)

        row_selftest = QHBoxLayout()
        row_selftest.addStretch()
        row_selftest.addWidget(self.btn_selftest)
        row_selftest.addWidget(self.btn_orderflow_test)

        # Profiles 編集（config/profiles.json）
        grp_profiles = QGroupBox("戦略プロファイル（config/profiles.json）", self)
        lay_profiles = QVBoxLayout(grp_profiles)

        self.list_profiles = QListWidget(grp_profiles)
        self.list_profiles.setToolTip("walkforward_retrain で使用するプロファイル一覧")

        btn_profiles_row = QHBoxLayout()
        self.btn_profile_add = QPushButton("追加", grp_profiles)
        self.btn_profile_remove = QPushButton("削除", grp_profiles)
        self.btn_profile_save = QPushButton("保存", grp_profiles)

        self.btn_profile_add.clicked.connect(self._on_profile_add_clicked)
        self.btn_profile_remove.clicked.connect(self._on_profile_remove_clicked)
        self.btn_profile_save.clicked.connect(self._on_profile_save_clicked)

        btn_profiles_row.addWidget(self.btn_profile_add)
        btn_profiles_row.addWidget(self.btn_profile_remove)
        btn_profiles_row.addWidget(self.btn_profile_save)
        btn_profiles_row.addStretch()

        lay_profiles.addWidget(self.list_profiles)
        lay_profiles.addLayout(btn_profiles_row)

        # レイアウトに積む
        root.addWidget(grp_profile)
        root.addWidget(grp_auth)
        root.addLayout(btn_row)
        root.addWidget(self.lbl_active)
        root.addLayout(row_selftest)
        root.addWidget(grp_profiles)

        # 余白を下に追加
        root.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

    # ------------------------------------------------------------------
    # 内部ロジック
    # ------------------------------------------------------------------
    def _load_profiles(self) -> None:
        """設定ファイルからプロファイル一覧を読み込み、コンボボックスに反映。"""
        cfg = mt5_account_store.load_config()
        profiles = sorted(cfg["profiles"].keys())
        active = cfg.get("active_profile") or ""

        self.cmb_profile.blockSignals(True)
        self.cmb_profile.clear()

        # デモ・本口座の典型名をあらかじめ候補に入れておく
        base_candidates = ["demo", "real"]
        for name in base_candidates:
            if name not in profiles:
                profiles.append(name)

        for name in profiles:
            self.cmb_profile.addItem(name)

        # active_profile があれば選択
        if active and active in profiles:
            self.cmb_profile.setCurrentText(active)
        elif profiles:
            self.cmb_profile.setCurrentIndex(0)

        self.cmb_profile.blockSignals(False)

        # 選択中のプロファイル内容を反映
        self._apply_profile_to_fields(self.cmb_profile.currentText())
        self._refresh_active_label()

    def _apply_profile_to_fields(self, profile_name: str) -> None:
        """指定プロファイルの情報を入力欄に反映。"""
        if not profile_name:
            self.ed_login.clear()
            self.ed_password.clear()
            self.ed_server.clear()
            return

        acc = mt5_account_store.get_profile(profile_name)
        if acc is None:
            # 未保存プロファイルならフィールドは空に
            self.ed_login.clear()
            self.ed_password.clear()
            self.ed_server.clear()
            return

        self.ed_login.setText(str(acc.get("login", "")))
        self.ed_password.setText(acc.get("password", ""))
        self.ed_server.setText(acc.get("server", ""))

    def _on_profile_changed(self, name: str) -> None:
        self._apply_profile_to_fields(name)

    def _on_save_clicked(self) -> None:
        name = self.cmb_profile.currentText().strip()
        if not name:
            QMessageBox.warning(self, "保存エラー", "プロファイル名を入力してください。")
            return

        login_txt = self.ed_login.text().strip()
        password = self.ed_password.text()
        server = self.ed_server.text().strip()

        if not login_txt or not password or not server:
            QMessageBox.warning(self, "保存エラー", "ログインID・パスワード・サーバをすべて入力してください。")
            return

        try:
            login = int(login_txt)
        except ValueError:
            QMessageBox.warning(self, "保存エラー", "ログインID は数字のみを入力してください。")
            return

        mt5_account_store.upsert_profile(name, login=login, password=password, server=server)
        QMessageBox.information(self, "保存完了", f"プロファイル '{name}' を保存しました。")

        # 再読込して active/profile 表示を更新
        self._load_profiles()

    def _on_switch_clicked(self) -> None:
        name = self.cmb_profile.currentText().strip()
        if not name:
            QMessageBox.warning(self, "切り替えエラー", "プロファイル名を選択または入力してください。")
            return

        acc = mt5_account_store.get_profile(name)
        if acc is None:
            # 未保存なら「保存してから切り替える？」かを確認
            res = QMessageBox.question(
                self,
                "未保存プロファイル",
                "このプロファイルはまだ保存されていません。入力中の内容で保存してから切り替えますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if res == QMessageBox.StandardButton.Yes:
                self._on_save_clicked()
                acc = mt5_account_store.get_profile(name)
                if acc is None:
                    return
            else:
                return

        mt5_account_store.set_active_profile(name, apply_env=True)
        self._refresh_active_label()

        QMessageBox.information(
            self,
            "口座切り替え",
            f"アクティブ口座を '{name}' に切り替えました。\n\n"
            "このGUIプロセス内では MT5_LOGIN / MT5_PASSWORD / MT5_SERVER が\n"
            "選択した口座の情報に更新されています。",
        )

    def _on_selftest_clicked(self) -> None:
        """
        「MT5 接続テスト（自己診断）」ボタン押下時のハンドラ。
        """
        try:
            ok, log = mt5_selftest.run_mt5_selftest()
        except Exception as e:
            # サービス層でも例外を握っているが、GUI 側でも念のためガードしておく
            QMessageBox.critical(
                self,
                "MT5 接続テスト エラー",
                f"MT5 自己診断の実行中に予期しないエラーが発生しました。\n\n{e!r}",
            )
            return

        # 成功／失敗でアイコンとタイトルを変える
        if ok:
            icon = QMessageBox.Icon.Information
            title = "MT5 接続テスト 成功"
        else:
            icon = QMessageBox.Icon.Critical
            title = "MT5 接続テスト 失敗"

        # 詳細ログ（ログ全文）は detailedText に入れる
        first_line = log.splitlines()[0] if log else ""

        msg_box = QMessageBox(self)
        msg_box.setIcon(icon)
        msg_box.setWindowTitle(title)

        if ok:
            msg_box.setText(
                "MT5 への接続・ログインが正常に確認されました。\n"
                "取引準備は問題ありません。"
            )
        else:
            msg_box.setText(
                "MT5 への接続またはログインで問題が見つかりました。\n"
                "次の点を確認してください：\n"
                "  ・MT5 ターミナルは起動しているか\n"
                "  ・設定タブの口座ID / パスワード / サーバーは正しいか\n"
                "  ・デモ口座の有効期限が切れていないか\n"
                "  ・同時ログイン数の制限に引っかかっていないか\n"
                "\n詳細は「詳細」ボタンから確認できます。"
            )

        msg_box.setDetailedText(log)
        msg_box.exec()

    def _on_orderflow_selftest_clicked(self) -> None:
        """
        「テスト発注（selftest_order_flow）」ボタン押下時のハンドラ。
        """
        # まずは確認ダイアログ
        res = QMessageBox.question(
            self,
            "テスト発注の確認",
            (
                "scripts.selftest_order_flow を実行して、\n"
                "現在のアクティブMT5口座で 0.01 lot の成行注文→即決済テストを行います。\n\n"
                "※ 必ずデモ口座で実行してください。\n\n"
                "続行してよろしいですか？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if res != QMessageBox.StandardButton.Yes:
            return

        try:
            ok, log = mt5_selftest.run_mt5_orderflow_selftest()
        except Exception as e:
            QMessageBox.critical(
                self,
                "テスト発注エラー",
                "selftest_order_flow 実行中に予期しないエラーが発生しました。\n\n"
                f"{e!r}",
            )
            return

        # 成功／失敗でアイコンとタイトルを変える
        if ok:
            icon = QMessageBox.Icon.Information
            title = "テスト発注 成功"
            text = (
                "0.01 lot の成行発注→即決済テストが正常に完了しました。\n"
                "詳細ログは「詳細」ボタンから確認できます。"
            )
        else:
            icon = QMessageBox.Icon.Critical
            title = "テスト発注 失敗"
            text = (
                "テスト発注中にエラーが発生したか、自己診断が失敗しました。\n"
                "詳細ログを確認して、口座設定やMT5ターミナルの状態を見直してください。"
            )

        msg_box = QMessageBox(self)
        msg_box.setIcon(icon)
        msg_box.setWindowTitle(title)
        msg_box.setText(text)
        msg_box.setDetailedText(log)
        msg_box.exec()

    def _refresh_active_label(self) -> None:
        cfg = mt5_account_store.load_config()
        active = cfg.get("active_profile") or "(未設定)"
        acc = mt5_account_store.get_profile(active)

        if acc is None:
            txt = f"現在のアクティブ口座: {active}（設定情報が見つかりません）"
        else:
            txt = (
                f"現在のアクティブ口座: {active}\n"
                f"  login={acc.get('login')} / server={acc.get('server')}"
            )
        self.lbl_active.setText(txt)

    # ------------------------------------------------------------------
    # 戦略プロファイル（config/profiles.json）編集
    # ------------------------------------------------------------------
    def _load_strategy_profiles(self) -> None:
        """config/profiles.json からプロファイル一覧を読み込んで表示。"""
        try:
            profiles = load_profiles()
            self.list_profiles.clear()
            for profile in profiles:
                self.list_profiles.addItem(profile)
        except Exception as e:
            QMessageBox.warning(
                self,
                "読み込みエラー",
                f"プロファイル一覧の読み込みに失敗しました。\n\n{e}",
            )

    def _on_profile_add_clicked(self) -> None:
        """プロファイル追加ボタン押下時。"""
        from PyQt6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self,
            "プロファイル追加",
            "プロファイル名を入力してください:",
        )
        if ok and text.strip():
            profile_name = text.strip()
            # 重複チェック
            for i in range(self.list_profiles.count()):
                if self.list_profiles.item(i).text() == profile_name:
                    QMessageBox.warning(
                        self,
                        "追加エラー",
                        f"プロファイル '{profile_name}' は既に存在します。",
                    )
                    return
            self.list_profiles.addItem(profile_name)

    def _on_profile_remove_clicked(self) -> None:
        """プロファイル削除ボタン押下時。"""
        current_item = self.list_profiles.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "削除エラー", "削除するプロファイルを選択してください。")
            return

        profile_name = current_item.text()
        res = QMessageBox.question(
            self,
            "削除確認",
            f"プロファイル '{profile_name}' を削除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            row = self.list_profiles.row(current_item)
            self.list_profiles.takeItem(row)

    def _on_profile_save_clicked(self) -> None:
        """プロファイル保存ボタン押下時。"""
        profiles = []
        for i in range(self.list_profiles.count()):
            item = self.list_profiles.item(i)
            if item and item.text().strip():
                profiles.append(item.text().strip())

        if not profiles:
            QMessageBox.warning(self, "保存エラー", "プロファイルが1つもありません。")
            return

        try:
            save_profiles(profiles)
            QMessageBox.information(
                self,
                "保存完了",
                f"プロファイル一覧を保存しました。\n\n保存内容: {', '.join(profiles)}",
            )
            # 再読み込みして反映確認
            self._load_strategy_profiles()
        except Exception as e:
            QMessageBox.critical(
                self,
                "保存エラー",
                f"プロファイル一覧の保存に失敗しました。\n\n{e}",
            )
