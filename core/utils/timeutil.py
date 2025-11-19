# core/utils/timeutil.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), name="Asia/Tokyo")


def now_jst_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")
