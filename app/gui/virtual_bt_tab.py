# app/gui/virtual_bt_tab.py
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QProcess, QSettings, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QDateEdit,
    QComboBox,
    QPlainTextEdit,
    QMessageBox,
    QLineEdit,
    QSplitter,
)
from PyQt6.QtCore import QDate
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as Canvas
from matplotlib.figure import Figure

from app.services.virtual_bt_service import VirtualBacktestService


class VirtualBTTab(QWidget):
    """
    Virtual BT（仮想実行バックテスト）タブ。
    
    - CSV選択
    - 開始/停止ボタン
    - 実行中は入力disabled
    - 実行状態/出力先をラベル表示
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._service = VirtualBacktestService(self)
        self._csv_path: Optional[str] = None
        
        # QSettings を初期化（CSVパス永続化用）
        self._settings = QSettings("fxbot", "michibiki")
        
        # 資産曲線データ（差分更新用）
        self._equity_data: list[dict] = []
        
        # 500msタイマー（実行中のみ動作）
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(500)
        self._update_timer.timeout.connect(self._on_update_timer)
        
        # UI構築
        self._setup_ui()
        
        # 保存済みCSVパスを復元
        self._restore_last_csv_path()
        
        # サービスシグナル接続
        self._service.finished.connect(self._on_service_finished)
        self._service.error_occurred.connect(self._on_service_error)
        self._service.log_output.connect(self._on_log_output)
        
    def _setup_ui(self):
        """UIを構築する。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        
        # CSV選択
        csv_row = QHBoxLayout()
        csv_row.addWidget(QLabel("CSVファイル:"))
        self.csv_path_edit = QLineEdit()
        self.csv_path_edit.setReadOnly(True)
        self.csv_path_edit.setPlaceholderText("CSVファイルを選択してください")
        csv_row.addWidget(self.csv_path_edit, 1)
        self.btn_select_csv = QPushButton("選択...")
        self.btn_select_csv.clicked.connect(self._select_csv)
        csv_row.addWidget(self.btn_select_csv)
        root.addLayout(csv_row)
        
        # パラメータ行
        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("シンボル:"))
        self.symbol_combo = QComboBox()
        self.symbol_combo.addItems(["USDJPY-", "EURJPY-", "GBPJPY-", "AUDJPY-"])
        self.symbol_combo.setCurrentText("USDJPY-")
        params_row.addWidget(self.symbol_combo)
        
        params_row.addWidget(QLabel("TF:"))
        self.tf_combo = QComboBox()
        self.tf_combo.addItems(["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
        self.tf_combo.setCurrentText("M5")
        params_row.addWidget(self.tf_combo)
        
        params_row.addWidget(QLabel("開始:"))
        self.start_edit = QDateEdit()
        self.start_edit.setCalendarPopup(True)
        end_date = QDate.currentDate().addDays(-1)
        start_date = end_date.addMonths(-1)
        self.start_edit.setDate(start_date)
        params_row.addWidget(self.start_edit)
        
        params_row.addWidget(QLabel("終了:"))
        self.end_edit = QDateEdit()
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDate(end_date)
        params_row.addWidget(self.end_edit)
        
        params_row.addWidget(QLabel("初期資本:"))
        self.capital_edit = QLineEdit()
        self.capital_edit.setText("100000.0")
        self.capital_edit.setMaximumWidth(100)
        params_row.addWidget(self.capital_edit)
        
        params_row.addStretch(1)
        root.addLayout(params_row)
        
        # プロファイルと初期ポジション
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("プロファイル:"))
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(["michibiki_std"])
        self.profile_combo.setCurrentText("michibiki_std")
        profile_row.addWidget(self.profile_combo)
        
        profile_row.addWidget(QLabel("初期ポジション:"))
        self.init_pos_combo = QComboBox()
        self.init_pos_combo.addItems(["flat", "carry"])
        self.init_pos_combo.setCurrentText("flat")
        profile_row.addWidget(self.init_pos_combo)
        
        profile_row.addStretch(1)
        root.addLayout(profile_row)
        
        # 開始/停止ボタン
        control_row = QHBoxLayout()
        self.btn_start = QPushButton("開始")
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_stop.setEnabled(False)
        control_row.addWidget(self.btn_start)
        control_row.addWidget(self.btn_stop)
        control_row.addStretch(1)
        root.addLayout(control_row)
        
        # 状態表示
        self.status_label = QLabel("準備完了")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        
        # 出力先表示
        self.out_dir_label = QLabel("")
        self.out_dir_label.setWordWrap(True)
        self.out_dir_label.hide()
        root.addWidget(self.out_dir_label)
        
        # 分割ウィジェット（ログと資産曲線）
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        
        # ログ表示
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("実行ログがここに表示されます")
        # ログのフォントサイズを小さく（行密度を上げる）
        self.log_text.setStyleSheet("QPlainTextEdit { font-size: 10px; }")
        splitter.addWidget(self.log_text)
        
        # 資産曲線描画エリア
        equity_widget = QWidget(self)
        equity_layout = QVBoxLayout(equity_widget)
        equity_layout.setContentsMargins(0, 0, 0, 0)
        
        equity_label = QLabel("資産曲線", equity_widget)
        equity_layout.addWidget(equity_label)
        
        self.equity_fig = Figure(figsize=(9, 3))
        self.equity_canvas = Canvas(self.equity_fig)
        self.equity_ax = self.equity_fig.add_subplot(111)
        self.equity_ax.set_xlabel("時間")
        self.equity_ax.set_ylabel("資産 (JPY)")
        self.equity_ax.grid(True)
        self.equity_line = None  # 後で初期化
        
        equity_layout.addWidget(self.equity_canvas)
        splitter.addWidget(equity_widget)
        
        # 分割比率（ログ:資産曲線 = 1:2）
        splitter.setSizes([120, 260])
        
        # ログ側は縮みやすく、グラフ側は広さを優先
        splitter.setStretchFactor(0, 0)  # ログ
        splitter.setStretchFactor(1, 1)  # 資産曲線
        
        root.addWidget(splitter, 1)
        
    def _select_csv(self):
        """CSVファイルを選択する。"""
        # 初期フォルダを D:\fxbot\data に固定
        start_dir = r"D:\fxbot\data"
        # フォルダが存在しない場合は現在のディレクトリにフォールバック
        if not Path(start_dir).exists():
            start_dir = str(Path(".").resolve())
        
        fn, _ = QFileDialog.getOpenFileName(
            self,
            "バックテスト入力CSV（OHLCV）を選択",
            start_dir,
            "CSV (*.csv)"
        )
        if fn:
            self._csv_path = fn
            self.csv_path_edit.setText(fn)
            # QSettings に保存
            self._settings.setValue("virtual_bt/last_csv_path", fn)
            self._append_log(f"[VirtualBT] CSV selected: {fn}")
    
    def _restore_last_csv_path(self):
        """保存済みCSVパスを復元する。"""
        try:
            saved_path = self._settings.value("virtual_bt/last_csv_path", None)
            if saved_path and isinstance(saved_path, str):
                # ファイルが存在するか確認
                csv_path = Path(saved_path)
                if csv_path.exists() and csv_path.is_file():
                    self._csv_path = saved_path
                    self.csv_path_edit.setText(saved_path)
                    self._append_log(f"[VirtualBT] Restored CSV path: {saved_path}")
                else:
                    # ファイルが存在しない場合は設定をクリア
                    self._settings.remove("virtual_bt/last_csv_path")
                    self._append_log(f"[VirtualBT] Saved CSV path not found, cleared: {saved_path}")
        except Exception as e:
            # 復元失敗時は無視（アプリは継続）
            self._append_log(f"[VirtualBT] Failed to restore CSV path: {e}")
    
    def _on_start_clicked(self):
        """開始ボタンがクリックされたときの処理。"""
        if not self._csv_path:
            QMessageBox.warning(self, "Virtual BT", "CSVファイルを選択してください。")
            return
        
        if not Path(self._csv_path).exists():
            QMessageBox.warning(self, "Virtual BT", f"CSVファイルが見つかりません: {self._csv_path}")
            return
        
        try:
            # パラメータ取得
            symbol = self.symbol_combo.currentText()
            timeframe = self.tf_combo.currentText()
            start_date = self.start_edit.date().toString("yyyy-MM-dd")
            end_date = self.end_edit.date().toString("yyyy-MM-dd")
            capital = float(self.capital_edit.text() or "100000.0")
            profile = self.profile_combo.currentText()
            init_position = self.init_pos_combo.currentText()
            
            # サービスで実行開始
            run_id, out_dir = self._service.start_run(
                csv_path=self._csv_path,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                capital=capital,
                profile=profile,
                init_position=init_position,
            )
            
            # UI更新
            self._set_running(True)
            self.status_label.setText(f"実行中... run_id: {run_id}")
            self.out_dir_label.setText(f"出力先: {out_dir}")
            self.out_dir_label.show()
            self._append_log(f"[VirtualBT] Started run_id={run_id}")
            self._append_log(f"[VirtualBT] Out dir: {out_dir}")
            
            # 資産曲線をリセット
            self._equity_data = []
            self._update_equity_plot()
            
            # 500msタイマーを開始
            self._update_timer.start()
            
        except Exception as e:
            QMessageBox.critical(self, "Virtual BT エラー", f"実行開始に失敗しました:\n{e}")
            self._append_log(f"[VirtualBT] ERROR: {e}")
            import traceback
            self._append_log(traceback.format_exc())
    
    def _on_stop_clicked(self):
        """停止ボタンがクリックされたときの処理。"""
        try:
            self._service.stop_run()
            self._set_running(False)
            self.status_label.setText("停止しました")
            self._append_log("[VirtualBT] Stop requested")
        except Exception as e:
            QMessageBox.warning(self, "Virtual BT", f"停止処理でエラーが発生しました:\n{e}")
            self._append_log(f"[VirtualBT] ERROR on stop: {e}")
    
    def _on_service_finished(self, exit_code: int, exit_status: int):
        """サービスが終了したときの処理。"""
        self._set_running(False)
        
        if exit_code == 0:
            self.status_label.setText(f"完了 (exit_code={exit_code})")
            self._append_log(f"[VirtualBT] Finished successfully (exit_code={exit_code})")
            
            # 成果物確認
            out_dir = self._service.get_out_dir()
            if out_dir:
                self._check_outputs(out_dir)
        else:
            self.status_label.setText(f"失敗 (exit_code={exit_code})")
            self._append_log(f"[VirtualBT] Finished with error (exit_code={exit_code})")
            QMessageBox.warning(
                self,
                "Virtual BT",
                f"バックテストが失敗しました (exit_code={exit_code})。\nログを確認してください。"
            )
    
    def _on_service_error(self, error_message: str):
        """サービスでエラーが発生したときの処理。"""
        self._append_log(f"[VirtualBT] ERROR: {error_message}")
        QMessageBox.critical(self, "Virtual BT エラー", error_message)
    
    def _set_running(self, running: bool):
        """実行状態に応じてUIを更新する。"""
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        
        # 入力フィールドを無効化
        self.csv_path_edit.setEnabled(not running)
        self.btn_select_csv.setEnabled(not running)
        self.symbol_combo.setEnabled(not running)
        self.tf_combo.setEnabled(not running)
        self.start_edit.setEnabled(not running)
        self.end_edit.setEnabled(not running)
        self.capital_edit.setEnabled(not running)
        self.profile_combo.setEnabled(not running)
        self.init_pos_combo.setEnabled(not running)
        
        # タイマーを停止/開始
        if not running:
            self._update_timer.stop()
    
    def _check_outputs(self, out_dir: Path):
        """成果物を確認してログに出力する。"""
        self._append_log(f"[VirtualBT] Checking outputs in {out_dir}")
        
        required_files = ["config.json", "bt_app.log"]
        optional_files = ["metrics.json", "equity_curve.csv", "trades.csv", "monthly_returns.csv"]
        
        for fname in required_files:
            path = out_dir / fname
            if path.exists():
                self._append_log(f"[VirtualBT] ✓ {fname} exists")
            else:
                self._append_log(f"[VirtualBT] ✗ {fname} NOT FOUND")
        
        for fname in optional_files:
            path = out_dir / fname
            if path.exists():
                self._append_log(f"[VirtualBT] ✓ {fname} exists")
            else:
                self._append_log(f"[VirtualBT] - {fname} not generated (optional)")
    
    def _append_log(self, text: str):
        """ログテキストに追加する。"""
        self.log_text.appendPlainText(text)
    
    def _on_log_output(self, text: str):
        """サービスからのリアルタイムログ出力を受け取る。"""
        self._append_log(text)
    
    def _on_update_timer(self):
        """500msタイマー：equity_curve.csv の差分を読み取って描画を更新。"""
        if not self._service.is_running():
            return
        
        try:
            # 差分を読み取る（全読み込み禁止）
            new_rows = self._service.read_equity_curve_diff()
            if new_rows:
                # データに追加
                self._equity_data.extend(new_rows)
                # 描画を更新
                self._update_equity_plot()
        except Exception as e:
            # 更新失敗でもアプリは継続
            pass
    
    def _update_equity_plot(self):
        """資産曲線の描画を更新する。"""
        if not self._equity_data:
            return
        
        try:
            import pandas as pd
            
            # データをDataFrameに変換
            df = pd.DataFrame(self._equity_data)
            if df.empty:
                return
            
            # 時間をdatetimeに変換
            df["time"] = pd.to_datetime(df["time"])
            df = df.sort_values("time")
            
            # 既存の線がある場合は set_data で更新、ない場合は plot で新規作成
            if self.equity_line is None:
                self.equity_line, = self.equity_ax.plot(df["time"], df["equity"], "b-", linewidth=1.5)
            else:
                self.equity_line.set_data(df["time"], df["equity"])
            
            # 軸を更新
            self.equity_ax.relim()
            self.equity_ax.autoscale_view()
            
            # キャンバスを更新
            self.equity_canvas.draw_idle()
        except Exception as e:
            # 描画失敗でもアプリは継続
            pass
