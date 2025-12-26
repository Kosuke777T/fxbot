# app/services/ops_overview_facade.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.ops_history_service import summarize_ops_history
from app.services.wfo_stability_service import load_saved_stability


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_latest_retrain_report() -> Optional[Dict[str, Any]]:
    """
    最新の retrain report (logs/retrain/report_*.json) を読み込む。
    """
    report_dir = Path("logs") / "retrain"
    if not report_dir.exists():
        return None

    report_candidates = list(report_dir.glob("report_*.json"))
    if not report_candidates:
        return None

    # 最新のファイルを取得
    latest_report = max(report_candidates, key=lambda p: p.stat().st_mtime)

    try:
        return json.loads(latest_report.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def get_ops_overview() -> Dict[str, Any]:
    """
    Ops / Scheduler / AI の「運用意思決定」を GUI が1回で描画できるようにまとめて返す薄いラッパ。
    - 既存APIの結果を寄せ集めるだけ（判断ロジックは各service側に置く）
    """
    out: Dict[str, Any] = {
        "next_action": None,
        "wfo_stability": None,
        "latest_retrain": None,
        "generated_at": _now_iso_utc(),
    }

    # 0) retrain report は1回だけ読む
    report = None
    try:
        report = _load_latest_retrain_report()
    except Exception:
        report = None

    # 1) next_action（PROMOTE/HOLD/BLOCKED 等）
    try:
        summary = summarize_ops_history(cache_sec=2)
        last_view = summary.get("last_view") or {}
        next_action = last_view.get("next_action")
        if next_action:
            out["next_action"] = next_action
    except Exception:
        out["next_action"] = None

    # 2) WFO stability（stable/score/reasons）
    try:
        run_id = None
        if report:
            rid = report.get("run_id")
            if rid is not None:
                run_id = str(rid)

        if run_id:
            stability = load_saved_stability(run_id)
            if stability:
                out["wfo_stability"] = {
                    "stable": bool(stability.get("stable", False)),
                    "score": stability.get("score"),
                    "reasons": stability.get("reasons") or [],
                    "run_id": run_id,
                    "sources": stability.get("sources"),
                }
            else:
                # ★stabilityが無い/読めないケースも可視化（運用で超大事）
                out["wfo_stability"] = {
                    "stable": False,
                    "score": None,
                    "reasons": ["stability_not_found"],
                    "run_id": run_id,
                    "sources": None,
                }
    except Exception:
        out["wfo_stability"] = None

    # 3) 直近 retrain（run_id/data_range/threshold）
    try:
        if report:
            out["latest_retrain"] = {
                "run_id": report.get("run_id"),
                "data_range": report.get("data_range"),
                "threshold": report.get("threshold"),
            }
    except Exception:
        out["latest_retrain"] = None

    return out


