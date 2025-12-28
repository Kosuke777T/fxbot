# Auto-generated minimal compatibility shim.
# Purpose: provide app.core.config for scripts/tools that migrated from core.config
# Policy: no new behavior, just re-export existing API.

from core.config import cfg, load_config

__all__ = ['cfg', 'load_config']
