from __future__ import annotations

import json
import os
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Deque, List, Optional
from pathlib import Path

# プロジェクトルート = app/services/ から 2 つ上
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_LOG_DIR = _PROJECT_ROOT / "logs"
_LOG_FILE = _LOG_DIR / "ui_events.jsonl"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


@dataclass
class UiEvent:
    ts: str
    kind: str
    symbol: str
    side: Optional[str] = None
    price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    profit_jpy: Optional[float] = None
    # T-44-3: Exit as Decision (label-only / add-only)
    # - Recorded on CLOSE events in live execution when available
    # - Must not affect trade logic
    exit_type: Optional[str] = None  # "DEFENSE" | "PROFIT" | None
    exit_reason: Optional[str] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    corr_id: Optional[str] = None  # 相関ID（イベントログで追跡用）
    source_record_id: Optional[str] = None  # どの履歴レコードから再実行されたか


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class _EventStore:
    def __init__(self, maxlen: int = 1000):
        self._buf: Deque[UiEvent] = deque(maxlen=maxlen)

    def append(self, ev: UiEvent) -> None:
        with _lock:
            self._buf.appendleft(ev)
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")

    def add(self, **kwargs: Any) -> None:
        kwargs.setdefault("ts", _now())
        self.append(UiEvent(**kwargs))

    def recent(self, n: int = 200) -> List[UiEvent]:
        with _lock:
            return list(list(self._buf)[:n])


EVENT_STORE = _EventStore()
