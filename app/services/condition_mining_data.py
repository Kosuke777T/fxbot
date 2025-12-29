from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import csv
from typing import Any, Dict, Iterable, Optional

from app.services import decision_log


def _parse_iso_dt(s: Any) -> Optional[datetime]:
    """Parse ISO-ish datetime and normalize to timezone-aware UTC.

    - If tzinfo is missing (naive), assume UTC.
    - If tzinfo exists, convert to UTC.
    """
    if not s:
        return None
    if isinstance(s, datetime):
        dt = s
    elif isinstance(s, str):
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
    else:
        return None

    # normalize to UTC-aware
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc)
    except Exception:
        return dt

def _iter_decision_paths() -> Iterable[Path]:
    root = decision_log._get_decision_log_dir()
    if not root.exists():
        return []
    return sorted(root.glob("decisions_*.jsonl"))


def resolve_window(
    window: str | None,
    now: datetime | None = None,
    recent_minutes: int = 30,
    past_minutes: int = 30,
    past_offset_minutes: int = 24 * 60,
) -> tuple[datetime | None, datetime | None]:
    # recent: [now-recent_minutes, now]
    # past  : [now-past_offset_minutes-past_minutes, now-past_offset_minutes]
    if not window:
        return (None, None)

    w = str(window).strip().lower()
    now_dt = now or datetime.now(timezone.utc)

    if w == "recent":
        end = now_dt
        start = end - timedelta(minutes=int(recent_minutes))
        return (start, end)

    if w == "past":
        end = now_dt - timedelta(minutes=int(past_offset_minutes))
        start = end - timedelta(minutes=int(past_minutes))
        return (start, end)

    return (None, None)


def get_decisions_window_summary(
    symbol: str,
    window: str | None = None,
    profile: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_scan: int = 200_000,
) -> Dict[str, Any]:
    dt_start = _parse_iso_dt(start) if start else None
    dt_end = _parse_iso_dt(end) if end else None

    if dt_start is None and dt_end is None and window:
        ws, we = resolve_window(window)
        dt_start, dt_end = ws, we

    n = 0
    min_dt: Optional[datetime] = None
    max_dt: Optional[datetime] = None
    scanned = 0
    sources: list[str] = []

    for path in _iter_decision_paths():
        sources.append(str(path))
        for j in decision_log._iter_jsonl(path):
            scanned += 1
            if scanned > max_scan:
                break

            if j.get("symbol") != symbol:
                continue

            if profile is not None:
                p = j.get("profile")
                if p is not None and p != profile:
                    continue

            ts = (_parse_iso_dt(j.get("ts_utc")) or _parse_iso_dt(j.get("ts_jst")) or _parse_iso_dt(j.get("timestamp")))
            if ts is None:
                fts = j.get("filters") if isinstance(j.get("filters"), dict) else None
                ts = (_parse_iso_dt((fts or {}).get("ts_utc")) or _parse_iso_dt((fts or {}).get("ts_jst")) or _parse_iso_dt((fts or {}).get("timestamp")))
            if ts is None:
                continue

            if dt_start and ts < dt_start:
                continue
            if dt_end and ts > dt_end:
                continue

            n += 1
            if min_dt is None or ts < min_dt:
                min_dt = ts
            if max_dt is None or ts > max_dt:
                max_dt = ts

        if scanned > max_scan:
            break

    return {
        "symbol": symbol,
        "profile": profile,
        "n": n,
        "start_ts": min_dt.isoformat() if min_dt else None,
        "end_ts": max_dt.isoformat() if max_dt else None,
        "sources": sources,
        "scanned": scanned,
        "max_scan": max_scan,
    }

