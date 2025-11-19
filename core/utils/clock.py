from __future__ import annotations

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), name="Asia/Tokyo")


def now_jst() -> datetime:
    """Return current datetime in JST (timezone-aware)."""
    return datetime.now(JST)
