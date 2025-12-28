from __future__ import annotations

from app.services.condition_mining_data import get_decisions_window_summary, build_ops_cards_for_zero_decisions
from datetime import datetime, timezone

from typing import Any, Dict, List, Optional

from app.services.condition_mining_data import get_decisions_recent_past_summary


def get_decisions_recent_past_min_stats(symbol: str) -> Dict[str, Any]:
    """
    Facade: recent/past の min_stats だけを返す薄いAPI。
    既存の get_decisions_recent_past_summary(symbol) を再利用し、抽出のみを行う。
    """
    try:
        out = get_decisions_recent_past_summary(symbol)
        recent = (out.get("recent") or {})
        past = (out.get("past") or {})
        return {
            "recent": {"min_stats": (recent.get("min_stats") or {})},
            "past": {"min_stats": (past.get("min_stats") or {})},
        }
    except Exception:
        # GUI側を落とさない（縮退）
        return {
            "recent": {"min_stats": {}},
            "past": {"min_stats": {}},
        }


def get_decisions_recent_past_window_info(symbol: str, profile: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """
    Facade: recent/past の件数・期間（start/end）を 即取得できる薄いラッパー。
    ついでに min_stats も同梱（GUIやOpsで同時に使う ため・後方互換）。

    T-43-3:
    - decisions=0 でも Ops 向けに「なぜ0件か」の推定カードを返す（ops_cards）
    - warnings を必ず返す（縮退時もキー欠落させない）
    """
    try:
        summary = get_decisions_recent_past_summary(symbol, profile=profile, **kwargs)
        recent = (summary.get("recent") or {})
        past = (summary.get("past") or {})

        rn = int(recent.get("n", 0) or 0)
        pn = int(past.get("n", 0) or 0)

        warnings: List[str] = []
        ops_cards: List[Dict[str, Any]] = []

        if rn == 0 and pn == 0:
            warnings = ["no_decisions_in_recent_and_past"]
            try:
                ops_cards = build_ops_cards_for_zero_decisions(symbol, {"n": rn, **(recent or {})}, {"n": pn, **(past or {})})
            except Exception:
                ops_cards = []

        return {
            "recent": {
                "n": rn,
                "range": recent.get("range", {"start": None, "end": None}),
                "min_stats": (recent.get("min_stats") or {}),
            },
            "past": {
                "n": pn,
                "range": past.get("range", {"start": None, "end": None}),
                "min_stats": (past.get("min_stats") or {}),
            },
            "warnings": warnings,
            "ops_cards": ops_cards,
        }
    except Exception:
        # 縮退：キー欠落を絶対に起こさない
        warnings: List[str] = ["window_info_failed"]
        ops_cards: List[Dict[str, Any]] = []
        try:
            ops_cards = build_ops_cards_for_zero_decisions(
                symbol,
                {"n": 0, "range": {"start": None, "end": None}, "min_stats": {}},
                {"n": 0, "range": {"start": None, "end": None}, "min_stats": {}},
            )
        except Exception:
            ops_cards = []

        return {
            "recent": {"n": 0, "range": {"start": None, "end": None}, "min_stats": {}},
            "past": {"n": 0, "range": {"start": None, "end": None}, "min_stats": {}},
            "warnings": warnings,
            "ops_cards": ops_cards,
        }


def _boolish(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "ok")
    return False


def _is_entry(rec: Dict[str, Any]) -> bool:
    """entry判定（ログ形式の差分に耐える）"""
    # よくあるフラグ
    for k in ("entry", "entered", "is_entry"):
        if k in rec:
            return _boolish(rec.get(k))
    # よくある action/decision
    for k in ("action", "decision", "kind"):
        v = rec.get(k)
        if isinstance(v, str) and v.strip().upper() in ("ENTRY", "ENTER", "BUY", "SELL"):
            return True
    return False


def _get_filter_reasons(rec: Dict[str, Any]) -> List[str]:
    """
    候補生成用の理由抽出（ログの仕様差分に耐える）
    優先:
      1) rec.filter_reasons（list/str）
      2) decision_detail.signal.reason（例: threshold_ok）
      3) filters の非None項目（presence）
      4) filters.filter_level（存在すれば）
    """
    # 1) 直接の filter_reasons
    v = rec.get("filter_reasons")
    if isinstance(v, list):
        out = [str(x) for x in v if x is not None and str(x).strip() != ""]
        if out:
            return out
    if isinstance(v, str) and v.strip() != "":
        return [v.strip()]

    reasons: List[str] = []

    # 2) decision_detail.signal.reason
    dd = rec.get("decision_detail")
    if isinstance(dd, dict):
        sig = dd.get("signal")
        if isinstance(sig, dict):
            r = sig.get("reason")
            if isinstance(r, str) and r.strip():
                reasons.append(f"signal_reason:{r.strip()}")

    # 3) filters presence
    f = rec.get("filters")
    if isinstance(f, dict):
        # 除外キー（ノイズ）
        skip = {"timestamp", "profile_stats", "filter_reasons"}
        for k, val in f.items():
            if k in skip:
                continue
            # None以外を「条件が評価されている」とみなして候補化
            if val is not None:
                reasons.append(f"filters:{k}")

        # 4) filter_level（あれば）
        lv = f.get("filter_level")
        if lv is not None:
            reasons.append(f"filter_level:{lv}")

    # 重複排除（順序維持）
    seen = set()
    uniq = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq

    if isinstance(v, list):
        return [str(x) for x in v if x is not None and str(x).strip() != ""]
    if isinstance(v, str) and v.strip() != "":
        return [v.strip()]
    return []


def get_condition_candidates(symbol: str = "USDJPY-", top_n: int = 10) -> dict:
    # T-43 条件探索・比較AI（Facade）
    # - 既存GUI/API互換を維持（top_n を維持）
    # - 内部で T-43-2 core を呼ぶ（top_k にマップ）
    from app.services.condition_mining_candidates import get_condition_candidates_core

    return get_condition_candidates_core(
        symbol=symbol,
        top_k=top_n,
        max_conds=80,
        min_support=20,
    )
