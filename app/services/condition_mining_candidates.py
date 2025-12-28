from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional
from loguru import logger

from app.services.condition_mining_data import get_decisions_recent_past_summary
from app.services.condition_mining_dsl import (
    Condition,
    build_reason_conditions,
    build_hour_bucket_conditions,
    build_prob_margin_conditions,
    and2,
)

# ---------- extraction helpers (robust; no new deps) ----------

def _get_reason_codes(rec: Dict[str, Any]) -> List[str]:
    # decisions.jsonl は実装差が出るので “あるものだけ拾う”
    out: List[str] = []
    v = rec.get("reason")
    if isinstance(v, str) and v:
        out.append(v)

    v2 = rec.get("reasons")
    if isinstance(v2, list):
        out.extend([x for x in v2 if isinstance(x, str)])

    meta = rec.get("meta") or {}
    if isinstance(meta, dict):
        v3 = meta.get("reason_codes")
        if isinstance(v3, list):
            out.extend([x for x in v3 if isinstance(x, str)])
    return [x.strip() for x in out if isinstance(x, str) and x.strip()]


def _get_hour(rec: Dict[str, Any]) -> Optional[int]:
    ts = rec.get("timestamp")
    # T-43-1で dt 正規化済み想定：datetime or ISO str
    try:
        import datetime as _dt
        if isinstance(ts, _dt.datetime):
            return int(ts.hour)
        if isinstance(ts, str) and ts:
            # fromisoformat で読める形式を期待（T-43-1）
            dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.hour)
    except Exception:
        return None
    return None


def _get_prob_margin(rec: Dict[str, Any]) -> Optional[float]:
    try:
        pb = rec.get("prob_buy")
        ps = rec.get("prob_sell")
        if pb is None or ps is None:
            meta = rec.get("meta") or {}
            if isinstance(meta, dict):
                pb = meta.get("prob_buy", pb)
                ps = meta.get("prob_sell", ps)
        if pb is None or ps is None:
            return None
        pb = float(pb); ps = float(ps)
        return max(pb, ps) - 0.5
    except Exception:
        return None


def _match(cond: Condition, rec: Dict[str, Any]) -> bool:
    t = cond.get("type")
    p = cond.get("params") or {}

    if t == "reason_in":
        keys = set(p.get("keys") or [])
        rs = set(_get_reason_codes(rec))
        return len(keys & rs) > 0

    if t == "hour_in":
        hs = set(p.get("hours") or [])
        h = _get_hour(rec)
        return (h in hs) if h is not None else False

    if t == "prob_margin_ge":
        m = _get_prob_margin(rec)
        mn = p.get("min")
        return (m is not None and mn is not None and float(m) >= float(mn))

    if t == "and":
        sub = p.get("conds") or []
        if len(sub) != 2:
            return False
        return _match(sub[0], rec) and _match(sub[1], rec)

    return False


def _eval_condition(cond: Condition, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    # support と filter_pass_rate だけは必ず返す（outcome無くても根拠になる）
    hits = [r for r in rows if _match(cond, r)]
    support = len(hits)
    if support == 0:
        return {"support": 0, "filter_pass_rate": 0.0}

    fp = 0
    for r in hits:
        if r.get("filter_pass") is True:
            fp += 1
    return {
        "support": support,
        "filter_pass_rate": float(fp / support) if support else 0.0,
    }


def _confidence(recent_s: int, past_s: int, min_support: int, degradation: bool) -> str:
    # canonical: LOW/MID/HIGH
    if recent_s >= min_support and past_s >= min_support and not degradation:
        return "HIGH"
    if recent_s >= min_support and past_s >= min_support and degradation:
        return "MID"
    if recent_s >= min_support or past_s >= min_support:
        return "MID"
    return "LOW"


def get_condition_candidates_core(
    symbol: str,
    top_k: int = 10,
    max_conds: int = 80,
    min_support: int = 20,
) -> Dict[str, Any]:
    data = get_decisions_recent_past_summary(symbol)
    recent_rows = (data.get("recent") or {}).get("decisions") or []
    past_rows = (data.get("past") or {}).get("decisions") or []

    # total=0 でも落ちない
    if not recent_rows and not past_rows:
        return {
            "symbol": symbol,
            "candidates": [],
            "warnings": ["no_decisions_in_recent_and_past"],
        }

    # ---- candidate generation (lightweight) ----
    reasons: List[str] = []
    hours: List[int] = []
    margins: List[float] = []
    for r in (recent_rows + past_rows):
        reasons.extend(_get_reason_codes(r))
        h = _get_hour(r)
        if h is not None:
            hours.append(h)
        m = _get_prob_margin(r)
        if m is not None:
            margins.append(m)

    c1: List[Condition] = []
    c1.extend(build_reason_conditions(reasons, top_n=30))
    c1.extend(build_hour_bucket_conditions(hours))
    c1.extend(build_prob_margin_conditions(margins))

    # 上限（暴走防止）
    c1 = c1[:max_conds]

    # 2条件AND（少数だけ）
    c2: List[Condition] = []
    for i in range(min(12, len(c1))):
        for j in range(i + 1, min(12, len(c1))):
            c2.append(and2(c1[i], c1[j]))

    conds = (c1 + c2)[:max_conds]

    # ---- evaluate & rank ----
    cards: List[Dict[str, Any]] = []
    for c in conds:
        r = _eval_condition(c, recent_rows)
        p = _eval_condition(c, past_rows)

        recent_s = int(r["support"])
        past_s = int(p["support"])
        if recent_s + past_s < min_support:
            continue

        # 劣化：recent の filter_pass_rate が past より明確に悪い（簡易）
        delta = float(r["filter_pass_rate"]) - float(p["filter_pass_rate"])
        degradation = bool((recent_s >= min_support) and (past_s >= min_support) and (delta <= -0.10))

        conf = _confidence(recent_s, past_s, min_support=min_support, degradation=degradation)

        score = (
            (recent_s + past_s) * 0.01
            + (float(r["filter_pass_rate"]) * 2.0)
            + (delta * 1.5)
            + (0.2 if conf == "HIGH" else 0.0)
            - (0.3 if degradation else 0.0)
        )

        cards.append({
            "condition": {
                "id": c.get("id"),
                "description": c.get("description"),
                "tags": c.get("tags") or [],
            },
            "support": {
                "recent": recent_s,
                "past": past_s,
            },
            "condition_confidence": conf,
            "degradation": degradation,
            "score": float(score),
            "evidence": {
                "recent": r,
                "past": p,
                "delta": {
                    "filter_pass_rate": delta,
                },
                "notes": [
                    "metrics_are_lightweight_v0",
                    "support_guard_applied",
                ],
            },
        })

    cards.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    out = cards[:max(0, int(top_k))]

    warnings: List[str] = []
    if len(out) == 0:
        warnings.append("no_candidates_after_support_guard")

    logger.info(f"[cond_mine] symbol={symbol} candidates={len(out)} (top_k={top_k}, max_conds={max_conds})")
    return {
        "symbol": symbol,
        "candidates": out,
        "warnings": warnings,
    }
