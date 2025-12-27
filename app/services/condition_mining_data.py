from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.services import decision_log


def _parse_iso_dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _iter_decision_paths() -> Iterable[Path]:
    root = decision_log._get_decision_log_dir()
    if not root.exists():
        return []
    return sorted(root.glob("decisions_*.jsonl"))


def resolve_window(
    window: str | None,
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


def get_decisions_window_summary(
    symbol: str,
    window: str | None = None,
    profile: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_scan: int = 200_000,
) -> Dict[str, Any]:
    dt_start = _parse_iso_dt(start) if start else None
    dt_end = _parse_iso_dt(end) if end else None

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

            if profile is not None:
                p = j.get("profile")
                if p is not None and p != profile:
                    continue

            ts = _parse_iso_dt(j.get("ts_jst")) or _parse_iso_dt(j.get("timestamp"))
            if ts is None:
                fts = j.get("filters") if isinstance(j.get("filters"), dict) else None
                ts = _parse_iso_dt((fts or {}).get("timestamp"))
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

def get_decisions_recent_past_summary(symbol: str) -> dict:
    """Aggregate recent/past windows and attach minimal stats.

    Returns:
      {
        "recent": <window_summary_dict>,
        "past": <window_summary_dict>,
      }
    """
    recent = get_decisions_window_summary(
        symbol=symbol,
        window="recent",
    )
    past = get_decisions_window_summary(
        symbol=symbol,
        window="past",
    )

    # decisions/rows のキー名は実装依存なので両対応
    r_rows = (recent.get("decisions") or recent.get("rows") or [])
    p_rows = (past.get("decisions") or past.get("rows") or [])

    recent["min_stats"] = _min_stats(r_rows)
    past["min_stats"] = _min_stats(p_rows)

    return {"recent": recent, "past": past}

# --- T-42-3-18 Step 3: minimal window stats (recent/past) -----------------

def _min_stats(rows):
    """Compute minimal aggregate stats for a list of decision-like dicts.

    Returns:
      {
        total: int,
        filter_pass_count: int,
        filter_pass_rate: float,
        entry_count: int,
        entry_rate: float,
      }
    """
    if not rows:
        return {
            "total": 0,
            "filter_pass_count": 0,
            "filter_pass_rate": 0.0,
            "entry_count": 0,
            "entry_rate": 0.0,
        }

    total = 0
    pass_cnt = 0
    entry_cnt = 0

    for r in rows:
        if not isinstance(r, dict):
            continue
        total += 1
        if bool(r.get("filter_pass", False)):
            pass_cnt += 1
        if str(r.get("action", "")).upper() == "ENTRY":
            entry_cnt += 1

    # Avoid ZeroDivision
    denom = total if total > 0 else 1
    return {
        "total": int(total),
        "filter_pass_count": int(pass_cnt),
        "filter_pass_rate": float(pass_cnt) / float(denom),
        "entry_count": int(entry_cnt),
        "entry_rate": float(entry_cnt) / float(denom),
    }










