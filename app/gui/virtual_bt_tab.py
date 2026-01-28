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
    QDialog,
    QGroupBox,
    QFormLayout,
    QGridLayout,
    QTabWidget,
)
from PyQt6.QtCore import QDate
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as Canvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates

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
        
        # 進捗情報（ログから抽出）
        self._progress_percent: Optional[int] = None
        
        # 500msタイマー（実行中のみ動作）
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(500)
        self._update_timer.timeout.connect(self._on_update_timer)
        
        # 成績サマリ更新用タイマー（1秒間隔）
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats)
        
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
        self.btn_show_log = QPushButton("ログを表示")
        self.btn_show_log.clicked.connect(self._on_show_log_clicked)
        self.progress_label = QLabel("Progress: --%")
        control_row.addWidget(self.btn_start)
        control_row.addWidget(self.btn_stop)
        control_row.addWidget(self.btn_show_log)
        control_row.addWidget(self.progress_label)
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
        
        # タブウィジェット（成績サマリと資産曲線）
        tabs_inner = QTabWidget(self)
        
        # 成績サマリ表示エリア（タブ1）
        summary_panel = QWidget(self)
        stats_layout = QVBoxLayout(summary_panel)
        stats_layout.setContentsMargins(4, 4, 4, 4)
        stats_layout.setSpacing(4)
        
        stats_label = QLabel("バックテスト成績サマリ", summary_panel)
        stats_label.setStyleSheet("font-weight: bold;")
        stats_layout.addWidget(stats_label)
        
        # 成績表示用のグリッドレイアウト
        self.stats_grid = QGridLayout()
        self.stats_labels = {}  # ラベルを保持する辞書
        self._stats_row_count = 0  # 現在の行数
        self._category_row_counts = {}  # カテゴリごとの行数
        
        # 全体統計
        self._add_stat_row("全体", "実行バー数", "bars", "0")
        self._add_stat_row("全体", "エントリー総数", "n_entries", "0")
        self._add_stat_row("全体", "スキップ数", "n_filter_fail", "0")
        self._add_stat_row("全体", "最大ドローダウン", "max_drawdown", "0.0%")
        self._add_stat_row("全体", "平均保有時間", "avg_holding_bars", "0.0 bars")
        self._add_stat_row("全体", "最大連敗", "loss_streak_max", "0")
        self._add_stat_row("全体", "最終資産", "final_equity", "0 JPY")
        self._add_stat_row("全体", "総損益", "total_pnl_jpy", "0 JPY")
        self._add_stat_row("全体", "総損益率", "total_pnl_pct", "0.0%")
        self._add_stat_row("全体", "Profit Factor", "profit_factor", "—")
        self._add_stat_row("全体", "期待値", "avg_pnl_per_trade", "0.0 JPY/trade")
        
        # Buy統計
        self._add_stat_row("Buy", "エントリー数", "buy_entries", "0")
        self._add_stat_row("Buy", "勝数", "buy_wins", "0")
        self._add_stat_row("Buy", "勝率", "buy_win_rate", "—")
        self._add_stat_row("Buy", "現在連勝数", "buy_consec_win", "0")
        self._add_stat_row("Buy", "最大連勝数", "buy_max_consec_win", "0")
        self._add_stat_row("Buy", "最大連敗数", "buy_loss_streak_max", "0")
        
        # Sell統計
        self._add_stat_row("Sell", "エントリー数", "sell_entries", "0")
        self._add_stat_row("Sell", "勝数", "sell_wins", "0")
        self._add_stat_row("Sell", "勝率", "sell_win_rate", "—")
        self._add_stat_row("Sell", "現在連勝数", "sell_consec_win", "0")
        self._add_stat_row("Sell", "最大連勝数", "sell_max_consec_win", "0")
        self._add_stat_row("Sell", "最大連敗数", "sell_loss_streak_max", "0")
        
        stats_layout.addLayout(self.stats_grid)
        stats_layout.addStretch(1)
        tabs_inner.addTab(summary_panel, "成績サマリ")
        
        # 資産曲線描画エリア（タブ2）
        chart_panel = QWidget(self)
        equity_layout = QVBoxLayout(chart_panel)
        equity_layout.setContentsMargins(8, 8, 8, 8)
        equity_layout.setSpacing(6)
        
        # カーソル値表示ラベル
        self.cursor_label = QLabel("Cursor: -", chart_panel)
        self.cursor_label.setStyleSheet("color: #666; font-size: 11px;")
        equity_layout.addWidget(self.cursor_label)
        
        self.equity_fig = Figure(figsize=(9, 3))
        self.equity_canvas = Canvas(self.equity_fig)
        self.equity_ax = self.equity_fig.add_subplot(111)
        self.equity_ax.set_xlabel("時間")
        self.equity_ax.set_ylabel("資産 (JPY)")
        self.equity_ax.grid(True)
        self.equity_line = None  # 後で初期化
        
        # canvas を stretch=1 で追加（領域を取りに行く）
        self.equity_canvas.setMinimumHeight(420)
        equity_layout.addWidget(self.equity_canvas, stretch=1)
        
        # Matplotlib のマウス移動イベントを接続
        self.equity_canvas.mpl_connect("motion_notify_event", self._on_equity_motion)
        
        tabs_inner.addTab(chart_panel, "資産曲線")
        
        root.addWidget(tabs_inner, 1)
        
        # ログ表示用の別ウィンドウ（非表示で保持）
        self._log_dialog = None
        self._log_text = None
        
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
            
            # 成績サマリ更新タイマーを開始
            self._stats_timer.start()
            
            # 進捗をリセット
            self._progress_percent = None
            self.progress_label.setText("Progress: --%")
            
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
            self.status_label.setText("途中停止")
            self._append_log("[VirtualBT] Stop requested")
            
            # 最終成績を更新（途中停止として）
            self._update_stats()
            
            # 進捗を「途中停止」と明示
            if self._progress_percent is not None:
                self.progress_label.setText(f"Progress: {self._progress_percent}% (途中停止)")
            else:
                self.progress_label.setText("Progress: --% (途中停止)")
        except Exception as e:
            QMessageBox.warning(self, "Virtual BT", f"停止処理でエラーが発生しました:\n{e}")
            self._append_log(f"[VirtualBT] ERROR on stop: {e}")
    
    def _on_service_finished(self, exit_code: int, exit_status: int):
        """サービスが終了したときの処理。"""
        self._set_running(False)
        
        # 最終成績を更新
        self._update_stats()
        
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
        
        # 進捗を最終値で固定
        if self._progress_percent is not None:
            self.progress_label.setText(f"Progress: {self._progress_percent}% (完了)")
        else:
            self.progress_label.setText("Progress: 100% (完了)")
    
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
            self._stats_timer.stop()
    
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
    
    def _add_stat_row(self, category: str, label: str, key: str, default: str = "0"):
        """成績サマリに1行追加する。"""
        # カテゴリごとの行数をカウント
        if category not in self._category_row_counts:
            self._category_row_counts[category] = 0
        
        category_rows = self._category_row_counts[category]
        
        stat_label = QLabel(label + ":", self)
        value_label = QLabel(default, self)
        value_label.setMinimumWidth(80)
        self.stats_labels[key] = value_label
        
        if category_rows == 0:
            # 最初の行はカテゴリラベルも表示
            cat_label = QLabel(f"{category}:", self)
            cat_label.setStyleSheet("font-weight: bold;")
            self.stats_grid.addWidget(cat_label, self._stats_row_count, 0)
            self.stats_grid.addWidget(stat_label, self._stats_row_count, 1)
            self.stats_grid.addWidget(value_label, self._stats_row_count, 2)
        else:
            # 2行目以降はカテゴリラベルは空
            self.stats_grid.addWidget(stat_label, self._stats_row_count, 1)
            self.stats_grid.addWidget(value_label, self._stats_row_count, 2)
        
        self._category_row_counts[category] += 1
        self._stats_row_count += 1
    
    def _append_log(self, text: str):
        """ログテキストに追加する（別ウィンドウ用）。"""
        if self._log_text is not None:
            self._log_text.appendPlainText(text)
        
        # 進捗情報を抽出
        if "[bt_progress]" in text:
            try:
                # [bt_progress] 42 の形式から数値を抽出
                parts = text.split("[bt_progress]")
                if len(parts) > 1:
                    pct_str = parts[1].strip().split()[0]
                    self._progress_percent = int(pct_str)
                    self.progress_label.setText(f"Progress: {self._progress_percent}%")
            except (ValueError, IndexError):
                pass
    
    def _on_log_output(self, text: str):
        """サービスからのリアルタイムログ出力を受け取る。"""
        self._append_log(text)
    
    def _on_show_log_clicked(self):
        """「ログを表示」ボタンがクリックされたときの処理。"""
        if self._log_dialog is None:
            # ログ表示用の別ウィンドウを作成
            self._log_dialog = QDialog(self)
            self._log_dialog.setWindowTitle("Virtual BT 実行ログ")
            self._log_dialog.resize(800, 600)
            
            layout = QVBoxLayout(self._log_dialog)
            
            self._log_text = QPlainTextEdit(self._log_dialog)
            self._log_text.setReadOnly(True)
            self._log_text.setPlaceholderText("実行ログがここに表示されます")
            self._log_text.setStyleSheet("QPlainTextEdit { font-size: 10px; }")
            layout.addWidget(self._log_text)
            
            # 閉じるボタン
            btn_close = QPushButton("閉じる", self._log_dialog)
            btn_close.clicked.connect(self._log_dialog.close)
            layout.addWidget(btn_close)
            
            # 既存のログを表示（サービスから取得）
            # 注意: サービス側にログを保持する機能がない場合は、ここで取得できない
            # その場合は実行中のログのみ表示される
        
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()
    
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
    
    def _update_stats(self):
        """成績サマリを更新する（live_stats.json を優先、なければ metrics.json/trades.csv）。"""
        out_dir = self._service.get_out_dir()
        if not out_dir:
            return
        
        try:
            import json
            
            # live_stats.json を優先的に読む（実行中リアルタイム更新）
            live_stats_path = out_dir / "live_stats.json"
            if live_stats_path.exists():
                stats = json.loads(live_stats_path.read_text(encoding="utf-8"))
                
                # 全体統計
                bars = stats.get("bars_processed", 0)
                n_entries = stats.get("n_entries", 0)
                n_filter_fail = stats.get("n_filter_fail", 0)
                max_dd = stats.get("max_drawdown", 0.0)
                avg_holding_bars = stats.get("avg_holding_bars", 0.0)
                
                if "bars" in self.stats_labels:
                    self.stats_labels["bars"].setText(str(bars))
                if "n_entries" in self.stats_labels:
                    self.stats_labels["n_entries"].setText(str(n_entries))
                if "n_filter_fail" in self.stats_labels:
                    self.stats_labels["n_filter_fail"].setText(str(n_filter_fail))
                if "max_drawdown" in self.stats_labels:
                    self.stats_labels["max_drawdown"].setText(f"{max_dd*100:.2f}%")
                if "avg_holding_bars" in self.stats_labels:
                    self.stats_labels["avg_holding_bars"].setText(f"{avg_holding_bars:.1f} bars")
                
                # 全体統計（追加項目）
                if "loss_streak_max" in self.stats_labels:
                    self.stats_labels["loss_streak_max"].setText(str(stats.get("loss_streak_max", 0)))
                
                final_equity = stats.get("final_equity", 0.0)
                if "final_equity" in self.stats_labels:
                    self.stats_labels["final_equity"].setText(f"{final_equity:,.0f} JPY")
                
                total_pnl_jpy = stats.get("total_pnl_jpy", 0.0)
                if "total_pnl_jpy" in self.stats_labels:
                    self.stats_labels["total_pnl_jpy"].setText(f"{total_pnl_jpy:+,.0f} JPY")
                
                total_pnl_pct = stats.get("total_pnl_pct", 0.0)
                if "total_pnl_pct" in self.stats_labels:
                    self.stats_labels["total_pnl_pct"].setText(f"{total_pnl_pct:+.2f}%")
                
                profit_factor = stats.get("profit_factor")
                if "profit_factor" in self.stats_labels:
                    if profit_factor is not None:
                        self.stats_labels["profit_factor"].setText(f"{profit_factor:.2f}")
                    else:
                        self.stats_labels["profit_factor"].setText("—")
                
                avg_pnl_per_trade = stats.get("avg_pnl_per_trade", 0.0)
                if "avg_pnl_per_trade" in self.stats_labels:
                    self.stats_labels["avg_pnl_per_trade"].setText(f"{avg_pnl_per_trade:+.1f} JPY/trade")
                
                # Buy/Sell 別統計
                if "buy_entries" in self.stats_labels:
                    self.stats_labels["buy_entries"].setText(str(stats.get("buy_entries", 0)))
                if "buy_wins" in self.stats_labels:
                    self.stats_labels["buy_wins"].setText(str(stats.get("buy_wins", 0)))
                
                buy_win_rate = stats.get("buy_win_rate")
                if "buy_win_rate" in self.stats_labels:
                    if buy_win_rate is not None:
                        self.stats_labels["buy_win_rate"].setText(f"{buy_win_rate*100:.1f}%")
                    else:
                        self.stats_labels["buy_win_rate"].setText("—")
                
                if "buy_max_consec_win" in self.stats_labels:
                    self.stats_labels["buy_max_consec_win"].setText(str(stats.get("buy_max_consec_win", 0)))
                if "buy_consec_win" in self.stats_labels:
                    self.stats_labels["buy_consec_win"].setText(str(stats.get("buy_consec_win", 0)))
                
                if "buy_loss_streak_max" in self.stats_labels:
                    self.stats_labels["buy_loss_streak_max"].setText(str(stats.get("buy_loss_streak_max", 0)))
                
                if "sell_entries" in self.stats_labels:
                    self.stats_labels["sell_entries"].setText(str(stats.get("sell_entries", 0)))
                if "sell_wins" in self.stats_labels:
                    self.stats_labels["sell_wins"].setText(str(stats.get("sell_wins", 0)))
                
                sell_win_rate = stats.get("sell_win_rate")
                if "sell_win_rate" in self.stats_labels:
                    if sell_win_rate is not None:
                        self.stats_labels["sell_win_rate"].setText(f"{sell_win_rate*100:.1f}%")
                    else:
                        self.stats_labels["sell_win_rate"].setText("—")
                
                if "sell_max_consec_win" in self.stats_labels:
                    self.stats_labels["sell_max_consec_win"].setText(str(stats.get("sell_max_consec_win", 0)))
                if "sell_consec_win" in self.stats_labels:
                    self.stats_labels["sell_consec_win"].setText(str(stats.get("sell_consec_win", 0)))
                
                if "sell_loss_streak_max" in self.stats_labels:
                    self.stats_labels["sell_loss_streak_max"].setText(str(stats.get("sell_loss_streak_max", 0)))
                
                return  # live_stats.json があればここで終了
            
            # フォールバック：metrics.json / trades.csv から取得（完了後・後方互換）
            metrics_path = out_dir / "metrics.json"
            if metrics_path.exists():
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                
                # 全体統計
                bars = metrics.get("bars", 0)
                n_entries = metrics.get("n_entries", 0)
                n_filter_fail = metrics.get("n_filter_fail", 0)
                max_dd = metrics.get("max_drawdown", 0.0)
                avg_holding_bars = metrics.get("avg_holding_bars", 0.0)
                
                if "bars" in self.stats_labels:
                    self.stats_labels["bars"].setText(str(bars))
                if "n_entries" in self.stats_labels:
                    self.stats_labels["n_entries"].setText(str(n_entries))
                if "n_filter_fail" in self.stats_labels:
                    self.stats_labels["n_filter_fail"].setText(str(n_filter_fail))
                if "max_drawdown" in self.stats_labels:
                    self.stats_labels["max_drawdown"].setText(f"{max_dd*100:.2f}%")
                if "avg_holding_bars" in self.stats_labels:
                    self.stats_labels["avg_holding_bars"].setText(f"{avg_holding_bars:.1f} bars")
            
            # equity_curve.csv から final_equity と initial_capital を取得
            equity_curve_path = out_dir / "equity_curve.csv"
            if equity_curve_path.exists():
                try:
                    import pandas as pd
                    equity_df = pd.read_csv(equity_curve_path)
                    if not equity_df.empty and "equity" in equity_df.columns:
                        final_equity = float(equity_df["equity"].iloc[-1])
                        initial_capital = float(equity_df["equity"].iloc[0])
                        total_pnl_jpy = final_equity - initial_capital
                        if initial_capital > 0:
                            total_pnl_pct = (total_pnl_jpy / initial_capital) * 100.0
                        else:
                            total_pnl_pct = 0.0
                        
                        if "final_equity" in self.stats_labels:
                            self.stats_labels["final_equity"].setText(f"{final_equity:,.0f} JPY")
                        if "total_pnl_jpy" in self.stats_labels:
                            self.stats_labels["total_pnl_jpy"].setText(f"{total_pnl_jpy:+,.0f} JPY")
                        if "total_pnl_pct" in self.stats_labels:
                            self.stats_labels["total_pnl_pct"].setText(f"{total_pnl_pct:+.2f}%")
                except Exception:
                    # 取得失敗時は無視
                    pass
            
            # trades.csv を読み込んで Buy/Sell 別統計を計算
            trades_path = out_dir / "trades.csv"
            if trades_path.exists():
                import pandas as pd
                trades_df = pd.read_csv(trades_path)
                
                if not trades_df.empty and "side" in trades_df.columns:
                    # exit_time でソート（時系列順に確定）
                    if "exit_time" in trades_df.columns:
                        trades_df = trades_df.sort_values("exit_time").reset_index(drop=True)
                    
                    # 全体統計（追加項目）
                    loss_streak_max = self._calc_max_consecutive(trades_df, False)
                    if "loss_streak_max" in self.stats_labels:
                        self.stats_labels["loss_streak_max"].setText(str(loss_streak_max))
                    
                    # Profit Factor
                    if "pnl" in trades_df.columns:
                        win_pnl_sum = trades_df[trades_df["pnl"] > 0]["pnl"].sum()
                        loss_pnl_sum = abs(trades_df[trades_df["pnl"] < 0]["pnl"].sum())
                        if loss_pnl_sum > 0:
                            profit_factor = float(win_pnl_sum / loss_pnl_sum)
                            if "profit_factor" in self.stats_labels:
                                self.stats_labels["profit_factor"].setText(f"{profit_factor:.2f}")
                        else:
                            if "profit_factor" in self.stats_labels:
                                self.stats_labels["profit_factor"].setText("—")
                        
                        # 1トレードあたり期待値
                        avg_pnl_per_trade = float(trades_df["pnl"].mean())
                        if "avg_pnl_per_trade" in self.stats_labels:
                            self.stats_labels["avg_pnl_per_trade"].setText(f"{avg_pnl_per_trade:+.1f} JPY/trade")
                    
                    # Buy統計
                    buy_trades = trades_df[trades_df["side"] == "BUY"]
                    buy_entries = len(buy_trades)
                    buy_wins = len(buy_trades[buy_trades["pnl"] > 0]) if buy_entries > 0 else 0
                    buy_max_consec_win = self._calc_max_consecutive(buy_trades, True)
                    buy_consec_win = self._calc_current_consecutive(buy_trades, True)
                    buy_loss_streak_max = self._calc_max_consecutive(buy_trades, False)
                    
                    if "buy_entries" in self.stats_labels:
                        self.stats_labels["buy_entries"].setText(str(buy_entries))
                    if "buy_wins" in self.stats_labels:
                        self.stats_labels["buy_wins"].setText(str(buy_wins))
                    
                    if buy_entries > 0:
                        buy_win_rate = float(buy_wins) / buy_entries
                        if "buy_win_rate" in self.stats_labels:
                            self.stats_labels["buy_win_rate"].setText(f"{buy_win_rate*100:.1f}%")
                    else:
                        if "buy_win_rate" in self.stats_labels:
                            self.stats_labels["buy_win_rate"].setText("—")
                    
                    if "buy_max_consec_win" in self.stats_labels:
                        self.stats_labels["buy_max_consec_win"].setText(str(buy_max_consec_win))
                    if "buy_consec_win" in self.stats_labels:
                        self.stats_labels["buy_consec_win"].setText(str(buy_consec_win))
                    if "buy_loss_streak_max" in self.stats_labels:
                        self.stats_labels["buy_loss_streak_max"].setText(str(buy_loss_streak_max))
                    
                    # Sell統計
                    sell_trades = trades_df[trades_df["side"] == "SELL"]
                    sell_entries = len(sell_trades)
                    sell_wins = len(sell_trades[sell_trades["pnl"] > 0]) if sell_entries > 0 else 0
                    sell_max_consec_win = self._calc_max_consecutive(sell_trades, True)
                    sell_consec_win = self._calc_current_consecutive(sell_trades, True)
                    sell_loss_streak_max = self._calc_max_consecutive(sell_trades, False)
                    
                    if "sell_entries" in self.stats_labels:
                        self.stats_labels["sell_entries"].setText(str(sell_entries))
                    if "sell_wins" in self.stats_labels:
                        self.stats_labels["sell_wins"].setText(str(sell_wins))
                    
                    if sell_entries > 0:
                        sell_win_rate = float(sell_wins) / sell_entries
                        if "sell_win_rate" in self.stats_labels:
                            self.stats_labels["sell_win_rate"].setText(f"{sell_win_rate*100:.1f}%")
                    else:
                        if "sell_win_rate" in self.stats_labels:
                            self.stats_labels["sell_win_rate"].setText("—")
                    
                    if "sell_max_consec_win" in self.stats_labels:
                        self.stats_labels["sell_max_consec_win"].setText(str(sell_max_consec_win))
                    if "sell_consec_win" in self.stats_labels:
                        self.stats_labels["sell_consec_win"].setText(str(sell_consec_win))
                    if "sell_loss_streak_max" in self.stats_labels:
                        self.stats_labels["sell_loss_streak_max"].setText(str(sell_loss_streak_max))
        except Exception as e:
            # 更新失敗でもアプリは継続
            pass
    
    def _calc_max_consecutive(self, trades_df, is_win: bool) -> int:
        """最大連勝/連敗数を計算する。"""
        if trades_df.empty or "pnl" not in trades_df.columns:
            return 0
        
        wins = (trades_df["pnl"] > 0).astype(int)
        if not is_win:
            wins = 1 - wins
        
        max_consec = 0
        current = 0
        for w in wins:
            if w == 1:
                current += 1
                max_consec = max(max_consec, current)
            else:
                current = 0
        
        return max_consec
    
    def _calc_current_consecutive(self, trades_df, is_win: bool) -> int:
        """現在の連勝/連敗数を計算する（最後から数える）。"""
        if trades_df.empty or "pnl" not in trades_df.columns:
            return 0
        
        wins = (trades_df["pnl"] > 0).astype(int)
        if not is_win:
            wins = 1 - wins
        
        # 最後から連続する数を数える
        current = 0
        for w in reversed(wins):
            if w == 1:
                current += 1
            else:
                break
        
        return current
    
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
    
    def _on_equity_motion(self, event):
        """マウス移動イベント：カーソル位置の日時・資産値を表示する。"""
        try:
            # グラフ外に出たら "Cursor: -" に戻す
            if event.inaxes != self.equity_ax:
                self.cursor_label.setText("Cursor: -")
                return
            
            # xdata, ydata が None の場合は表示しない
            if event.xdata is None or event.ydata is None:
                self.cursor_label.setText("Cursor: -")
                return
            
            # xdata を datetime に変換（Matplotlib の日付形式）
            try:
                dt = mdates.num2date(event.xdata)
                # タイムゾーン情報を削除（表示用）
                if hasattr(dt, 'tz_localize'):
                    dt = dt.tz_localize(None)
                elif hasattr(dt, 'replace'):
                    dt = dt.replace(tzinfo=None)
            except (ValueError, TypeError, OverflowError):
                # 日付変換に失敗した場合は数値として表示
                self.cursor_label.setText(f"Cursor: {event.xdata:.1f} Equity: {event.ydata:,.0f} JPY")
                return
            
            # ydata を JPY としてフォーマット
            equity_value = float(event.ydata)
            
            # ラベル更新
            self.cursor_label.setText(
                f"Cursor: {dt.strftime('%Y-%m-%d %H:%M')} Equity: {equity_value:,.0f} JPY"
            )
        except Exception:
            # エラー時は "Cursor: -" に戻す
            self.cursor_label.setText("Cursor: -")
