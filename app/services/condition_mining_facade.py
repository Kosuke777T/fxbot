from __future__ import annotations

from app.services.condition_mining_data import get_decisions_window_summary
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


def _extract_decisions_list(win: Dict[str, Any]) -> List[Dict[str, Any]]:
    """window summary から decisions の配列を取り出す（仕様差分に耐える）"""
    if not isinstance(win, dict):
        return []
    for k in ("decisions", "items", "rows", "records"):
        v = win.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


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


def get_condition_candidates(symbol: str, top_n: int = 20) -> Dict[str, Any]:
    """条件探索AI（T-43）用：フィルタ理由の頻度上位を候補として返す（最小版）"""
    out = get_decisions_recent_past_summary(symbol)
    now = datetime.now(timezone.utc).isoformat()

    warnings: List[str] = []
    res: Dict[str, Any] = {"symbol": symbol, "generated_at": now, "recent": {}, "past": {}, "warnings": warnings}

    for bucket in ("recent", "past"):
        win = (out.get(bucket) or {})
        decisions = _extract_decisions_list(win)
        if not decisions:
            # summaryに決定リストが無い設計なので、window_summaryの時刻範囲で jsonl を読む
            wsum = get_decisions_window_summary(symbol, window=bucket) or {}
            start_ts = wsum.get("start_ts")
            end_ts = wsum.get("end_ts")

            # windowが未定義（None）なら暫定分割：直近K件をrecent、その前K件をpast
            if start_ts is None and end_ts is None:
                K = 3000  # まずは十分大きめ（既存 max_scan と同程度の思想）
                all_recs = list(_iter_decisions_jsonl(symbol, max_n=K*2) or [])
                if bucket == "recent":
                    decisions = all_recs[-K:]
                else:
                    decisions = all_recs[-K*2:-K]
            else:
                # readerで補う（時刻範囲で切る）
                decisions = []
                for rec in _iter_decisions_jsonl(symbol, max_n=200000) or []:
                    if _in_window(rec, start_ts, end_ts):
                        decisions.append(rec)

            if not decisions:
                warnings.append(f"{bucket}: no decisions (summary has none; jsonl missing/empty or no records in window)")
                res[bucket] = {"candidates": [], "stats": (win.get("min_stats") or {})}
                continue


        # reason => counts
        counts: Dict[str, int] = {}
        pass_counts: Dict[str, int] = {}
        entry_counts: Dict[str, int] = {}

        for rec in decisions:
            reasons = _get_filter_reasons(rec)
            fp = _boolish(rec.get("filter_pass"))
            en = _is_entry(rec)
            if not reasons:
                continue
            for r in reasons:
                counts[r] = counts.get(r, 0) + 1
                if fp:
                    pass_counts[r] = pass_counts.get(r, 0) + 1
                if en:
                    entry_counts[r] = entry_counts.get(r, 0) + 1

        items = []
        for r, c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[: max(0, int(top_n))]:
            pc = pass_counts.get(r, 0)
            ec = entry_counts.get(r, 0)
            items.append({
                "reason": r,
                "count": c,
                "filter_pass_count": pc,
                "filter_pass_rate": (pc / c) if c else 0.0,
                "entry_count": ec,
                "entry_rate": (ec / c) if c else 0.0,
            })

        res[bucket] = {
            "stats": (win.get("min_stats") or {}),
            "candidates": items,
        }

    return res


def get_condition_diagnostics(symbol: str) -> Dict[str, Any]:
    """診断用：recent/past の min_stats と候補生成の警告を返す（最小版）"""
    c = get_condition_candidates(symbol, top_n=10)
    return {
        "symbol": symbol,
        "generated_at": c.get("generated_at"),
        "recent_min_stats": ((c.get("recent") or {}).get("stats") or {}),
        "past_min_stats": ((c.get("past") or {}).get("stats") or {}),
        "warnings": (c.get("warnings") or []),
    }

from pathlib import Path
import json

# _DECISIONS_LOG = <resolved dynamically>


def _iter_decisions_jsonl(max_n: int = 5000):
    """decisions.jsonl を後ろから最大 max_n 件読む（安全・既存前提）"""
    p = _DECISIONS_LOG
    if not p.exists():
        return
    lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
    for line in lines[-max_n:]:
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                yield rec
        except Exception:
            continue


def _in_window(rec: dict, start_ts: str | None, end_ts: str | None) -> bool:
    """ISO文字列の範囲判定（None は無制限）"""
    ts = rec.get('ts_jst') or rec.get('ts') or rec.get('timestamp') or rec.get('time')
    if not isinstance(ts, str):
        return False
    if start_ts and ts < start_ts:
        return False
    if end_ts and ts > end_ts:
        return False
    return True


def _resolve_decisions_log(symbol: str) -> Path | None:
    """
    decisions.jsonl の所在を symbol から推定して解決する。
    既存成果物を探すだけ（新規生成はしない）。
    優先:
      1) logs/backtest/<symbol>/M5/decisions.jsonl
      2) logs/decisions/decisions_<symbol_without_dash>.jsonl
      3) logs/backtest/<symbol>/M5/backtest_* /decisions.jsonl のうち最新
    """
    sym = (symbol or "").strip()
    if not sym:
        return None

    # 1) backtestの集約（M5は現状固定：既存成果物の場所に合わせる）
    p1 = Path('logs') / 'backtest' / sym / 'M5' / 'decisions.jsonl'
    if p1.exists():
        return p1

    # 2) decisions フォルダ（USDJPY- なら USDJPY に寄せる）
    sym2 = sym.replace('-', '')
    p2 = Path('logs') / 'decisions' / f'decisions_{sym2}.jsonl'
    if p2.exists():
        return p2

    # 3) backtest run別フォルダの中から最新っぽいもの
    base = Path('logs') / 'backtest' / sym / 'M5'
    if base.exists():
        cands = sorted(base.glob('backtest_*/decisions.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in cands:
            if p.exists():
                return p

    return None


def _iter_decisions_jsonl(symbol: str, max_n: int = 200000):
    """resolved decisions.jsonl を後ろから最大 max_n 件読む（安全）"""
    p = _resolve_decisions_log(symbol)
    if p is None or (not p.exists()):
        return
    lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
    for line in lines[-max_n:]:
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                yield rec
        except Exception:
            continue

