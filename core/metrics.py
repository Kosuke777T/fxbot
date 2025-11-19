# core/metrics.py
from __future__ import annotations
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict
import time, os, json, tempfile

RUNTIME_DIR = os.path.join(os.getcwd(), "runtime")
METRICS_JSON = os.path.join(RUNTIME_DIR, "metrics.json")

def _atomic_write_json(path: str, obj: dict):
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
