from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def is_live() -> bool:
    """
    Returns True when the environment variable FXBOT_RUNTIME indicates live mode.
    """
    return os.environ.get("FXBOT_RUNTIME", "").strip().lower() in {"live", "prod", "production"}
