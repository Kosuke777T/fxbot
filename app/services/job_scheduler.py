# app/services/job_scheduler.py

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import datetime as _dt
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class JobScheduler:
    """
    内蔵スケジューラ（v5.1 準拠）

    State machine: IDLE → RUNNING → SUCCESS/FAILED → IDLE
    last_run_at で重複防止（必須）
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Parameters
        ----------
        config_path : Path, optional
            ジョブ設定ファイルのパス（未指定なら configs/scheduler.yaml を探索）
        """
        self.config_path = config_path or Path("configs/scheduler.yaml")
        self.jobs: List[Dict[str, Any]] = []
        self.jobs_state: Dict[str, Dict[str, Any]] = {}
        self._scheduler_level_cfg: int | None = None
        self._jobs_loaded: bool = False  # 冪等化用フラグ
        self._load_jobs()

    def _load_jobs(self) -> None:
        """ジョブ設定を読み込む（YAML または JSON）。"""
        # 冪等化：既にロード済みの場合は早期リターン
        if self._jobs_loaded:
            pid = os.getpid()
            scheduler_id = id(self)
            logger.info(f"[JobScheduler] _load_jobs ignored already_loaded=True pid={pid} scheduler_id={scheduler_id}")
            return

        if not self.config_path.exists():
            logger.warning(f"[JobScheduler] config not found: {self.config_path}, using empty jobs")
            self.jobs = []
            # ファイルが存在しない場合は _jobs_loaded を True にしない（再試行可能）
            return

        try:
            import yaml

            with self.config_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # scheduler_level を読み込む（-1 は未設定を意味）
            scheduler_level_raw = data.get("scheduler_level")
            if scheduler_level_raw is not None:
                try:
                    self._scheduler_level_cfg = int(scheduler_level_raw)
                except (ValueError, TypeError):
                    logger.warning(f"[JobScheduler] invalid scheduler_level: {scheduler_level_raw}, using None")
                    self._scheduler_level_cfg = None
            else:
                self._scheduler_level_cfg = None

            jobs_raw = data.get("jobs", [])
            if not isinstance(jobs_raw, list):
                logger.warning(f"[JobScheduler] jobs is not list in {self.config_path}")
                self.jobs = []
                # jobs is not list の場合は _jobs_loaded を True にしない（再試行可能）
                return
            self.jobs = jobs_raw
            job_ids = [str(j.get("id") or j.get("name") or "?") for j in self.jobs]
            # 観測用：pid, scheduler_id, thread_id を出力（二重生成 vs ログ多重の切り分け用）
            pid = os.getpid()
            scheduler_id = id(self)
            thread_id = threading.get_ident()
            logger.info(
                f"[JobScheduler] loaded {len(self.jobs)} jobs from {self.config_path} (scheduler_level={self._scheduler_level_cfg}) "
                f"pid={pid} scheduler_id={scheduler_id} thread_id={thread_id}"
            )
            logger.info(f"[JobScheduler] job_ids={job_ids}")
            # ロード成功時にフラグをセット（冪等化）
            self._jobs_loaded = True
        except Exception as e:
            logger.error(f"[JobScheduler] failed to load jobs from {self.config_path}: {e}")
            self.jobs = []
            # 例外でロード失敗した場合は _jobs_loaded を True にしない（再試行可能）

        # 読み込んだ jobs に対して jobs_state を初期化
        self.jobs_state = self.jobs_state or {}
        for job in self.jobs:
            job_id = str(job.get("id") or job.get("name") or "?")
            if job_id not in self.jobs_state:
                self.jobs_state[job_id] = {
                    "state": "IDLE",
                    "last_run_at": None,
                    "last_result": None,
                    "next_run_at": None,  # run_always用
                }

    def _initialize_job_state(self, job_id: str) -> None:
        """ジョブ状態を初期化（初回実行時のみ）。"""
        if job_id not in self.jobs_state:
            self.jobs_state[job_id] = {
                "state": "IDLE",
                "last_run_at": None,
                "last_result": None,
                "next_run_at": None,  # run_always用
            }

    def _edition_allow(self, job: Dict[str, Any]) -> bool:
        """
        Edition 制御によるジョブ実行可否を判定する（T-41 実装）。

        Parameters
        ----------
        job : dict
            ジョブ定義

        Returns
        -------
        bool
            実行可能な場合 True
        """
        from app.services.scheduler_guard import allow_job_by_scheduler_level

        ok, reason = allow_job_by_scheduler_level(job, self._scheduler_level_cfg)
        if not ok:
            logger.debug(f"[JobScheduler] job {job.get('id')} denied: {reason}")
        return ok

    def _should_run(self, job: Dict[str, Any], now: datetime) -> bool:
        """
        ジョブを実行すべきか判定する（重複防止含む）。

        Parameters
        ----------
        job : dict
            ジョブ定義
        now : datetime
            現在時刻（timezone aware 推奨）

        Returns
        -------
        bool
            実行すべき場合 True
        """
        job_id = str(job.get("id") or job.get("name") or "?")
        self._initialize_job_state(job_id)

        # enabled チェック
        if not job.get("enabled", True):
            return False

        # 状態チェック（RUNNING 中は実行しない）
        state = self.jobs_state[job_id].get("state", "IDLE")
        if state == "RUNNING":
            logger.debug(f"[JobScheduler] job {job_id} is already RUNNING, skip")
            return False

        # スケジュールチェック（weekday/hour/minute）
        weekday = job.get("weekday")
        hour = job.get("hour")
        minute = job.get("minute")

        # weekday/hour/minute がすべて None の場合の処理
        if weekday is None and hour is None and minute is None:
            run_always = bool(job.get("run_always", False))
            if not run_always:
                # run_always=False の場合は完全除外（永久機関防止）
                logger.debug(f"[JobScheduler] job {job_id} has no schedule and run_always=False, skip")
                return False

            # run_always=True の場合：高頻度実行（最低1分刻み）
            logger.debug(f"[JobScheduler] job {job_id} run_always=True, checking next_run_at")
            # jobs_state に next_run_at を保存して、1分以上経過していれば実行可能
            next_run_at_str = self.jobs_state[job_id].get("next_run_at")
            if next_run_at_str:
                try:
                    next_run_at = datetime.fromisoformat(next_run_at_str.replace("Z", "+00:00"))
                    if now < next_run_at:
                        logger.debug(f"[JobScheduler] job {job_id} run_always: next_run_at={next_run_at_str} not reached, skip")
                        return False
                except Exception as e:
                    logger.warning(f"[JobScheduler] failed to parse next_run_at for {job_id}: {e}")
            # next_run_at が無い、または既に経過している場合は実行可能
            return True  # run_always(no-schedule): bypass last_run_at daily guard

        # 通常のスケジュールチェック（weekday/hour/minute のいずれかが設定されている場合）
        if weekday is not None:
            if now.weekday() != weekday:
                return False
        if hour is not None:
            if now.hour != hour:
                return False
        if minute is not None:
            if now.minute != minute:
                return False

        # last_run_at による重複防止
        last_run_at_str = self.jobs_state[job_id].get("last_run_at")
        if last_run_at_str:
            try:
                # ISO形式文字列をパース
                last_run_at = datetime.fromisoformat(last_run_at_str.replace("Z", "+00:00"))
                # 同じスケジュール枠（weekday/hour/minute）で既に実行済みならスキップ
                if (
                    weekday is None or last_run_at.weekday() == weekday
                ) and (
                    hour is None or last_run_at.hour == hour
                ) and (
                    minute is None or last_run_at.minute == minute
                ):
                    # 同じ日付ならスキップ（より厳密な判定）
                    if last_run_at.date() == now.date():
                        logger.debug(
                            f"[JobScheduler] job {job_id} already ran today at {last_run_at_str}, skip"
                        )
                        return False
            except Exception as e:
                logger.warning(f"[JobScheduler] failed to parse last_run_at for {job_id}: {e}")

        return True

    def _run_job(self, job: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        """
        ジョブを実行する（内部実装、状態更新は呼び出し元で行う）。

        Parameters
        ----------
        job : dict
            ジョブ定義
        now : datetime
            現在時刻

        Returns
        -------
        dict
            実行結果: {"ok": bool, "rc": int, "stdout": str, "stderr": str, "error": dict|None}
        """
        job_id = str(job.get("id") or job.get("name") or "?")
        command = job.get("command")
        if not command:
            return {
                "ok": False,
                "rc": -1,
                "stdout": "",
                "stderr": "",
                "error": {"code": "NO_COMMAND", "message": "job has no command"},
            }

        logger.info(f"[JobScheduler][run] mode=subprocess job={job_id} cmd={command}")

        try:
            # コマンドを実行（シェル経由）
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=3600,  # 1時間タイムアウト
                encoding="utf-8",
                errors="replace",
            )

            rc = result.returncode
            ok = rc == 0

            logger.info(f"[JobScheduler] job {job_id} finished: rc={rc}")

            return {
                "ok": ok,
                "rc": rc,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
                "error": None,
            }

        except subprocess.TimeoutExpired:
            error = {"code": "TIMEOUT", "message": "job execution timeout (3600s)"}
            logger.error(f"[JobScheduler] job {job_id} timeout")
            return {
                "ok": False,
                "rc": -1,
                "stdout": "",
                "stderr": "",
                "error": error,
            }

        except Exception as e:
            error = {"code": "EXECUTION_ERROR", "message": str(e)}
            logger.exception(f"[JobScheduler] job {job_id} failed: {e}")
            return {
                "ok": False,
                "rc": -1,
                "stdout": "",
                "stderr": "",
                "error": error,
            }

    def run_pending(self) -> List[Dict[str, Any]]:
        """
        実行すべきジョブを実行する（Public API）。

        Returns
        -------
        list[dict]
            実行結果のリスト（各要素は {"job_id": str, "result": dict}）
        """
        logger.info("[JobScheduler][tick] run_pending called now_epoch={} now_local={}", int(time.time()), _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        now = datetime.now(timezone.utc)

        # Edition 制御でフィルタリング（T-41: scheduler_guard を統合済み）
        filtered_jobs = [job for job in self.jobs if self._edition_allow(job)]

        results: List[Dict[str, Any]] = []

        for job in filtered_jobs:
            job_id = str(job.get("id") or job.get("name") or "?")
            self._initialize_job_state(job_id)

            should_run = self._should_run(job, now)
            if not should_run:
                logger.debug(f"[JobScheduler] job {job_id} _should_run=False, skip")
                continue
            logger.debug(f"[JobScheduler] job {job_id} _should_run=True, executing")

            # 実行する直前に state を RUNNING に更新し、last_run_at を設定（最重要）
            st = self.jobs_state[job_id]
            st["state"] = "RUNNING"
            now_iso = now.isoformat()
            st["last_run_at"] = now_iso

            # ジョブを実行
            result = self._run_job(job, now)

            # 実行結果に応じて SUCCESS または FAILED を設定
            ok = result.get("ok", False)
            if ok:
                st["state"] = "SUCCESS"
            else:
                st["state"] = "FAILED"
                # 失敗時はstderrもログに出す（観測用）
                stderr = result.get("stderr", "")
                if stderr:
                    logger.warning(f"[JobScheduler] job {job_id} stderr: {stderr[:500]}")

            # 結果を保存（stdout/stderr/errorが無い場合に補完）
            last_result = {
                "ok": ok,
                "rc": result.get("rc", -1),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "error": result.get("error"),
            }
            # stdout/stderrが無い場合は空文字列で補完
            if "stdout" not in last_result:
                last_result["stdout"] = ""
            if "stderr" not in last_result:
                last_result["stderr"] = ""
            if "error" not in last_result:
                last_result["error"] = None
            st["last_result"] = last_result

            # run_always=True の場合、next_run_at を「今 + 1分」に更新（高頻度実行の制御）
            weekday = job.get("weekday")
            hour = job.get("hour")
            minute = job.get("minute")
            if weekday is None and hour is None and minute is None and bool(job.get("run_always", False)):
                from datetime import timedelta
                next_run = now + timedelta(minutes=1)
                st["next_run_at"] = next_run.isoformat()
                logger.debug(f"[JobScheduler] job {job_id} run_always: next_run_at updated to {next_run.isoformat()}")

            results.append({"job_id": job_id, "result": result})

            # 最後に IDLE に戻す（ToDo準拠）
            st["state"] = "IDLE"

            # T-40: chain（ジョブ連鎖）は未実装（ToDo に無いため削除）

        return results

    def get_jobs(self) -> List[Dict[str, Any]]:
        """
        全ジョブのリストを返す（Public API）。

        Returns
        -------
        list[dict]
            ジョブ定義のリスト
        """
        return list(self.jobs)

    def get_job_state(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        指定ジョブの状態を返す（Public API）。

        Parameters
        ----------
        job_id : str
            ジョブID

        Returns
        -------
        dict | None
            ジョブ状態: {"state": str, "last_run_at": str|None, "last_result": dict|None}
            ジョブが存在しない場合は None
        """
        job = next((j for j in self.jobs if str(j.get("id") or j.get("name")) == job_id), None)
        if not job:
            return None

        self._initialize_job_state(job_id)
        return dict(self.jobs_state[job_id])

    def add_job(self, job: Dict[str, Any]) -> None:
        """
        ジョブを追加する（Public API）。

        Parameters
        ----------
        job : dict
            ジョブ定義（id, enabled, weekday, hour, minute, command など）
        """
        job_id = str(job.get("id") or job.get("name") or "?")
        # 既存ジョブを置き換え
        existing = next((j for j in self.jobs if str(j.get("id") or j.get("name")) == job_id), None)
        if existing:
            idx = self.jobs.index(existing)
            self.jobs[idx] = job
            logger.info(f"[JobScheduler] updated job: {job_id}")
        else:
            self.jobs.append(job)
            logger.info(f"[JobScheduler] added job: {job_id}")

    def remove_job(self, job_id: str) -> bool:
        """
        ジョブを削除する（Public API）。

        Parameters
        ----------
        job_id : str
            ジョブID

        Returns
        -------
        bool
            削除成功時 True、ジョブが存在しない場合 False
        """
        job = next((j for j in self.jobs if str(j.get("id") or j.get("name")) == job_id), None)
        if not job:
            return False

        self.jobs.remove(job)
        # 状態も削除
        self.jobs_state.pop(job_id, None)
        logger.info(f"[JobScheduler] removed job: {job_id}")
        return True

    def reload(self) -> None:
        """
        ジョブ設定を再読み込みする（Public API）。
        """
        # 冪等化フラグをリセットして再読込を可能にする
        self._jobs_loaded = False
        self._load_jobs()
        logger.info(f"[JobScheduler] reloaded {len(self.jobs)} jobs")



# --- T-42-3-3: persistence helpers ---

    def _save_jobs(self) -> None:
        """Persist current jobs to configs/scheduler.yaml (atomic write)."""
        try:
            import yaml  # type: ignore
        except Exception as e:
            raise RuntimeError("PyYAML is required to save scheduler.yaml") from e

        p = self.config_path

        # load existing doc (preserve top-level keys)
        doc = {}
        if p.exists():
            try:
                doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                doc = {}

        if not isinstance(doc, dict):
            doc = {}

        # keep scheduler_level if already present; otherwise reflect current cfg
        if "scheduler_level" not in doc:
            doc["scheduler_level"] = self._scheduler_level_cfg

        doc["jobs"] = list(self.jobs or [])

        # atomic write: tmp -> replace
        tmp = Path(str(p) + ".tmp")
        data = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(p)

    def _add_job(self, job: dict) -> None:
        # job_id の存在は必須（GUI側でも保証するが二重化）
        jid = (job or {}).get("id")
        if not jid:
            raise ValueError("job.id is required")

        # 既存IDがあれば上書き（更新扱い）
        new_jobs = [j for j in (self.jobs or []) if j.get("id") != jid]
        new_jobs.append(job)
        self.jobs = new_jobs
        self._save_jobs()

    def _remove_job(self, job_id: str) -> bool:
        if not job_id:
            return False
        before = len(self.jobs or [])
        self.jobs = [j for j in (self.jobs or []) if j.get("id") != job_id]
        changed = (len(self.jobs or []) != before)
        if changed:
            self._save_jobs()
        return changed

