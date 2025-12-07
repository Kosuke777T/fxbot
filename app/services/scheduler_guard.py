# app/services/scheduler_guard.py

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from loguru import logger

from app.services import edition_guard


# Edition の「強さ順」定義（小文字も対応）
_EDITION_ORDER = {
    "FREE": 0,
    "BASIC": 1,
    "PRO": 2,
    "EXPERT": 3,
    "MASTER": 4,
    "free": 0,
    "basic": 1,
    "pro": 2,
    "expert": 3,
    "master": 4,
}


def _edition_rank(name: Optional[str]) -> int:
    """
    Edition 名を「強さ順」の整数に変換する。

    未指定や未知の名前は 0 (FREE 相当) として扱う。
    """
    if not name:
        return 0
    return _EDITION_ORDER.get(str(name).upper(), 0)


def filter_jobs_for_current_edition(
    jobs: Iterable[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    EditionGuard の設定に基づき、現在のエディションで実行可能な
    ジョブだけを返すユーティリティ。

    想定する job dict 例:
        {
            "id": "weekly_retrain",
            "enabled": True,
            "edition_min": "PRO",  # 省略可
            ...
        }

    振る舞い:
    - edition_min が現在のエディションより上位なら、そのジョブは除外。
    - guard.scheduler_jobs_max が None でなければ、
      edition 条件を満たしたジョブの先頭から max 件だけを残す。
    """
    current_edition = edition_guard.current_edition()
    max_jobs = edition_guard.scheduler_limit()

    # 安全のため list に確定してから処理
    all_jobs: List[Dict[str, Any]] = list(jobs)

    allowed_by_edition: List[Dict[str, Any]] = []
    skipped_by_edition: List[str] = []

    for job in all_jobs:
        edition_min = job.get("edition_min")
        job_id = str(job.get("id") or job.get("name") or "?")

        # edition_min が指定されていて「要求Edition > 現在Edition」なら除外
        if edition_min:
            required_rank = _edition_rank(edition_min)
            current_rank = _edition_rank(current_edition)
            if required_rank > current_rank:
                skipped_by_edition.append(job_id)
                continue

        allowed_by_edition.append(job)

    # edition_min による除外ログ
    if skipped_by_edition:
        logger.info(
            "[SchedulerGuard] edition='{}' edition_min 制約で {} 個のジョブを除外: {}",
            current_edition,
            len(skipped_by_edition),
            skipped_by_edition,
        )

    # ジョブ数上限の適用
    limited_jobs: List[Dict[str, Any]] = allowed_by_edition
    if max_jobs < 0:
        logger.warning(
            "[SchedulerGuard] scheduler_limit が負数({}) なので無視します",
            max_jobs,
        )
    elif max_jobs > 0:
        limited_jobs = allowed_by_edition[:max_jobs]
        if len(allowed_by_edition) > len(limited_jobs):
            skipped_ids = [
                str(job.get("id") or job.get("name") or "?")
                for job in allowed_by_edition[max_jobs:]
            ]
            logger.info(
                "[SchedulerGuard] edition='{}' scheduler_limit={} により "
                "{} 個のジョブを除外: {}",
                current_edition,
                max_jobs,
                len(allowed_by_edition) - len(limited_jobs),
                skipped_ids,
            )

    logger.info(
        "[SchedulerGuard] edition='{}' 有効ジョブ数 / 全ジョブ数: {}/{} (max={})",
        current_edition,
        len(limited_jobs),
        len(all_jobs),
        max_jobs,
    )

    return limited_jobs
