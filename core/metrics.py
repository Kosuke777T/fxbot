# core/metrics.py
from __future__ import annotations
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict
import time, os, json, tempfile
import traceback

RUNTIME_DIR = os.path.join(os.getcwd(), "runtime")
METRICS_JSON = os.path.join(RUNTIME_DIR, "metrics.json")

def _metrics_enabled() -> bool:
    """
    metrics の書き込みが有効かどうかを判定する（環境変数で制御）。

    Returns
    -------
    bool
        True のとき metrics を書き込む
    """
    v = os.getenv("FXBOT_NO_METRICS", "").strip().lower()
    return v not in ("1", "true", "on", "yes")

def _atomic_write_json(path: str, obj: dict):
    if os.getenv("FXBOT_METRICS_TRACE", "").strip().lower() in ("1", "true", "on", "yes"):
        print("[METRICS_TRACE][core] _atomic_write_json called:",
              "path=", path,
              "FXBOT_NO_METRICS=", os.getenv("FXBOT_NO_METRICS"))
        traceback.print_stack(limit=18)

    # metrics が無効な場合はスキップ
    if not _metrics_enabled():
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="metrics_", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass

@dataclass
class _MetricsStore:
    _lock: Lock = field(default_factory=Lock)
    _kv: Dict[str, Any] = field(default_factory=dict)

    def set(self, **kwargs):
        with self._lock:
            self._kv.update(kwargs)
            self._kv["ts"] = time.time()
            _atomic_write_json(METRICS_JSON, self._kv)

    def inc(self, key: str, by: int = 1):
        with self._lock:
            self._kv[key] = int(self._kv.get(key, 0)) + by
            self._kv["ts"] = time.time()
            _atomic_write_json(METRICS_JSON, self._kv)

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._kv)

METRICS = _MetricsStore()
