# app/services/condition_mining_data.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from app.services import decision_log
def resolve_window(
    window: str | None,
    *,
    now: datetime | None = None,
    recent_minutes: int = 30,
    past_minutes: int = 30,
    past_offset_minutes: int = 24 * 60,
) -> tuple[datetime | None, datetime | None]:
    # recent: [now-recent_minutes, now]
    # past  : [now-past_offset_minutes-past_minutes, now-past_offset_minutes]
    if not window:
        return (None, None)

    w = str(window).strip().lower()
    now_dt = now or datetime.now(timezone.utc)

    if w == "recent":
        end = now_dt
        start = end - timedelta(minutes=int(recent_minutes))
        return (start, end)

    if w == "past":
        end = now_dt - timedelta(minutes=int(past_offset_minutes))
        start = end - timedelta(minutes=int(past_minutes))
        return (start, end)

    return (None, None)

w = str(window).strip().lower()
    now_dt = now or datetime.now(timezone.utc)

    if w == "recent":
        end = now_dt
        start = end - timedelta(minutes=int(recent_minutes))
        return (start, end)

    if w == "past":
        end = now_dt - timedelta(minutes=int(past_offset_minutes))
        start = end - timedelta(minutes=int(past_minutes))
        return (start, end)

    return (None, None)



def _parse_iso_dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    if not isinstance(s, str):
        return None
    try:
        # "2025-12-20T12:37:10+09:00" などを想定
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _iter_decision_paths() -> Iterable[Path]:
    # decision_log 内の「決定ログのルート」を使う（既存優先）
    root = decision_log._get_decision_log_dir()  # existing helper
    if not root.exists():
        return []
    # decisions_*.jsonl を全部対象（Live/Backtest混在でもまずはOK）
    return sorted(root.glob("decisions_*.jsonl"))


def get_decisions_window_summary(
    *,
    symbol: str,
    profile: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_scan: int = 200_000,
) -> Dict[str, Any]:
    """
    Read-only summary for condition mining.
    Returns: {symbol, profile, start_ts, end_ts, n, sources[]}
    - start/end: ISO string (inclusive start, inclusive end) or None
    """
    dt_start = _parse_iso_dt(start) if start else None
    dt_end = _parse_iso_dt(end) if end else None

    # If explicit start/end not provided, resolve by named window.
    if dt_start is None and dt_end is None and window:
        ws, we = resolve_window(window)
        dt_start, dt_end = ws, we

    n = 0
    min_dt: Optional[datetime] = None
    max_dt: Optional[datetime] = None
    scanned = 0
    sources: list[str] = []

    for path in _iter_decision_paths():
        sources.append(str(path))
        for j in decision_log._iter_jsonl(path):
            scanned += 1
            if scanned > max_scan:
                break

            if j.get("symbol") != symbol:
                continue

            # profile はログに無い場合もあるので「あれば絞る」
            if profile is not None:
                p = j.get("profile")
                if p is not None and p != profile:
                    continue

            ts = _parse_iso_dt(j.get("ts_jst")) or _parse_iso_dt(j.get("timestamp"))
            if ts is None:
                continue

            if dt_start and ts < dt_start:
                continue
            if dt_end and ts > dt_end:
                continue

            n += 1
            if min_dt is None or ts < min_dt:
                min_dt = ts
            if max_dt is None or ts > max_dt:
                max_dt = ts

        if scanned > max_scan:
            break

    return {
        "symbol": symbol,
        "profile": profile,
        "n": n,
        "start_ts": min_dt.isoformat() if min_dt else None,
        "end_ts": max_dt.isoformat() if max_dt else None,
        "sources": sources,
        "scanned": scanned,
        "max_scan": max_scan,
    }


