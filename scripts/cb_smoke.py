from __future__ import annotations

from typing import Any, Optional

import importlib


def main() -> None:
    cb_mod = importlib.import_module("app.services.circuit_breaker")
    scan_and_update = getattr(cb_mod, "scan_and_update", None)
    status = getattr(cb_mod, "status", None)

    if callable(scan_and_update):
        scan_and_update()
    if callable(status):
        s: Optional[dict[str, Any]] = status()
        print(s)
    else:
        print({"circuit": "unknown"})


if __name__ == "__main__":
    main()
