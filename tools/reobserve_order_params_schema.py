from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _iter_jsonl(path: Path) -> Iterable[Tuple[int, Dict[str, Any]]]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception as e:
                raise ValueError(f"json decode failed: line={i} err={e}") from e
            if isinstance(obj, dict):
                yield i, obj


def _find_latest_decisions_log(logs_dir: Path) -> Optional[Path]:
    candidates = sorted(logs_dir.glob("decisions_*.jsonl"))
    if not candidates:
        return None
    # latest by mtime, tie-break by name
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return candidates[-1]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Re-observe order_params schema (read-only).")
    ap.add_argument("--path", type=str, default="", help="decisions_*.jsonl path (optional).")
    ap.add_argument("--tail", type=int, default=5, help="show last N order_params samples.")
    args = ap.parse_args(argv)

    logs_dir = Path("logs")
    fail_reasons: List[str] = []

    if args.path:
        log_path = Path(args.path)
        if not log_path.exists():
            fail_reasons.append(f"path not found: {log_path}")
            log_path = None  # type: ignore[assignment]
    else:
        log_path = _find_latest_decisions_log(logs_dir)
        if log_path is None:
            fail_reasons.append("no logs/decisions_*.jsonl found")

    if fail_reasons:
        print("[NG] re-observe failed (precheck)")
        for r in fail_reasons:
            print(f"- {r}")
        return 2

    assert log_path is not None
    print(f"[obs] decisions_log: {log_path.as_posix()}")

    # (line_no, timestamp, source, order_params)
    order_rows: List[Tuple[int, Any, str, Dict[str, Any]]] = []
    try:
        for line_no, obj in _iter_jsonl(log_path):
            op = obj.get("order_params")
            src = "order_params"
            if not isinstance(op, dict):
                dd = obj.get("decision_detail")
                if isinstance(dd, dict) and isinstance(dd.get("order_params"), dict):
                    op = dd.get("order_params")
                    src = "decision_detail.order_params"
            if isinstance(op, dict):
                order_rows.append((line_no, obj.get("timestamp"), src, op))
    except Exception as e:
        print("[NG] re-observe failed (read/decode)")
        print(f"- {e}")
        return 2

    if len(order_rows) == 0:
        print("[NG] no rows with order_params found")
        print("- condition_not_met: order_params_rows >= 1")
        return 2

    keys_union = set()
    missing_schema: List[str] = []
    for line_no, _ts, src, op in order_rows:
        keys_union.update(op.keys())
        if "schema_version" not in op:
            missing_schema.append(f"line={line_no} src={src}")

    keys_sorted = sorted(keys_union)
    print(f"[obs] order_params.rows: {len(order_rows)}")
    print(f"[obs] order_params.keys_union({len(keys_sorted)}): {keys_sorted}")

    if missing_schema:
        print("[NG] schema_version missing in some order_params rows")
        print("- condition_not_met: all order_params have 'schema_version'")
        print(f"- missing_schema_version_rows: {missing_schema[:50]}" + (" ..." if len(missing_schema) > 50 else ""))
        # still show tail for debugging
        tail_n = max(0, int(args.tail))
    else:
        print("[OK] schema_version present in all observed order_params rows")
        tail_n = max(0, int(args.tail))

    if tail_n > 0:
        print(f"[obs] tail_samples: last {tail_n} order_params rows")
        for line_no, ts, src, op in order_rows[-tail_n:]:
            print(f"--- line={line_no} timestamp={ts} src={src} ---")
            try:
                print(json.dumps(op, ensure_ascii=False, sort_keys=True))
            except Exception:
                print(str(op))

    return 0 if not missing_schema else 2


if __name__ == "__main__":
    raise SystemExit(main())


