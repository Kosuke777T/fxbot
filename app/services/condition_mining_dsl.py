from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple
from collections import Counter


Condition = Dict[str, Any]


def _dedupe(conds: Iterable[Condition]) -> List[Condition]:
    seen = set()
    out: List[Condition] = []
    for c in conds:
        key = (c.get("type"), repr(c.get("params", {})))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def build_reason_conditions(reasons: Iterable[str], top_n: int = 30) -> List[Condition]:
    # reasons は decisions 内の reason / reasons / meta.reason_codes などから抽出される前提
    cnt = Counter([r for r in reasons if isinstance(r, str) and r.strip()])
    out: List[Condition] = []
    for k, _ in cnt.most_common(top_n):
        out.append({
            "id": f"reason:{k}",
            "type": "reason_in",
            "params": {"keys": [k]},
            "description": f"フィルタ理由に '{k}' を含む",
            "tags": ["reason"],
        })
    return _dedupe(out)


def build_hour_bucket_conditions(hours: Iterable[int]) -> List[Condition]:
    # ざっくり 0-7 / 8-15 / 16-23 の 3分割（重くしない）
    out: List[Condition] = []
    hs = [h for h in hours if isinstance(h, int) and 0 <= h <= 23]
    if not hs:
        return out

    buckets = [
        ("h00_07", range(0, 8)),
        ("h08_15", range(8, 16)),
        ("h16_23", range(16, 24)),
    ]
    for bid, rr in buckets:
        out.append({
            "id": f"hour:{bid}",
            "type": "hour_in",
            "params": {"hours": list(rr)},
            "description": f"時間帯が {rr.start:02d}-{rr.stop-1:02d} 時",
            "tags": ["time_of_day"],
        })
    return out


def build_prob_margin_conditions(margins: Iterable[float]) -> List[Condition]:
    # prob_margin = max(prob_buy, prob_sell) - 0.5 のような “勢い” 指標を想定
    ms = sorted([float(x) for x in margins if x is not None])
    if len(ms) < 50:
        return []

    # 分位で 2点だけ切る（暴走防止）
    def q(p: float) -> float:
        i = int((len(ms) - 1) * p)
        return ms[max(0, min(len(ms) - 1, i))]

    q70 = q(0.70)
    q85 = q(0.85)

    out = [
        {
            "id": f"pm:ge_{q70:.3f}",
            "type": "prob_margin_ge",
            "params": {"min": q70},
            "description": f"prob_margin >= {q70:.3f}",
            "tags": ["prob"],
        },
        {
            "id": f"pm:ge_{q85:.3f}",
            "type": "prob_margin_ge",
            "params": {"min": q85},
            "description": f"prob_margin >= {q85:.3f}",
            "tags": ["prob"],
        },
    ]
    return _dedupe(out)


def and2(a: Condition, b: Condition) -> Condition:
    # ANDは2条件まで（T-43-2では上限固定）
    ida = a.get("id", "A")
    idb = b.get("id", "B")
    return {
        "id": f"and:{ida}&{idb}",
        "type": "and",
        "params": {"conds": [a, b]},
        "description": f"({a.get('description')}) AND ({b.get('description')})",
        "tags": sorted(set((a.get("tags") or []) + (b.get("tags") or []) + ["and"])),
    }
