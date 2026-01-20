# app/services/scheduler_facade.py
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.job_scheduler import JobScheduler
from loguru import logger

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# シングルトン的に1インスタンスだけ使う
_scheduler: Optional[JobScheduler] = None


def _get_scheduler() -> JobScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = JobScheduler()
    return _scheduler


def get_scheduler() -> JobScheduler:
    """
    JobScheduler のシングルトンインスタンスを取得する（Public API）。
    GUI起動時の二重生成を防ぐために使用する。
    """
    scheduler = _get_scheduler()
    # 観測用：シングルトンが正しく動作しているか確認（初回のみログ出力）
    if not hasattr(get_scheduler, "_logged"):
        scheduler_id = id(scheduler)
        logger.info(f"[scheduler_facade] get_scheduler() returned scheduler_id={scheduler_id} (singleton check)")
        get_scheduler._logged = True
    return scheduler


def get_scheduler_snapshot() -> Dict[str, Any]:
    """
    GUI 用の Scheduler 状態スナップショット（readonly）

    Returns
    -------
    dict
        {
          "scheduler_level": int|None,
          "jobs": [
            {
              "id": str,
              "enabled": bool,
              "schedule": {"weekday":int|None,"hour":int|None,"minute":int|None},
              "state": str,
              "last_run_at": str|None,
              "last_result": dict|None,
            }
          ],
          "generated_at": str (ISO, UTC)
        }
    """
    sch = _get_scheduler()

    jobs_view: List[Dict[str, Any]] = []

    for job in sch.get_jobs():
        job_id = str(job.get("id") or job.get("name") or "?")
        st = sch.get_job_state(job_id) or {}

        # last_resultを補完（stdout/stderr/errorが無い場合）
        last_result = st.get("last_result")
        if last_result and isinstance(last_result, dict):
            # 足りないキーがあれば補完
            if "stdout" not in last_result:
                last_result["stdout"] = ""
            if "stderr" not in last_result:
                last_result["stderr"] = ""
            if "error" not in last_result:
                last_result["error"] = None

        jobs_view.append({
            "id": job_id,
            "enabled": bool(job.get("enabled", True)),
            "command": job.get("command", ""),  # 編集機能のために追加
            "scheduler_level": job.get("scheduler_level"),  # 編集機能のために追加
            "run_always": bool(job.get("run_always", False)),  # 常時実行判定用
            "schedule": {
                "weekday": job.get("weekday"),
                "hour": job.get("hour"),
                "minute": job.get("minute"),
            },
            "next_run_at": _calc_next_run_utc(job.get("weekday"), job.get("hour"), job.get("minute")),
            "state": st.get("state"),
            "last_run_at": st.get("last_run_at"),
            "last_result": last_result,
        })

    scheduler_level = _get_scheduler_level(sch)
    return {
        "scheduler_level": scheduler_level,  # 表示用
        "can_edit": bool((scheduler_level or 0) >= 3),
        "jobs": jobs_view,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _get_scheduler_level(sch: JobScheduler) -> int | None:
    """
    scheduler_level を安全に取得する（public API 優先、fallback で private 属性）。

    Parameters
    ----------
    sch : JobScheduler
        JobScheduler インスタンス

    Returns
    -------
    int | None
        scheduler_level（取得できない場合は None）
    """
    # public API があれば優先
    lvl = None
    try:
        if hasattr(sch, "get_scheduler_level"):
            lvl = sch.get_scheduler_level()  # type: ignore[attr-defined]
    except Exception:
        lvl = None
    # fallback（既存互換）
    if lvl is None:
        lvl = getattr(sch, "_scheduler_level_cfg", None)
    return lvl


def _calc_next_run_utc(weekday: Any, hour: Any, minute: Any) -> str | None:
    """weekday/hour/minute の単純スケジュールから次回実行(UTC ISO)を計算する。
    None が多い（常時/未設定）場合は None を返す。
    """
    # いずれも未設定なら next_run は出せない（GUI側で '-' 表示）
    if weekday is None and hour is None and minute is None:
        return None

    now = datetime.now(timezone.utc)

    # 目標時刻の候補を今から探す（最大 8日分探索）
    for add_days in range(0, 8):
        cand = now.replace(second=0, microsecond=0) + timedelta(days=add_days)

        if weekday is not None and cand.weekday() != int(weekday):
            continue

        # hour/minute が指定されていればそれに合わせる（未指定は現時刻を許容しない）
        if hour is not None:
            cand = cand.replace(hour=int(hour))
        else:
            continue

        if minute is not None:
            cand = cand.replace(minute=int(minute))
        else:
            continue

        # 未来（または今ちょうど）なら採用
        if cand >= now.replace(second=0, microsecond=0):
            return cand.isoformat()

    return None




def _validate_job_payload(job: dict) -> Tuple[bool, str]:
    """
    ジョブペイロードのバリデーション（services層での最終チェック）。

    Parameters
    ----------
    job : dict
        ジョブ定義

    Returns
    -------
    Tuple[bool, str]
        (is_valid, error_message)
        is_valid=True の場合は error_message は空文字列
    """
    job_id = (job.get("id") or "").strip()
    cmd = (job.get("command") or "").strip()

    if not job_id:
        return False, "id is required"
    if not _JOB_ID_RE.match(job_id):
        return False, "id must match ^[A-Za-z0-9_-]{1,64}$"
    if not cmd:
        return False, "command is required"

    weekday = job.get("weekday", None)
    hour = job.get("hour", None)
    minute = job.get("minute", None)

    if weekday is not None and (not isinstance(weekday, int) or weekday < 0 or weekday > 6):
        return False, "weekday must be None or 0..6"
    # hour: None を許容し、None の場合は range check をスキップ
    if hour is not None:
        if not isinstance(hour, int) or hour < 0 or hour > 23:
            return False, "hour must be None or 0..23"
    # minute: None を許容し、None の場合は range check をスキップ
    if minute is not None:
        if not isinstance(minute, int) or minute < 0 or minute > 59:
            return False, "minute must be None or 0..59"

    return True, ""


def add_scheduler_job(job: dict) -> dict:
    """Add/Update a scheduler job and persist to YAML (T-42-3-3)."""
    # use local singleton: _get_scheduler()
    snap = get_scheduler_snapshot()
    if not snap.get("can_edit"):
        return {"ok": False, "error": "scheduler is read-only (can_edit=false)"}

    # バリデーション（services層での最終チェック）
    ok, err = _validate_job_payload(job)
    if not ok:
        return {"ok": False, "error": err}

    sch = _get_scheduler()
    sch.add_job(job)
    # ★永続化（追加の直後）
    sch._save_jobs()
    return {"ok": True, "snapshot": get_scheduler_snapshot()}

def remove_scheduler_job(job_id: str) -> dict:
    """Remove a scheduler job and persist to YAML (T-42-3-3)."""
    # use local singleton: _get_scheduler()
    snap = get_scheduler_snapshot()
    if not snap.get("can_edit"):
        return {"ok": False, "error": "scheduler is read-only (can_edit=false)"}

    sch = _get_scheduler()
    changed = sch.remove_job(job_id)
    # ★永続化（削除の直後）
    if changed:
        sch._save_jobs()
    return {"ok": True, "removed": bool(changed), "snapshot": get_scheduler_snapshot()}


def run_scheduler_job_now(job_id: str) -> dict:
    """
    GUI用: 指定ジョブを「今すぐ」1回実行する。
    - GUI -> services のみ
    - scheduler singleton を facade 経由で叩く
    """
    sch = _get_scheduler()

    # jobs から対象を探す（jobs の実体は JobScheduler 側の保持データ）
    jobs = getattr(sch, "jobs", None) or []
    job = next((j for j in jobs if (j.get("id") == job_id)), None)
    if not job:
        return {"ok": False, "error": f"job not found: {job_id}"}

    if not job.get("enabled", True):
        return {"ok": False, "error": f"job disabled: {job_id}"}

    # 既存の実行関数に寄せる（最小差分）
    # _run_job(job, now) は now パラメータが必要
    now = datetime.now(timezone.utc)
    if hasattr(sch, "_run_job"):
        res = sch._run_job(job, now)  # noqa: SLF001 (internal call by design)
    else:
        # 最後の保険：既存APIに合わせる（ここは実プロジェクトの実装名に合わせて調整）
        return {"ok": False, "error": "scheduler has no _run_job()"}

    snap = get_scheduler_snapshot()
    return {"ok": True, "job_id": job_id, "result": res, "snapshot": snap}


# ============================================================================
# Daemon管理API（T-42-3-11）
# ============================================================================

_DAEMON_PID_FILE = Path("logs/scheduler_daemon.pid")


def start_scheduler_daemon(poll_sec: float = 1.0) -> dict:
    """
    スケジューラデーモンを起動する。
    """
    if _DAEMON_PID_FILE.exists():
        try:
            pid_data = json.loads(_DAEMON_PID_FILE.read_text(encoding="utf-8"))
            pid = pid_data.get("pid")
            if pid and _is_process_running(pid):
                return {
                    "ok": False,
                    "error": f"daemon already running (pid={pid})",
                    "pid": pid,
                    "pid_file": str(_DAEMON_PID_FILE),
                }
        except Exception:
            _DAEMON_PID_FILE.unlink(missing_ok=True)

    try:
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.services.scheduler_daemon",
                "--poll-sec",
                str(poll_sec),
            ],
            creationflags=creation_flags,
        )

        pid_data = {
            "pid": proc.pid,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "poll_sec": poll_sec,
        }
        _DAEMON_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DAEMON_PID_FILE.write_text(json.dumps(pid_data), encoding="utf-8")

        return {
            "ok": True,
            "error": None,
            "pid": proc.pid,
            "pid_file": str(_DAEMON_PID_FILE),
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "pid": None,
            "pid_file": None,
        }




