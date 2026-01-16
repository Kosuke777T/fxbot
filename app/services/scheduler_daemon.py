# app/services/scheduler_daemon.py
"""
スケジューラ常駐デーモン（T-42-3-11）

GUIを閉じてもスケジュールが回るように、別プロセスで常駐実行する。
"""
from __future__ import annotations

import sys
import time
import json
from pathlib import Path
from typing import Any

from loguru import logger

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.job_scheduler import JobScheduler
from app.services.execution_service import ExecutionService
from app.services import trade_state

_RUNTIME_FLAGS_PATH = Path("config/runtime_flags.json")


def _sync_runtime_flags() -> None:
    """
    GUIが書いた config/runtime_flags.json を読み取り、trade_state を更新する。
    - gate は execute_exit 内部に委譲する（呼び出し側では判断しない）
    - 破損/未存在は黙って無視（観測を壊さない）
    """
    if not _RUNTIME_FLAGS_PATH.exists():
        return
    try:
        data = json.loads(_RUNTIME_FLAGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    try:
        if isinstance(data, dict) and ("trading_enabled" in data):
            trade_state.update(trading_enabled=bool(data.get("trading_enabled")))
    except Exception:
        return


def main(poll_sec: float = 1.0) -> int:
    """
    常駐ランナーのメインループ。

    Parameters
    ----------
    poll_sec : float
        ポーリング間隔（秒）。デフォルトは1.0秒。

    Returns
    -------
    int
        終了コード（0=正常終了、非0=エラー）
    """
    # file log for daemon (T-42-3-11)
    Path("logs").mkdir(parents=True, exist_ok=True)
    logger.add("logs/scheduler_daemon.log", rotation="1 week", retention="4 weeks", encoding="utf-8")

    logger.info("[SchedulerDaemon] Starting daemon (poll_sec={})", poll_sec)

    try:
        # JobScheduler を初期化（configs/scheduler.yaml を使用）
        scheduler = JobScheduler()
        logger.info("[SchedulerDaemon] JobScheduler initialized (jobs={})", len(scheduler.get_jobs()))
        exec_service = ExecutionService()

        # メインループ
        while True:
            try:
                # run_pending() を実行
                results = scheduler.run_pending()
                if results:
                    logger.debug("[SchedulerDaemon] run_pending() executed {} jobs", len(results))
                    for r in results:
                        job_id = r.get("job_id", "?")
                        res = r.get("result") or {}
                        ok = bool(res.get("ok", False))
                        rc = res.get("rc")
                        err = res.get("error")
                        logger.info("[SchedulerDaemon] job '{}' result: ok={} rc={} error={}", job_id, ok, rc, err)

                # T-45-2: 自動EXITを定期実行に接続（gateは execute_exit 内部に委譲）
                _sync_runtime_flags()
                logger.debug("[SchedulerDaemon] tick: calling execute_exit(symbol=None, dry_run=False)")
                ret = exec_service.execute_exit(symbol=None, dry_run=False)
                logger.debug("[SchedulerDaemon] tick: execute_exit done ret={}", ret)

            except KeyboardInterrupt:
                logger.info("[SchedulerDaemon] Received KeyboardInterrupt, shutting down")
                return 0
            except Exception as e:
                # 例外は握ってログに出し、プロセスは落とさない
                logger.exception("[SchedulerDaemon] Error in run_pending(): {}", e)
                # 致命的でない限り続行

            # ポーリング間隔だけ待機
            time.sleep(poll_sec)

    except Exception as e:
        # 初期化エラーなど致命的なエラー
        logger.exception("[SchedulerDaemon] Fatal error: {}", e)
        return 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scheduler Daemon")
    parser.add_argument(
        "--poll-sec",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    args = parser.parse_args()

    exit_code = main(poll_sec=args.poll_sec)
    sys.exit(exit_code)

