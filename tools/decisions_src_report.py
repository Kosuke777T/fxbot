from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _find_latest_decisions_log(logs_dir: Path) -> Optional[Path]:
    c = sorted(logs_dir.glob("decisions_*.jsonl"))
    if not c:
        return None
    c.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return c[-1]


def _get(d: Any, path: tuple[str, ...]) -> Any:
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Decisions src/order_params shape report (read-only).")
    ap.add_argument("--path", type=str, default="", help="decisions_*.jsonl path (optional).")
    ap.add_argument("--tail", type=int, default=300, help="use last N rows for report (0=all).")
    ap.add_argument("--symbol", type=str, default="", help="filter by symbol (optional).")
    args = ap.parse_args(argv)

    logs_dir = Path("logs")
    if args.path:
        p = Path(args.path)
        if not p.exists():
            print(f"[NG] path not found: {p}")
            return 2
    else:
        p = _find_latest_decisions_log(logs_dir)
        if p is None:
            print("[NG] no logs/decisions_*.jsonl found")
            return 2

    rows = list(_iter_jsonl(p))
    if args.symbol:
        rows = [r for r in rows if r.get("symbol") == args.symbol]

    if not rows:
        print(f"[NG] no rows found (path={p.as_posix()} symbol={args.symbol!r})")
        return 2

    tail_n = int(args.tail)
    if tail_n > 0:
        rows = rows[-tail_n:]

    print(f"[obs] decisions_log={p.as_posix()} rows={len(rows)} tail={tail_n}")

    # src distribution
    src_ctr: Counter[str] = Counter()
    for r in rows:
        src = r.get("src")
        src_ctr[src if isinstance(src, str) else "(none)"] += 1
    print(f"[obs] src_counts_top10={src_ctr.most_common(10)}")

    # order_params (top/detail) + schema_version
    cnt_top = 0
    cnt_src = 0
    cnt_top_nosrc = 0
    cnt_detail_only = 0
    keys_top: set[str] = set()
    keys_detail: set[str] = set()
    miss_sv_top = 0
    miss_sv_src = 0

    for r in rows:
        op = r.get("order_params")
        ddop = _get(r, ("decision_detail", "order_params"))

        is_top = isinstance(op, dict) and bool(op)
        is_src = is_top and (r.get("src") == "order_params")
        is_top_nosrc = is_top and not is_src
        is_detail_only = (not is_top) and isinstance(ddop, dict) and bool(ddop)

        if is_top:
            cnt_top += 1
            keys_top.update(op.keys())
            if "schema_version" not in op:
                miss_sv_top += 1
        if is_src:
            cnt_src += 1
            if "schema_version" not in op:
                miss_sv_src += 1
        if is_top_nosrc:
            cnt_top_nosrc += 1
        if is_detail_only:
            cnt_detail_only += 1
            keys_detail.update(ddop.keys())

        if isinstance(ddop, dict) and bool(ddop):
            keys_detail.update(ddop.keys())

    denom = len(rows) if len(rows) > 0 else 1
    print(f"[obs] top.order_params.rows={cnt_top} ratio={cnt_top/denom:.3f}")
    print(f"[obs] src==order_params.rows={cnt_src} ratio={cnt_src/denom:.3f}")
    print(f"[obs] top.order_params_without_src.rows={cnt_top_nosrc} ratio={cnt_top_nosrc/denom:.3f}")
    print(f"[obs] detail_only.order_params.rows={cnt_detail_only} ratio={cnt_detail_only/denom:.3f}")
    print(f"[obs] top.order_params.keys_union({len(keys_top)}): {sorted(keys_top)}")
    print(f"[obs] decision_detail.order_params.keys_union({len(keys_detail)}): {sorted(keys_detail)}")
    print(
        f"[obs] missing_schema_version: top={miss_sv_top}/{cnt_top} "
        f"src==order_params={miss_sv_src}/{cnt_src}"
    )

    # evaluation axes presence (existence only)
    axes = {
        "filter_pass": lambda r: r.get("filter_pass") is not None,
        "filter_reasons": lambda r: isinstance(r.get("filter_reasons"), (list, tuple)) and len(r.get("filter_reasons")) > 0,
        "prob_buy": lambda r: r.get("prob_buy") is not None,
        "prob_sell": lambda r: r.get("prob_sell") is not None,
        "decision": lambda r: r.get("decision") is not None,
        "action": lambda r: r.get("action") is not None,
        "pnl_top": lambda r: r.get("pnl") is not None,
        "pnl_detail": lambda r: _get(r, ("decision_detail", "pnl")) is not None,
    }
    axc: Counter[str] = Counter()
    for r in rows:
        for k, fn in axes.items():
            try:
                if fn(r):
                    axc[k] += 1
            except Exception:
                pass
    print(f"[obs] axes_presence_counts={dict(axc)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

