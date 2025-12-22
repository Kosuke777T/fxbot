# app/services/scheduler_guard.py

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from loguru import logger

from app.services import edition_guard


# Edition の「強さ順」定義（大文字のみ、_edition_rank で upper() するため）
_EDITION_ORDER = {
    "FREE": 0,
    "BASIC": 1,
    "PRO": 2,
    "EXPERT": 3,
    "MASTER": 4,
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


def get_effective_scheduler_level(config_level: int | None) -> int:
    """
    設定ファイルの scheduler_level と EditionGuard の scheduler_level を統合して、
    実効的な scheduler_level を返す。

    Parameters
    ----------
    config_level : int | None
        configs/scheduler.yaml の scheduler_level（-1 は未設定を意味）

    Returns
    -------
    int
        実効的な scheduler_level（0-3、範囲外の値はクランプされる）
    """
    # EditionGuard から現在の scheduler_level を取得
    edition_level = edition_guard.get_capability("scheduler_level")
    if edition_level is None:
        edition_level = 0

    # edition_level も 0..3 の範囲にクランプ（安全ガード）
    edition_level = max(0, min(3, edition_level))

    # config_level が指定されていれば、それと edition_level の小さい方を採用
    if config_level is not None and config_level >= 0:
        # config_level も先にクランプしてから min() を使う（仕様がコードに刻まれる）
        config_level_clamped = max(0, min(3, config_level))
        effective = min(config_level_clamped, edition_level)
    else:
        # config_level が未設定なら edition_level をそのまま使用
        effective = edition_level

    # 0..3 の範囲にクランプ（念のため、二重チェック）
    return max(0, min(3, effective))


def allow_job_by_scheduler_level(job: Dict[str, Any], config_level: int | None) -> tuple[bool, str]:
    """
    ジョブの scheduler_level 要求と実効レベルを比較して、実行可否を判定する。

    Parameters
    ----------
    job : dict
        ジョブ定義（scheduler_level キーを含む可能性がある）
    config_level : int | None
        configs/scheduler.yaml の scheduler_level（-1 は未設定を意味）

    Returns
    -------
    tuple[bool, str]
        (実行可能か, 理由文字列)
    """
    effective_level = get_effective_scheduler_level(config_level)

    # effective_level が 0 なら常に False（表示のみ）
    if effective_level == 0:
        return False, "scheduler_level=0 (表示のみ)"

    # ジョブの要求レベルを取得（デフォルト 0、int化失敗時は即拒否）
    try:
        required_level = int(job.get("scheduler_level", 0))
    except (ValueError, TypeError):
        logger.warning(
            "[SchedulerGuard] invalid scheduler_level in job {}, deny execution",
            job.get("id"),
        )
        return False, "invalid scheduler_level"

    # 0..3 の範囲にクランプ（yaml誤設定で 999 などが入った場合の安全ガード）
    required_level = max(0, min(3, required_level))

    # required_level <= effective_level のとき True
    if required_level > effective_level:
        return False, f"required_level({required_level}) > effective_level({effective_level})"

    # effective_level==2 のときは「1ジョブのみ」制限
    # これは JobScheduler 側で「実行対象ジョブが複数ある状態」を抑止する必要がある
    # ただし、ここでは単一ジョブの判定のみを行う（複数ジョブの制限は JobScheduler 側で実装）

    return True, f"allowed (required={required_level}, effective={effective_level})"
