# app/services/scheduler_facade.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.job_scheduler import JobScheduler


# シングルトン的に1インスタンスだけ使う
_scheduler: Optional[JobScheduler] = None


def _get_scheduler() -> JobScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = JobScheduler()
    return _scheduler


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

        jobs_view.append({
            "id": job_id,
            "enabled": bool(job.get("enabled", True)),
            "schedule": {
                "weekday": job.get("weekday"),
                "hour": job.get("hour"),
                "minute": job.get("minute"),
            },
            "next_run_at": _calc_next_run_utc(job.get("weekday"), job.get("hour"), job.get("minute")),
            "state": st.get("state"),
            "last_run_at": st.get("last_run_at"),
            "last_result": st.get("last_result"),
        })

    return {
        "scheduler_level": sch._scheduler_level_cfg,  # 表示専用（編集不可）
        "can_edit": bool((sch._scheduler_level_cfg or 0) >= 3),
        "jobs": jobs_view,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


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




def add_scheduler_job(job: dict) -> dict:
    """Add/Update a scheduler job and persist to YAML (T-42-3-3)."""
    # use local singleton: _get_scheduler()
    snap = get_scheduler_snapshot()
    if not snap.get("can_edit"):
        return {"ok": False, "error": "scheduler is read-only (can_edit=false)"}

    sch = _get_scheduler()
    sch._add_job(job)
    return {"ok": True, "snapshot": get_scheduler_snapshot()}

def remove_scheduler_job(job_id: str) -> dict:
    """Remove a scheduler job and persist to YAML (T-42-3-3)."""
    # use local singleton: _get_scheduler()
    snap = get_scheduler_snapshot()
    if not snap.get("can_edit"):
        return {"ok": False, "error": "scheduler is read-only (can_edit=false)"}

    sch = _get_scheduler()
    changed = sch._remove_job(job_id)
    return {"ok": True, "removed": bool(changed), "snapshot": get_scheduler_snapshot()}



