from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class SwitchRecord:
    ts: str
    symbol: str
    from_profile: str
    to_profile: str
    raw_reason: str


def _iter_decisions(path: Path) -> Iterable[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield rec


def _extract_reasons(rec: dict) -> list[str]:
    reasons: list[str] = []

    # v5.1フォーマット: filters.filter_reasons
    filters = rec.get("filters") or {}
    fl = filters.get("filter_reasons") or []
    if isinstance(fl, list):
        reasons.extend(r for r in fl if isinstance(r, str))

    # decision_detail 側にもあれば追加
    detail = rec.get("decision_detail") or {}
    dl = detail.get("filter_reasons") or []
    if isinstance(dl, list):
        reasons.extend(r for r in dl if isinstance(r, str))

    return reasons


def _parse_switch_reason(reason: str) -> tuple[str, str] | None:
    # "profile_switch:std->aggr" または "profile_switch:michibiki_std->michibiki_aggr"
    if not reason.startswith("profile_switch:"):
        return None
    body = reason.split("profile_switch:", 1)[1]
    if "->" not in body:
        return None
    from_p, to_p = body.split("->", 1)
    return from_p, to_p


def analyze_switches(symbol: str, limit: int = 20) -> list[SwitchRecord]:
    path = Path("logs/decisions") / f"decisions_{symbol}.jsonl"
    records: list[SwitchRecord] = []

    for rec in _iter_decisions(path):
        sym = rec.get("symbol") or symbol
        ts = rec.get("ts_jst") or rec.get("timestamp") or ""
        for r in _extract_reasons(rec):
            parsed = _parse_switch_reason(r)
            if parsed is None:
                continue
            from_p, to_p = parsed
            records.append(
                SwitchRecord(
                    ts=str(ts),
                    symbol=str(sym),
                    from_profile=from_p,
                    to_profile=to_p,
                    raw_reason=r,
                )
            )

    # 新しいものが後ろに来ている前提で、末尾から limit 件を返す
    return records[-limit:]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze profile_switch reasons from decisions.jsonl"
    )
    parser.add_argument(
        "--symbol",
        default="USDJPY-",
        help="symbol name (default: USDJPY-)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="max number of switches to show",
    )

    args = parser.parse_args()

    records = analyze_switches(symbol=args.symbol, limit=args.limit)

    print(f"symbol       : {args.symbol}")
    print(f"switch_count : {len(records)}")
    print("=== last switches ===")
    for r in records:
        print(
            f"{r.ts}  {r.symbol}  {r.from_profile} -> {r.to_profile}  ({r.raw_reason})"
        )


if __name__ == "__main__":
    main()

