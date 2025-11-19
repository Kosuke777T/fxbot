from pathlib import Path
from typing import Any, Dict

import yaml


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            a[k] = _deep_merge(a[k], v)
        else:
            a[k] = v
    return a


def load_config() -> Dict[str, Any]:
    base: Dict[str, Any] = {}
    base_path = Path("configs/config.yaml")
    if base_path.exists():
        base = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
    local_path = Path("configs/config.local.yaml")
    if local_path.exists():
        local: Dict[str, Any] = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        base = _deep_merge(base, local)
    return base
