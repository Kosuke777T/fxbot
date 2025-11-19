from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict

from app.core.config_loader import load_config


@lru_cache(maxsize=1)
def _load() -> Dict[str, Any]:
    return load_config()


cfg: Dict[str, Any] = _load()


def reload() -> Dict[str, Any]:
    """
    Reload configuration from disk and update cached reference.
    """
    global cfg
    cfg = load_config()
    _load.cache_clear()
    return cfg