def stop_scheduler_daemon(wait_sec: float = 0.5) -> dict:
    """
    スケジューラデーモンを停止する（標準ライブラリのみ）。

    Returns
    -------
    dict
        {
          "ok": bool,
          "error": str|None,
          "stopped": bool,
          "pid": int|None,
          "pid_file": str
        }
    """
    pid_file = str(_DAEMON_PID_FILE)

    if not _DAEMON_PID_FILE.exists():
        return {"ok": True, "error": None, "stopped": True, "pid": None, "pid_file": pid_file}

    try:
        pid_data = json.loads(_DAEMON_PID_FILE.read_text(encoding="utf-8"))
        pid = pid_data.get("pid")
        if not pid:
            _DAEMON_PID_FILE.unlink(missing_ok=True)
            return {"ok": True, "error": None, "stopped": True, "pid": None, "pid_file": pid_file}

        if not _is_process_running(int(pid)):
            _DAEMON_PID_FILE.unlink(missing_ok=True)
            return {"ok": True, "error": None, "stopped": True, "pid": int(pid), "pid_file": pid_file}

        pid = int(pid)

        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            import os, signal
            os.kill(pid, signal.SIGTERM)

        import time
        time.sleep(max(0.0, float(wait_sec)))

        if _is_process_running(pid):
            return {"ok": False, "error": f"process {pid} still running", "stopped": False, "pid": pid, "pid_file": pid_file}

        _DAEMON_PID_FILE.unlink(missing_ok=True)
        return {"ok": True, "error": None, "stopped": True, "pid": pid, "pid_file": pid_file}

    except Exception as e:
        return {"ok": False, "error": f"failed to stop daemon: {e}", "stopped": False, "pid": None, "pid_file": pid_file}

