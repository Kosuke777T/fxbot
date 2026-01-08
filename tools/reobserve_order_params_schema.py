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
    ap.add_argument(
        "--scope",
        type=str,
        choices=["all", "tail"],
        default="all",
        help="judgement scope: 'all' (default) checks all rows; 'tail' checks only last N rows (N=--tail).",
    )
    ap.add_argument(
        "--boundary-filtered",
        action="store_true",
        help=(
            "If set, PASS/FAIL judgement excludes nested sources (e.g. decision_detail.order_params) "
            "and evaluates only top-level src=='order_params' rows. Display stats remain mixed-source."
        ),
    )
    ap.add_argument(
        "--allow-empty-gate",
        action="store_true",
        help=(
            "If set with --boundary-filtered, gate_rows==0 is treated as OK (skipped) with a WARN line. "
            "Default: gate_rows must be >=1."
        ),
    )
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
    rows_all: List[Tuple[int, Any, str, Dict[str, Any]]] = []
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
                rows_all.append((line_no, obj.get("timestamp"), src, op))
    except Exception as e:
        print("[NG] re-observe failed (read/decode)")
        print(f"- {e}")
        return 2

    if len(rows_all) == 0:
        print("[NG] no rows with order_params found")
        print("- condition_not_met: order_params_rows >= 1")
        return 2

    tail_n = max(0, int(args.tail))
    if args.scope == "tail":
        rows_scope = rows_all[-tail_n:] if tail_n > 0 else []
    else:
        rows_scope = rows_all

    # ---- all rows stats (always shown; not used for scope judgement unless scope=all) ----
    keys_union = set()
    missing_schema_all: List[str] = []
    for line_no, _ts, src, op in rows_all:
        keys_union.update(op.keys())
        if "schema_version" not in op:
            missing_schema_all.append(f"line={line_no} src={src}")

    # ---- scope rows judgement ----
    # Display-only scope stats (mixed-source as-is)
    missing_schema_scope: List[str] = []
    for line_no, _ts, src, op in rows_scope:
        if "schema_version" not in op:
            missing_schema_scope.append(f"line={line_no} src={src}")

    rows_gate = rows_scope
    if args.boundary_filtered:
        # Gate judgement only on top-level order_params (exclude decision_detail.order_params, etc.)
        rows_gate = [r for r in rows_scope if isinstance(r, tuple) and len(r) >= 3 and r[2] == "order_params"]

    missing_schema_judge: List[str] = []
    for line_no, _ts, src, op in rows_gate:
        if "schema_version" not in op:
            missing_schema_judge.append(f"line={line_no} src={src}")

    keys_sorted = sorted(keys_union)
    print(f"[obs] order_params.rows: {len(rows_all)}")
    print(f"[obs] order_params.keys_union({len(keys_sorted)}): {keys_sorted}")

    # stats display (do not mix scope judgement and all-stats)
    all_rows_n = len(rows_all)
    all_missing_n = len(missing_schema_all)
    all_ratio = (all_missing_n / all_rows_n) if all_rows_n > 0 else 0.0
    scope_rows_n = len(rows_scope)
    scope_missing_n = len(missing_schema_scope)
    scope_ratio = (scope_missing_n / scope_rows_n) if scope_rows_n > 0 else 0.0
    # judgement uses rows_gate / missing_schema_judge (optionally boundary-filtered)
    judge_rows_n = len(rows_gate)
    judge_missing_n = len(missing_schema_judge)
    judge_ratio = (judge_missing_n / judge_rows_n) if judge_rows_n > 0 else 0.0
    print(
        f"[scope] scope={args.scope} tail_n={tail_n} scope_rows={scope_rows_n} "
        f"missing_schema_version={scope_missing_n} ratio={scope_ratio:.3f}"
    )
    if args.boundary_filtered:
        print(
            f"[gate] boundary_filtered=1 gate_rows={judge_rows_n} "
            f"missing_schema_version={judge_missing_n} ratio={judge_ratio:.3f}"
        )
        if args.allow_empty_gate and judge_rows_n <= 0:
            print("[WARN] boundary-filtered gate_rows==0 -> skipped (allow-empty-gate=1)")
    print(
        f"[all] all_rows={all_rows_n} missing_schema_version={all_missing_n} ratio={all_ratio:.3f}"
    )

    unmet: List[str] = []
    if judge_rows_n <= 0:
        if not (args.boundary_filtered and args.allow_empty_gate):
            unmet.append("order_params_rows_in_scope >= 1")
    if judge_missing_n > 0:
        unmet.append("all order_params in scope have 'schema_version'")

    if unmet:
        print("[NG] scope check failed")
        for c in unmet:
            print(f"- condition_not_met: {c}")
        if judge_missing_n > 0:
            print(
                f"- missing_schema_version_rows_in_scope: {missing_schema_judge[:50]}"
                + (" ..." if len(missing_schema_judge) > 50 else "")
            )
        if args.scope == "tail" and all_missing_n > 0:
            print(
                f"[info] all-rows missing_schema_version exists: n={all_missing_n}/{all_rows_n} "
                f"(ratio={all_ratio:.3f})"
            )
    else:
        print("[OK] scope check passed")
        if args.scope == "tail" and all_missing_n > 0:
            print(
                f"[info] all-rows missing_schema_version exists (ignored by scope=tail): n={all_missing_n}/{all_rows_n} "
                f"(ratio={all_ratio:.3f})"
            )

    if tail_n > 0:
        print(f"[obs] tail_samples: last {tail_n} order_params rows")
        for line_no, ts, src, op in rows_all[-tail_n:]:
            print(f"--- line={line_no} timestamp={ts} src={src} ---")
            try:
                print(json.dumps(op, ensure_ascii=False, sort_keys=True))
            except Exception:
                print(str(op))

    return 0 if not unmet else 2


if __name__ == "__main__":
    raise SystemExit(main())


