# core/metrics/registry.py
from __future__ import annotations
from pathlib import Path
import json
import threading
from typing import Any, Dict

# プロジェクトルート .../fxbot
ROOT = Path(__file__).resolve().parents[2]

# ランタイム出力: .../fxbot/runtime/metrics.json
_RUNTIME_DIR = ROOT / "runtime"
_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
METRICS_JSON = str(_RUNTIME_DIR / "metrics.json")  # GUI側が open(METRICS_JSON) する前提

class _MetricsKV:
    """
    簡易KVS: GUI側がファイル読めなかったときのフォールバック。
    trade_service 等が同プロセス内で set()/update() を呼べば get() で返る。
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kv: Dict[str, Any] = {}

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._kv)

    def set(self, kv: Dict[str, Any]) -> None:
        with self._lock:
            self._kv.update(kv)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            self._kv.update(kwargs)

# 外部公開
METRICS = _MetricsKV()
