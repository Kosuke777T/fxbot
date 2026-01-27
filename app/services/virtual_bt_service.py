# app/services/virtual_bt_service.py
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QProcess, QObject, pyqtSignal


class VirtualBacktestService(QObject):
    """
    Virtual BT（仮想実行バックテスト）の実行を管理するサービス。
    
    - run_id生成とout_dir作成
    - QProcessでtools/backtest_run.pyを起動
    - stdout/stderrをbt_app.logに保存
    - 安全な停止処理
    """
    
    # シグナル（必要に応じて拡張）
    finished = pyqtSignal(int, int)  # exit_code, exit_status
    error_occurred = pyqtSignal(str)  # error_message
    log_output = pyqtSignal(str)  # ログ出力（リアルタイム表示用）
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._run_id: Optional[str] = None
        self._out_dir: Optional[Path] = None
        self._log_file: Optional[Path] = None
        # equity_curve.csv の差分読取用
        self._equity_csv_path: Optional[Path] = None
        self._equity_file_handle = None
        self._equity_last_pos: int = 0
        self._equity_header_skipped: bool = False
        
    def generate_run_id(self) -> str:
        """
        run_idを生成する（衝突しない形式）。
        形式: YYYYMMDD_HHMMSS_fff（ミリ秒まで含む）
        """
        now = datetime.now()
        return now.strftime("%Y%m%d_%H%M%S_%f")[:-3]  # ミリ秒まで（マイクロ秒の上位3桁）
    
    def prepare_out_dir(self, run_id: str, base_dir: Optional[Path] = None) -> Path:
        """
        出力ディレクトリを作成する。
        
        Parameters
        ----------
        run_id : str
            実行ID
        base_dir : Path, optional
            ベースディレクトリ（Noneの場合はプロジェクトルート基準）
            
        Returns
        -------
        Path
            作成された出力ディレクトリ
        """
        if base_dir is None:
            # プロジェクトルート基準で logs/BTlog/runs/<run_id> を作成
            project_root = Path(__file__).resolve().parents[2]
            base_dir = project_root / "logs" / "BTlog" / "runs"
        
        out_dir = base_dir / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir
    
    def start_run(
        self,
        csv_path: str,
        symbol: str = "USDJPY-",
        timeframe: str = "M5",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        capital: float = 100000.0,
        profile: str = "michibiki_std",
        init_position: str = "flat",
    ) -> tuple[str, Path]:
        """
        バックテスト実行を開始する。
        
        Parameters
        ----------
        csv_path : str
            CSVファイルパス
        symbol : str
            シンボル名
        timeframe : str
            タイムフレーム
        start_date : str, optional
            開始日（YYYY-MM-DD形式）
        end_date : str, optional
            終了日（YYYY-MM-DD形式）
        capital : float
            初期資本
        profile : str
            プロファイル名
        init_position : str
            初期ポジション（flat/carry）
            
        Returns
        -------
        tuple[str, Path]
            (run_id, out_dir)
        """
        if self._process is not None:
            state = self._process.state()
            if state != QProcess.ProcessState.NotRunning:
                raise RuntimeError("既に実行中です。先にstop_run()を呼び出してください。")
        
        # run_id生成とout_dir作成
        self._run_id = self.generate_run_id()
        self._out_dir = self.prepare_out_dir(self._run_id)
        
        # ログファイルパス
        self._log_file = self._out_dir / "bt_app.log"
        
        # equity_curve.csv のパスを設定
        self._equity_csv_path = self._out_dir / "equity_curve.csv"
        self._equity_last_pos = 0
        self._equity_header_skipped = False
        
        # matplotlibキャッシュディレクトリを作成
        mpl_cache = self._out_dir / ".mplconfig"
        mpl_cache.mkdir(parents=True, exist_ok=True)
        
        # config.jsonを作成（実行条件を保存）
        config = {
            "csv_path": str(csv_path),
            "symbol": symbol,
            "timeframe": timeframe,
            "start_date": start_date,
            "end_date": end_date,
            "capital": capital,
            "profile": profile,
            "init_position": init_position,
            "run_id": self._run_id,
            "out_dir": str(self._out_dir),
        }
        import json
        config_path = self._out_dir / "config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        
        # QProcessでtools/backtest_run.pyを起動
        project_root = Path(__file__).resolve().parents[2]
        runner = project_root / "tools" / "backtest_run.py"
        
        if not runner.exists():
            raise FileNotFoundError(f"tools/backtest_run.py が見つかりません: {runner}")
        
        self._process = QProcess(self)
        self._process.setWorkingDirectory(str(project_root))
        
        # 環境変数設定（UTF-8 + 親プロセスの環境を引き継ぎ + matplotlibキャッシュ）
        env = self._process.processEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        
        # 親の環境から home 系を補完（無い場合のみ）
        for k in ("USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH"):
            v = os.environ.get(k)
            if v and env.value(k, "") == "":
                env.insert(k, v)
        
        # matplotlib が Path.home を使えなくてもここにキャッシュできる
        env.insert("MPLCONFIGDIR", str(mpl_cache))
        
        self._process.setProcessEnvironment(env)
        
        # 出力をマージ（stdout/stderrを統合）
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        
        # 引数構築
        args = [
            str(runner),
            "--csv", str(csv_path),
            "--symbol", symbol,
            "--timeframe", timeframe,
            "--profile", profile,
            "--init-position", init_position,
            "--capital", str(capital),
            "--out-dir", str(self._out_dir),
        ]
        
        if start_date:
            args.extend(["--start-date", start_date])
        if end_date:
            args.extend(["--end-date", end_date])
        
        # シグナル接続
        self._process.readyReadStandardOutput.connect(self._on_ready_read)
        self._process.finished.connect(self._on_finished)
        
        # ログファイルを開く（追記モード）
        self._log_file_handle = open(self._log_file, "a", encoding="utf-8")
        
        # プロセス起動
        exe = sys.executable
        self._process.start(exe, args)
        
        if not self._process.waitForStarted(2000):
            error_msg = f"プロセスを開始できませんでした: {exe} {' '.join(args)}"
            self._log_file_handle.close()
            self._process = None
            raise RuntimeError(error_msg)
        
        # 起動ログ
        self._write_log(f"[VirtualBT] Started run_id={self._run_id}")
        self._write_log(f"[VirtualBT] Command: {exe} {' '.join(args)}")
        self._write_log(f"[VirtualBT] Out dir: {self._out_dir}")
        
        return self._run_id, self._out_dir
    
    def stop_run(self) -> None:
        """
        実行中のバックテストを安全に停止する。
        terminate → wait → kill の順で試行する。
        """
        if self._process is None:
            return
        
        state = self._process.state()
        if state == QProcess.ProcessState.NotRunning:
            self._process = None
            return
        
        self._write_log("[VirtualBT] Stopping process...")
        
        # terminate を試行
        self._process.terminate()
        if not self._process.waitForFinished(3000):
            # 3秒待っても終了しない場合は kill
            self._write_log("[VirtualBT] Process did not terminate, killing...")
            self._process.kill()
            self._process.waitForFinished(1000)
        
        self._write_log("[VirtualBT] Process stopped")
        
        # ログファイルを閉じる
        if hasattr(self, "_log_file_handle") and self._log_file_handle:
            try:
                self._log_file_handle.close()
            except Exception:
                pass
        
        # equity_curve.csv のファイルハンドルを閉じる
        if self._equity_file_handle is not None:
            try:
                self._equity_file_handle.close()
                self._equity_file_handle = None
            except Exception:
                pass
        
        self._process = None
    
    def is_running(self) -> bool:
        """実行中かどうかを返す。"""
        if self._process is None:
            return False
        state = self._process.state()
        return state != QProcess.ProcessState.NotRunning
    
    def get_run_id(self) -> Optional[str]:
        """現在のrun_idを返す。"""
        return self._run_id
    
    def get_out_dir(self) -> Optional[Path]:
        """現在のout_dirを返す。"""
        return self._out_dir
    
    def _on_ready_read(self) -> None:
        """プロセスの標準出力を読み取ってログファイルに書き込む。"""
        if self._process is None:
            return
        
        try:
            raw = bytes(self._process.readAllStandardOutput())
            if raw:
                # UTF-8でデコード（エラー時はreplace）
                text = raw.decode("utf-8", errors="replace")
                self._write_log(text, flush=True)
                # GUIにリアルタイムで通知
                for line in text.splitlines():
                    if line.strip():
                        self.log_output.emit(line)
        except Exception as e:
            # エラーは無視（ログ出力の失敗でプロセスを止めない）
            pass
    
    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """プロセス終了時の処理。"""
        try:
            # enumを安全にint化（NormalExit=0, それ以外=1）
            status_int = 0 if exit_status == QProcess.ExitStatus.NormalExit else 1
            self._write_log(f"[VirtualBT] Process finished: exit_code={exit_code}, exit_status={exit_status} (status_int={status_int})")
        except Exception as e:
            # ここで落ちるとGUIが死ぬので絶対に握りつぶしてログへ
            self._write_log(f"[VirtualBT] _on_finished handler error: {e!r}")
            status_int = 1  # 失敗扱い
        
        # metrics.jsonの存在確認
        if self._out_dir:
            metrics_path = self._out_dir / "metrics.json"
            if not metrics_path.exists():
                self._write_log("[VirtualBT] WARNING: metrics.json was not generated")
        
        # ログファイルを閉じる
        if hasattr(self, "_log_file_handle") and self._log_file_handle:
            try:
                self._log_file_handle.close()
            except Exception:
                pass
        
        # equity_curve.csv のファイルハンドルを閉じる
        if self._equity_file_handle is not None:
            try:
                self._equity_file_handle.close()
                self._equity_file_handle = None
            except Exception:
                pass
        
        # シグナル発火（例外処理で保護）
        try:
            self.finished.emit(int(exit_code), status_int)
        except Exception:
            # 失敗扱いで通知だけは出す
            try:
                self.finished.emit(int(exit_code), 1)
            except Exception:
                pass
        
        # プロセス参照をクリア
        self._process = None
    
    def _write_log(self, text: str, flush: bool = False) -> None:
        """ログファイルに書き込む。"""
        if not hasattr(self, "_log_file_handle") or not self._log_file_handle:
            return
        
        try:
            self._log_file_handle.write(text)
            if not text.endswith("\n"):
                self._log_file_handle.write("\n")
            if flush:
                self._log_file_handle.flush()
        except Exception:
            pass
    
    def read_equity_curve_diff(self) -> list[dict]:
        """
        equity_curve.csv の差分を読み取る（全読み込み禁止、差分のみ）。
        
        Returns
        -------
        list[dict]
            新しく追加された行のリスト。各要素は {"time": str, "equity": float, "signal": str}
        """
        if self._equity_csv_path is None or not self._equity_csv_path.exists():
            return []
        
        new_rows = []
        try:
            # ファイルを開く（初回のみ）
            if self._equity_file_handle is None:
                self._equity_file_handle = open(self._equity_csv_path, "r", encoding="utf-8")
                self._equity_last_pos = 0
                self._equity_header_skipped = False
            
            # 前回の位置に移動
            self._equity_file_handle.seek(self._equity_last_pos)
            
            # 新しい行を読み取る
            for line in self._equity_file_handle:
                line = line.strip()
                if not line:
                    continue
                
                # ヘッダー行をスキップ（初回のみ）
                if not self._equity_header_skipped:
                    if line.startswith("time,equity,signal"):
                        self._equity_header_skipped = True
                        continue
                
                # CSV行をパース
                parts = line.split(",")
                if len(parts) >= 2:
                    try:
                        time_str = parts[0].strip()
                        equity_str = parts[1].strip()
                        signal_str = parts[2].strip() if len(parts) > 2 else "HOLD"
                        equity_val = float(equity_str)
                        new_rows.append({
                            "time": time_str,
                            "equity": equity_val,
                            "signal": signal_str,
                        })
                    except (ValueError, IndexError):
                        # パース失敗は無視（不正な行）
                        continue
            
            # 現在の位置を記録
            self._equity_last_pos = self._equity_file_handle.tell()
            
        except Exception as e:
            # 読取失敗時は空リストを返す（アプリは継続）
            pass
        
        return new_rows