def _try_extract_winrate_avgpnl_from_backtests(*, symbol: str) -> dict:
    """
    win_rate / avg_pnl を既存 backtest 成果物から“取れる範囲で”抽出する。
    優先: metrics.json -> trades*.csv
    - symbol は 'USDJPY-' 等（パスに含まれている場合のみ強く優先）
    - 取れなければ {} を返す（呼び出し側で縮退）
    """
    root = Path(".")

    def _norm(p: Path) -> str:
        return str(p).replace("\\", "/")

    def _is_relevant_path(s: str) -> bool:
        sym_a = symbol
        sym_b = symbol.replace("-", "")
        return (sym_a in s) or (sym_b in s)

    # 1) metrics.json（新しい順 / symbolを含むパス優先）
    try:
        metrics = sorted(root.rglob("metrics.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        metrics = sorted(metrics, key=lambda p: (not _is_relevant_path(_norm(p))), reverse=False)
        for p in metrics:
            s = _norm(p)
            if ("backtests/" not in s) and ("logs/backtest/" not in s) and ("backtest" not in s):
                continue
            try:
                obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue

            win_rate = obj.get("win_rate", None)
            avg_pnl  = obj.get("avg_pnl", None)

            if win_rate is None and "winrate" in obj:
                win_rate = obj.get("winrate")
            if avg_pnl is None and "mean_pnl" in obj:
                avg_pnl = obj.get("mean_pnl")

            out = {}
            if win_rate is not None:
                try:
                    out["win_rate"] = float(win_rate)
                except Exception:
                    pass
            if avg_pnl is not None:
                try:
                    out["avg_pnl"] = float(avg_pnl)
                except Exception:
                    pass

            if out:
                out["_src"] = s
                out["_kind"] = "metrics.json"
                return out
    except Exception:
        pass

    # 2) trades*.csv（新しい順 / symbolを含むパス優先）から計算
    try:
        trades = sorted(root.rglob("trades*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        trades = sorted(trades, key=lambda p: (not _is_relevant_path(_norm(p))), reverse=False)
        for p in trades:
            s = _norm(p)
            if ("backtests/" not in s) and ("logs/backtest/" not in s) and ("backtest" not in s):
                continue

            pnls = []
            with p.open("r", encoding="utf-8", errors="replace", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    if not row:
                        continue
                    v = row.get("pnl")
                    if v is None:
                        continue
                    try:
                        pnls.append(float(v))
                    except Exception:
                        continue

            if pnls:
                wins = sum(1 for x in pnls if x > 0)
                return {
                    "win_rate": float(wins / len(pnls)),
                    "avg_pnl": float(sum(pnls) / len(pnls)),
                    "_src": s,
                    "_kind": "trades.csv",
                }
    except Exception:
        pass

    return {}
def get_decisions_recent_past_summary(symbol: str, profile: Optional[str] = None, **kwargs) -> dict:
    """Aggregate recent/past windows and attach minimal stats."""
    recent = get_decisions_window_summary(
        symbol=symbol,
        window="recent",
        profile=profile,
        **kwargs,
    )
    past = get_decisions_window_summary(
        symbol=symbol,
        window="past",
        profile=profile,
        **kwargs,
    )

    # decisions/rows のキー名は実装依存なので両対応
    r_rows = (recent.get("decisions") or recent.get("rows") or [])
    p_rows = (past.get("decisions") or past.get("rows") or [])

    recent["min_stats"] = _min_stats(r_rows)
    past["min_stats"] = _min_stats(p_rows)

    # ★★★ ここが今回の本丸 ★★★
    if (
        not recent.get("start_ts")
        or not recent.get("end_ts")
        or not past.get("start_ts")
        or not past.get("end_ts")
    ):
        sR, eR, sP, eP = _resolve_recent_past_window()
        if recent.get("start_ts") is None:
            recent["start_ts"] = sR.isoformat()
        if recent.get("end_ts") is None:
            recent["end_ts"] = eR.isoformat()

        if past.get("start_ts") is None:
            past["start_ts"] = sP.isoformat()
        if past.get("end_ts") is None:
            past["end_ts"] = eP.isoformat()
    
    # range を追加（v5.2仕様準拠）
    recent["range"] = {
        "start": recent.get("start_ts"),
        "end": recent.get("end_ts"),
    }
    past["range"] = {
        "start": past.get("start_ts"),
        "end": past.get("end_ts"),
    }
    out = {"recent": recent, "past": past}

    # --- warnings / ops_cards の型固定（None禁止） ---
    warnings = []
    ops_cards = []

    rn = int((recent or {}).get("n") or 0)
    pn = int((past or {}).get("n") or 0)

    if rn == 0 and pn == 0:
        warnings.append("no_decisions_in_recent_and_past")
        ops_cards.append({
            "title": "decisions が 0 件です（原因の推定）",
            "summary": f"symbol={symbol} で recent/past ともに decisions=0 のため、探索AIは縮退動作中です。",
            "bullets": [
                "decisions_*.jsonl が存在しません（稼働停止/出力設定/権限/パスの可能性）"
            ],
        })

    out["warnings"] = warnings
    out["ops_cards"] = ops_cards
    # --- Step2 evidence（decisionsが無い場合は backtest 成果物から取る） ---
    ev = _try_extract_winrate_avgpnl_from_backtests(symbol=symbol)
    if ev:
        out["evidence"] = {k: v for k, v in ev.items() if not k.startswith("_")}
        out["evidence_src"] = ev.get("_src")
        out["evidence_kind"] = ev.get("_kind")

    return out
# --- T-42-3-18 Step 3: minimal window stats (recent/past) -----------------

def _min_stats(rows):
    """Compute minimal aggregate stats for a list of decision-like dicts.

    Returns:
      {
        total: int,
        filter_pass_count: int,
        filter_pass_rate: float,
        entry_count: int,
        entry_rate: float,
      }
    """
    if not rows:
        return {
            "total": 0,
            "filter_pass_count": 0,
            "filter_pass_rate": 0.0,
            "entry_count": 0,
            "entry_rate": 0.0,
        }

    total = 0
    pass_cnt = 0
    entry_cnt = 0

    for r in rows:
        if not isinstance(r, dict):
            continue
        total += 1
        if bool(r.get("filter_pass", False)):
            pass_cnt += 1
        if str(r.get("action", "")).upper() == "ENTRY":
            entry_cnt += 1

    # Avoid ZeroDivision
    denom = total if total > 0 else 1
    return {
        "total": int(total),
        "filter_pass_count": int(pass_cnt),
        "filter_pass_rate": float(pass_cnt) / float(denom),
        "entry_count": int(entry_cnt),
        "entry_rate": float(entry_cnt) / float(denom),
    }


def _resolve_recent_past_window(
    now_utc: datetime | None = None,
    recent_minutes: int = 30,
    past_days: int = 1,
):
    """recent/past の window をUTCで返すフォールバック"""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    end_recent = now_utc
    start_recent = now_utc - timedelta(minutes=recent_minutes)

    start_past = start_recent - timedelta(days=past_days)
    end_past = end_recent - timedelta(days=past_days)

    return start_recent, end_recent, start_past, end_past














# =========================
# T-43-3 Ops根拠カード（decisions=0 の理由推定）
# =========================

def _inspect_decision_logs(log_dir: str = "logs") -> dict:
    """
    decisionsログの存在・更新状況を軽く点検して「0件の理由」を推定するための材料を返す。
    - 依存を増やさず、filesystem 情報だけを見る（安全・縮退しやすい）
    """
    try:
        base = Path(log_dir)
        if not base.exists():
            return {"log_dir_exists": False, "files": 0, "latest_mtime": None, "latest_file": None}

        # decisions_*.jsonl を対象（運用に合わせる。過去仕様とも相性が良い）
        files = sorted(base.glob("decisions_*.jsonl"))
        if not files:
            return {"log_dir_exists": True, "files": 0, "latest_mtime": None, "latest_file": None}

        latest = max(files, key=lambda p: p.stat().st_mtime)
        st = latest.stat()
        return {
            "log_dir_exists": True,
            "files": int(len(files)),
            "latest_mtime": float(st.st_mtime),
            "latest_file": str(latest).replace("\\", "/"),
            "latest_size": int(st.st_size),
        }
    except Exception as e:
        return {"error": f"log_inspect_failed: {e}"}


def build_ops_cards_for_zero_decisions(symbol: str, recent: dict, past: dict) -> list[dict]:
    """
    decisions が 0 件のときに、Ops が原因を推定できるカードを返す。
    返却は GUI が描画しやすい固定形：title/summary/bullets/caveats
    """
    cards: list[dict] = []

    insp = _inspect_decision_logs("logs")

    # 主要推定（優先度順）
    bullets = []
    caveats = []

    if insp.get("error"):
        bullets.append("logs 点検が失敗しました（filesystem 由来の推定ができません）")
        caveats.append(insp.get("error"))
    else:
        if not insp.get("log_dir_exists", True):
            bullets.append("logs/ ディレクトリが見つかりません（ログ出力先の設定/起動フォルダを確認）")
        elif insp.get("files", 0) == 0:
            bullets.append("decisions_*.jsonl が存在しません（稼働停止/出力設定/権限/パスの可能性）")
        else:
            bullets.append(f"decisionsログは存在します（最新: {insp.get('latest_file')} size={insp.get('latest_size')}）")
            bullets.append("ただし recent/past ウィンドウに該当する行が 0 件です（期間・時刻・timezone の可能性）")

    # filter過多は “0件の原因” になり得るが、decisions自体が0なら断定できない → 注意書きで扱う
    caveats.append("decisions=0 の場合、フィルタ過多・稼働停止・データ欠損のどれもあり得ます（断定はしない）")
    caveats.append("まずは decisionsログの最終更新時刻と、実行系（常駐/GUI起動中）の状態を確認してください")

    cards.append({
        "kind": "ops_card",
        "title": "decisions が 0 件です（原因の推定）",
        "summary": f"symbol={symbol} で recent/past ともに decisions=0 のため、探索AIは縮退動作中です。",
        "bullets": bullets,
        "caveats": caveats,
        "evidence": {
            "symbol": symbol,
            "recent_n": int((recent or {}).get("n") or 0),
            "past_n": int((past or {}).get("n") or 0),
            "log_inspection": insp,
        }
    })

    return cards




