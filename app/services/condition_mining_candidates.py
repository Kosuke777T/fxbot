from __future__ import annotations

from typing import Any, Dict, List, Optional
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
        pb = float(pb)
        ps = float(ps)
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
    profile: Optional[str] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # NOTE:
    # - candidates 生成には rows(decisions) が必要（n だけでは条件評価できない）
    # - include_decisions=False 経路の summary が渡される可能性があるため、
    #   decisions が無ければ include_decisions=True で再取得する
    data: Dict[str, Any]
    if isinstance(summary, dict):
        data = summary
    else:
        data = get_decisions_recent_past_summary(symbol, profile=profile, include_decisions=True)

    recent = (data.get("recent") or {})
    past = (data.get("past") or {})

    # if passed-in summary doesn't contain decisions, refetch with include_decisions=True
    if (("decisions" not in recent) and ("decisions" not in past)):
        data = get_decisions_recent_past_summary(symbol, profile=profile, include_decisions=True)
        recent = (data.get("recent") or {})
        past = (data.get("past") or {})

    recent_rows = recent.get("decisions") or []
    past_rows = past.get("decisions") or []

    rn = int(recent.get("n") or 0)
    pn = int(past.get("n") or 0)
    warnings: List[str] = []

    # --- Step2-20: past-only candidates fallback ---
    # recent が 0 件でも past が十分にある場合は、縮退せず past を入力として候補生成を継続する。
    if rn == 0 and pn > 0 and (not recent_rows) and past_rows:
        recent_rows = list(past_rows)
        warnings.append("recent_empty_use_past_only")
    # --- end Step2-20 ---

    # total=0 でも落ちない
    if not recent_rows and not past_rows:
        return {
            "symbol": symbol,
            "candidates": [],
            "warnings": (warnings or ["no_decisions_in_recent_and_past"]),
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

    # --- Step2-20: extra generators (increase candidate pool; minimal; no new funcs) ---
    # hour_in (single hour) を追加：hour bucket だけだと候補が少なすぎるため
    try:
        uniq_hours = sorted(set(int(h) for h in hours if h is not None))
        for h in uniq_hours[:24]:
            c1.append(
                {
                    "type": "hour_in",
                    "params": {"hours": [h]},
                    "id": f"hour:h{h:02d}",
                    "description": f"Hour is {h:02d}",
                    "tags": ["hour", "single"],
                }
            )
    except Exception:
        pass

    # prob_margin_ge の閾値を増やす：分位点ベース（supportが偏りすぎるのを防ぐ）
    try:
        vals = sorted(float(x) for x in margins if x is not None)
        if vals:
            qs = [0.5, 0.6, 0.7, 0.8, 0.9]
            for q in qs:
                idx = int((len(vals) - 1) * q)
                thr = vals[max(0, min(idx, len(vals) - 1))]
                thr_id = f"{thr:.3f}"
                c1.append(
                    {
                        "type": "prob_margin_ge",
                        "params": {"min": float(thr)},
                        "id": f"pm:ge_{thr_id}",
                        "description": f"Prob margin >= {thr_id}",
                        "tags": ["pm", "ge", "quantile"],
                    }
                )
    except Exception:
        pass
    # --- end Step2-20 ---

    # 上限（暴走防止）
    c1 = c1[:max_conds]

    # --- Step2-20: dedupe by condition.id (stable, keep-first) ---
    # extra generators により同一 id が生成され得る（例: pm:ge_0.386）。
    try:
        _seen_ids = set()
        _dedup_c1: List[Condition] = []
        for _x in c1:
            if not isinstance(_x, dict):
                continue
            _id = _x.get("id")
            if isinstance(_id, str) and _id:
                if _id in _seen_ids:
                    continue
                _seen_ids.add(_id)
            _dedup_c1.append(_x)
        c1 = _dedup_c1
    except Exception:
        pass
    # --- end Step2-20 ---

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
        degradation = bool(
            (recent_s >= min_support) and (past_s >= min_support) and (delta <= -0.10)
        )

        conf = _confidence(recent_s, past_s, min_support=min_support, degradation=degradation)

        score = (
            (recent_s + past_s) * 0.01
            + (float(r["filter_pass_rate"]) * 2.0)
            + (delta * 1.5)
            + (0.2 if conf == "HIGH" else 0.0)
            - (0.3 if degradation else 0.0)
        )

        cards.append(
            {
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
            }
        )

    cards.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    out = cards[: max(0, int(top_k))]

    if len(out) == 0:
        warnings.append("no_candidates_after_support_guard")

    logger.info(
        f"[cond_mine] symbol={symbol} candidates={len(out)} (top_k={top_k}, max_conds={max_conds})"
    )
    ret: Dict[str, Any] = {
        "symbol": symbol,
        "candidates": out,
        "warnings": warnings,
    }

    # --- Step2-20: candidates debug (opt-in) ---
    # Enable by env: CM_CANDIDATES_DEBUG=1
    # NOTE: 既存出力に影響しないよう、追加キーは setdefault で付与する。
    # rows_used_n: past-only fallback 時に二重計上しない
    try:
        _w = ret.get("warnings") or []
        _past_only = isinstance(_w, list) and ("recent_empty_use_past_only" in _w)
        if _past_only:
            ret.setdefault("rows_used_n", int(len(past_rows)))
        else:
            ret.setdefault("rows_used_n", int(len(recent_rows) + len(past_rows)))
    except Exception:
        ret.setdefault("rows_used_n", int(len(recent_rows) + len(past_rows)))

    ret.setdefault("min_support", int(min_support))
    ret.setdefault("top_k", int(top_k))

    try:
        import os

        if os.environ.get("CM_CANDIDATES_DEBUG", "").strip() in (
            "1",
            "true",
            "True",
            "yes",
            "YES",
        ):
            _w = ret.get("warnings") or []
            _dbg = {
                "symbol": symbol,
                "profile": profile,
                "recent_empty_use_past_only": ("recent_empty_use_past_only" in _w)
                if isinstance(_w, list)
                else False,
                "fallback_used": ("recent_empty_use_past_only" in _w)
                if isinstance(_w, list)
                else False,
                "rows_used_n": ret.get("rows_used_n"),
                "min_support": ret.get("min_support"),
                "top_k": ret.get("top_k"),
                "candidates_len": len(ret.get("candidates") or [])
                if isinstance(ret.get("candidates"), list)
                else None,
            }
            print("[CM_CANDIDATES_DEBUG]", _dbg)

            # support 上位だけ（support:{recent,past} を合算）
            cands = ret.get("candidates")
            if isinstance(cands, list) and cands and isinstance(cands[0], dict):

                def _support_sum(x: dict) -> float:
                    sup = x.get("support")
                    if isinstance(sup, dict):
                        return float(int(sup.get("recent") or 0) + int(sup.get("past") or 0))
                    return 0.0

                top = sorted(cands, key=_support_sum, reverse=True)[:5]
                print(
                    "[CM_CANDIDATES_DEBUG] top_support=",
                    [(_support_sum(x), ((x.get("condition") or {}).get("id"))) for x in top],
                )
    except Exception:
        pass
    # --- end Step2-20 ---

    return ret


def get_condition_candidates(
    symbol: str,
    profile: Optional[str] = None,
    summary: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Backward-compatible wrapper.

    - Keeps public API stable for callers expecting `get_condition_candidates`.
    - Delegates to get_condition_candidates_core.
    """
    return get_condition_candidates_core(symbol=symbol, profile=profile, summary=summary, **kwargs)