def get_scheduler_daemon_status() -> dict:
    """
    スケジューラデーモンの状態を取得する。

    Returns
    -------
    dict
        {
            "running": bool,
            "pid": int | None,
            "started_at": str | None,
            "poll_sec": float | None,
            "pid_file": str | None,
        }
    """
    if not _DAEMON_PID_FILE.exists():
        return {
            "running": False,
            "pid": None,
            "started_at": None,
            "poll_sec": None,
            "pid_file": str(_DAEMON_PID_FILE),
        }

    try:
        pid_data = json.loads(_DAEMON_PID_FILE.read_text(encoding="utf-8"))
        pid = pid_data.get("pid")
        started_at = pid_data.get("started_at")
        poll_sec = pid_data.get("poll_sec")

        if pid and _is_process_running(pid):
            return {
                "running": True,
                "pid": pid,
                "started_at": started_at,
                "poll_sec": poll_sec,
                "pid_file": str(_DAEMON_PID_FILE),
            }
        else:
            # PIDファイルはあるがプロセスが存在しない（ゾンビPIDファイル）
            return {
                "running": False,
                "pid": pid,
                "started_at": started_at,
                "poll_sec": poll_sec,
                "pid_file": str(_DAEMON_PID_FILE),
            }

    except Exception as e:
        return {
            "running": False,
            "pid": None,
            "started_at": None,
            "poll_sec": None,
            "pid_file": str(_DAEMON_PID_FILE),
            "error": str(e),
        }


def _is_process_running(pid: int) -> bool:
    """
    プロセスが実行中かどうかをチェック（標準ライブラリのみ使用）
    """
    if sys.platform == "win32":
        try:
            import locale

            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                check=False,
            )
            enc = locale.getpreferredencoding(False) or "mbcs"
            out = (result.stdout or b"").decode(enc, errors="replace")
            return any(str(pid) in line for line in out.splitlines())
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def open_scheduler_daemon_log() -> Dict[str, Any]:
    """
    Open logs/scheduler_daemon.log with the default application (OS-dependent).
    GUI must call only this facade API (path resolving + OS-specific open live here).
    """
    try:
        # project root 推定は、このファイルの位置から固定でOK（services内に閉じる）
        # app/services/scheduler_facade.py -> project_root = parents[2]
        root = Path(__file__).resolve().parents[2]
        log_path = (root / "logs" / "scheduler_daemon.log").resolve()

        # 無ければ作る（"開く"要求でファイルが存在しないと既定アプリが失敗しやすい）
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not log_path.exists():
            log_path.write_text("", encoding="utf-8")

        # OSごとに既定アプリで開く
        if sys.platform.startswith("win"):
            os.startfile(str(log_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(log_path)], check=False)
        else:
            subprocess.run(["xdg-open", str(log_path)], check=False)

        return {"ok": True, "path": str(log_path), "error": None}
    except Exception as e:
        return {"ok": False, "path": None, "error": f"{type(e).__name__}: {e}"}

