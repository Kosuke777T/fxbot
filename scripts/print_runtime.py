from __future__ import annotations

from core.config import cfg
from core.utils.runtime import is_live

print("is_live:", is_live())
print("filters:", cfg.get("filters"))
print("entry:", cfg.get("entry"))
print("session:", cfg.get("session"))
