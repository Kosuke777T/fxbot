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

from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


def _ops_card(
    title: str,
    summary: str,
    bullets: Optional[List[str]] = None,
    caveats: Optional[List[str]] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ops View 用のカードを“必ず同じ形”で作る統一ヘルパ。
    services は公式語彙で返し、GUI は表示言い換えのみ（判断ロジック禁止）。
    """
    return {
        "kind": "ops_card",
        "title": title,
        "summary": summary,
        "bullets": list(bullets or []),
        "caveats": list(caveats or []),
        "evidence": dict(evidence or {}),
    }


def _inspect_decisions_log_dir(symbol: str) -> Dict[str, Any]:
    """decisions_*.jsonl の存在と最終更新を軽く検査（断定しない材料用）。
    既存のログ配置を壊さない。見つからなければ files=0 扱い。
    """
    log_dir = Path("logs")
    files: List[str] = []
    latest_mtime: Optional[float] = None
    latest_file: Optional[str] = None

    if log_dir.exists() and log_dir.is_dir():
        for p in log_dir.glob("decisions_*.jsonl"):
            try:
                st = p.stat()
                files.append(str(p).replace("\\", "/"))
                m = st.st_mtime
                if (latest_mtime is None) or (m > latest_mtime):
                    latest_mtime = m
                    latest_file = str(p).replace("\\", "/")
            except Exception:
                pass

    latest_mtime_iso: Optional[str] = None
    if latest_mtime is not None:
        latest_mtime_iso = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()

    return {
        "log_dir_exists": bool(log_dir.exists()),
        "files": int(len(files)),
        "latest_mtime": latest_mtime_iso,
        "latest_file": latest_file,
    }


def _normalize_facade_envelope(
    symbol: str,
    warnings: Optional[List[str]] = None,
    ops_cards_first: Optional[List[Dict[str, Any]]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    evidence_kind: Optional[str] = None,
    evidence_src: Optional[str] = None,
) -> Dict[str, Any]:
    """Facade の返り値を固定形に正規化（GUI側の分岐を減らす）。"""
    return {
        "warnings": list(warnings or []),
        "ops_cards_first": list(ops_cards_first or []),
        "evidence": dict(evidence or {}),
        "evidence_kind": evidence_kind or "none",
        "evidence_src": evidence_src,
        "symbol": symbol,
    }


# === Step2-2: “同梱 + 縮退安定化 + カード整形統一” の公開関数 ===
def get_condition_mining_ops_snapshot(symbol: str, profile: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Ops View / GUI が直接叩く薄い Facade（同梱返却）。

    返すもの（固定）:
      - warnings: list[str]
      - ops_cards_first: list[ops_card]
      - evidence/evidence_kind/evidence_src: 根拠スナップショット（空でもキーは必ず存在）
    """
    # 既存関数（同一モジュール内に存在する前提）
    try:
        win = get_decisions_recent_past_window_info(symbol, profile=profile)  # type: ignore[name-defined]
        recent = win.get("recent") or {}
        past = win.get("past") or {}
    except Exception:
        recent = {}
        past = {}

    recent_n = int((recent or {}).get("n") or 0)
    past_n = int((past or {}).get("n") or 0)

    if recent_n == 0 and past_n == 0:
        warn = ["no_decisions_in_recent_and_past"]
        log_inspection = _inspect_decisions_log_dir(symbol)
        card = _ops_card(
            title="decisions が 0 件です（原因の推定）",
            summary=f"symbol={symbol} で recent/past ともに decisions=0 のため、探索AIは縮退動作中です。",
            bullets=[
                "decisions_*.jsonl が存在しません（稼働停止/出力設定/権限/パスの可能性）",
            ],
            caveats=[
                "decisions=0 の場合、フィルタ過多・稼働停止・データ欠損のどれもあり得ます（断定はしない）",
                "まずは decisionsログの最終更新時刻と、実行系（常駐/GUI起動中）の状態を確認してください",
            ],
            evidence={
                "symbol": symbol,
                "recent_n": recent_n,
                "past_n": past_n,
                "log_inspection": log_inspection,
            },
        )
        return _normalize_facade_envelope(
            symbol=symbol,
            warnings=warn,
            ops_cards_first=[card],
            evidence=card.get("evidence") or {},
            evidence_kind="ops_card",
            evidence_src="logs/decisions_*.jsonl",
        )

    base_evidence = {"symbol": symbol, "recent_n": recent_n, "past_n": past_n}
    return _normalize_facade_envelope(
        symbol=symbol,
        warnings=[],
        ops_cards_first=[],
        evidence=base_evidence,
        evidence_kind="counts",
        evidence_src="condition_mining_facade.get_decisions_recent_past_window_info",
    )

