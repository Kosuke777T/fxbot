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

def _iter_decision_paths() -> "Iterable[Path]":
    """
    Yield decision log paths for Condition Mining.

    Sources may exist in multiple places:
    - logs/decisions_*.jsonl (v5.2 daily)
    - logs/decisions/*.jsonl (symbol-split)
    - logs/backtest/**/decisions.jsonl (backtest artifacts)

    Strategy:
    - collect all candidates
    - drop tiny files (likely empty) to avoid starving recent window
    - sort by mtime desc (newest first)
    """
    from pathlib import Path
    from typing import Iterable

    base = Path("logs")
    pats = [
        "decisions_*.jsonl",
        "decisions/*.jsonl",
        "backtest/**/decisions.jsonl",
    ]

    cand: list[Path] = []
    for pat in pats:
        cand.extend(base.glob(pat))

    # unique + exists
    uniq: list[Path] = []
    seen: set[str] = set()
    for x in cand:
        try:
            rp = x.resolve()
        except Exception:
            continue
        if not rp.exists():
            continue
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(rp)

    # drop tiny files (empirically: 171 bytes files exist)
    MIN_BYTES = 1024
    filtered = [x for x in uniq if x.stat().st_size >= MIN_BYTES]

    # if everything is tiny, keep originals (don't return empty)
    if not filtered:
        filtered = uniq

    # newest first
    filtered.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    for x in filtered:
        yield x


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


def get_decisions_window_summary(symbol: str, window: str | None = None, profile: Optional[str] = None, recent_minutes: int | None = None, past_minutes: int | None = None, past_offset_minutes: int | None = None, start: Optional[str] = None,
    end: Optional[str] = None,
    max_scan: int = 200_000,
    include_decisions: bool = False,
    max_decisions: int = 5000,
) -> Dict[str, Any]:
    # --- Step2-20: resolve minutes (caller args > store(profile) > default) ---
    # Why: get_decisions_window_summary is called directly (enrich path) and must reflect mt5_accounts.json
    # override/profile window settings, rather than falling back to 30/30/1440.
    #
    # NOTE:
    # - past_offset_minutes=0 は有効値（falsy 判定禁止）。None 判定のみで解決する。
    # - 既存API互換のため、引数はそのまま受け取りつつ「None を未指定」として扱う。
    DEFAULT_RECENT = 30
    DEFAULT_PAST = 30
    DEFAULT_OFFSET = 24 * 60

    _rm = recent_minutes
    _pm = past_minutes
    _om = past_offset_minutes

    if (_rm is None) or (_pm is None) or (_om is None):  # type: ignore[truthy-bool]
        try:
            store_win = mt5_account_store.get_condition_mining_window(profile=profile)
        except Exception:
            store_win = None

        if isinstance(store_win, dict):
            if _rm is None and store_win.get("recent_minutes") is not None:
                _rm = store_win.get("recent_minutes")
            if _pm is None and store_win.get("past_minutes") is not None:
                _pm = store_win.get("past_minutes")
            if _om is None and store_win.get("past_offset_minutes") is not None:
                _om = store_win.get("past_offset_minutes")

    # finalize (caller > store > default)
    recent_minutes = int(DEFAULT_RECENT if _rm is None else _rm)  # type: ignore[arg-type]
    past_minutes = int(DEFAULT_PAST if _pm is None else _pm)      # type: ignore[arg-type]
    past_offset_minutes = int(DEFAULT_OFFSET if _om is None else _om)  # type: ignore[arg-type]
    # --- end minutes resolve ---

    dt_start = _parse_iso_dt(start) if start else None
    dt_end = _parse_iso_dt(end) if end else None

    if dt_start is None and dt_end is None and window:
        # profile/override の window 設定を反映（明示指定が無い場合のデフォルトとして扱う）
        if (int(recent_minutes), int(past_minutes), int(past_offset_minutes)) == (30, 30, 24 * 60):
            try:
                w = get_condition_mining_window(profile=profile)
                if isinstance(w, dict):
                    if w.get("recent_minutes") is not None:
                        recent_minutes = int(w.get("recent_minutes"))
                    if w.get("past_minutes") is not None:
                        past_minutes = int(w.get("past_minutes"))
                    if w.get("past_offset_minutes") is not None:
                        past_offset_minutes = int(w.get("past_offset_minutes"))
            except Exception:
                pass

        now_dt = datetime.now(timezone.utc)
        start_dt, end_dt = resolve_window(
            window=window,
            now=now_dt,
            recent_minutes=recent_minutes,
            past_minutes=past_minutes,
            past_offset_minutes=past_offset_minutes,
        )
        dt_start, dt_end = start_dt, end_dt

    n = 0

    # min_stats counters (do not depend on include_decisions)

    _ms_total = 0

    _ms_pass = 0

    _ms_entry = 0
    min_dt: Optional[datetime] = None
    max_dt: Optional[datetime] = None
    scanned = 0
    sources: list[str] = []
    decisions: list[dict] = []  # optional (include_decisions)

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
            # ts 抽出（ログの揺れに強くする：既存APIの範囲で候補キーを増やす）
            # --- ensure action field for condition mining ---
            if isinstance(j, dict) and (not j.get('action')):
                fp = j.get('filter_pass', None)
                if fp is True:
                    j['action'] = 'ENTRY'
                elif fp is False:
                    j['action'] = 'BLOCKED'
                else:
                    j['action'] = 'HOLD'
            # --- end action ---

            ts = (
                _parse_iso_dt(j.get("ts_utc"))
                or _parse_iso_dt(j.get("ts_jst"))
                or _parse_iso_dt(j.get("timestamp"))
                or _parse_iso_dt(j.get("ts"))
                or _parse_iso_dt(j.get("time"))
                or _parse_iso_dt(j.get("time_utc"))
                or _parse_iso_dt(j.get("time_jst"))
                or _parse_iso_dt(j.get("datetime"))
                or _parse_iso_dt(j.get("dt"))
                or _parse_iso_dt(j.get("created_at"))
            )
            if ts is None:
                fts = j.get("filters") if isinstance(j.get("filters"), dict) else None
                ts = (
                    _parse_iso_dt((fts or {}).get("ts_utc"))
                    or _parse_iso_dt((fts or {}).get("ts_jst"))
                    or _parse_iso_dt((fts or {}).get("timestamp"))
                    or _parse_iso_dt((fts or {}).get("ts"))
                    or _parse_iso_dt((fts or {}).get("time"))
                    or _parse_iso_dt((fts or {}).get("time_utc"))
                    or _parse_iso_dt((fts or {}).get("time_jst"))
                    or _parse_iso_dt((fts or {}).get("datetime"))
                    or _parse_iso_dt((fts or {}).get("dt"))
                    or _parse_iso_dt((fts or {}).get("created_at"))
                )
            if ts is None:
                continue


            if dt_start and ts < dt_start:
                continue
            if dt_end and ts > dt_end:
                continue

            # CM_MIN_STATS_HIT_COUNTER: count stats on window-hit (independent of include_decisions)
            #
            # NOTE: ここで二重加算していたため n と min_stats.total がズレていた。
            # window-hit 1件につき 1回だけ加算する。
            _ms_total += 1
            fp = j.get('filter_pass')
            if fp is True:
                _ms_pass += 1
            # entry: prefer action, fallback to filter_pass
            if str(j.get('action','')).upper() == 'ENTRY' or (fp is True):
                _ms_entry += 1

            n += 1
            if include_decisions and len(decisions) < int(max_decisions):
                row = dict(j)
                # summarize側が拾いやすいよう timestamp を補完（ISO UTC）
                if not ((row.get("timestamp") or row.get("ts_jst")) or row.get("ts") or row.get("time")):
                    row["timestamp"] = ts.isoformat()
                decisions.append(row)




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
        "query_range": {
            "start": dt_start.isoformat() if dt_start else None,
            "end": dt_end.isoformat() if dt_end else None,
        },
        **({"decisions": decisions} if include_decisions else {}),
        'min_stats': {
            'total': int(_ms_total),
            'filter_pass_count': int(_ms_pass),
            'filter_pass_rate': float(_ms_pass) / float((_ms_total if _ms_total > 0 else 1)),
            'entry_count': int(_ms_entry),
            'entry_rate': float(_ms_entry) / float((_ms_total if _ms_total > 0 else 1)),
        },
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
    # --- Step2-20: resolve minutes (caller > store(profile) > default) ---
    # NOTE:
    # - 0 は有効値（falsy 判定で潰さない）。None のみ「未指定」。
    # - data層でも store の override/profile を反映させ、summary/enrich/ops の window を統一する。
    try:
        from app.services import mt5_account_store as _cm_store
    except Exception:
        _cm_store = None

    _rm = kwargs.pop("recent_minutes", None) if "recent_minutes" in kwargs else None
    _pm = kwargs.pop("past_minutes", None) if "past_minutes" in kwargs else None
    _om = kwargs.pop("past_offset_minutes", None) if "past_offset_minutes" in kwargs else None

    resolved = {"recent_minutes": 30, "past_minutes": 30, "past_offset_minutes": 24 * 60}

    if _cm_store is not None:
        try:
            w = _cm_store.get_condition_mining_window(profile=profile)
            if isinstance(w, dict):
                for k in ("recent_minutes", "past_minutes", "past_offset_minutes"):
                    if w.get(k) is not None:
                        try:
                            resolved[k] = int(w.get(k))
                        except Exception:
                            pass
        except Exception:
            pass

    if _rm is not None:
        try:
            resolved["recent_minutes"] = int(_rm)
        except Exception:
            pass
    if _pm is not None:
        try:
            resolved["past_minutes"] = int(_pm)
        except Exception:
            pass
    if _om is not None:
        try:
            resolved["past_offset_minutes"] = int(_om)
        except Exception:
            pass

    recent_minutes = int(resolved["recent_minutes"])
    past_minutes = int(resolved["past_minutes"])
    past_offset_minutes = int(resolved["past_offset_minutes"])
    # --- end minutes resolve ---

    recent = get_decisions_window_summary(
        recent_minutes=recent_minutes,
        past_minutes=past_minutes,
        past_offset_minutes=past_offset_minutes,

        symbol=symbol,
        window="recent",
        profile=profile,
        **kwargs,
    )
    past = get_decisions_window_summary(
        recent_minutes=recent_minutes,
        past_minutes=past_minutes,
        past_offset_minutes=past_offset_minutes,

        symbol=symbol,
        window="past",
        profile=profile,
        **kwargs,
    )

    # decisions/rows のキー名は実装依存なので両対応
    r_rows = (recent.get("decisions") or recent.get("rows") or [])
    p_rows = (past.get("decisions") or past.get("rows") or [])

    # min_stats は window_summary 側を優先（include_decisions=False でも集計できる）
    if not isinstance(recent.get("min_stats"), dict):
        recent["min_stats"] = _min_stats(r_rows)
    if not isinstance(past.get("min_stats"), dict):
        past["min_stats"] = _min_stats(p_rows)

    # ★★★ ここが今回の本丸 ★★★
    if (
        not recent.get("start_ts")
        or not recent.get("end_ts")
        or not past.get("start_ts")
        or not past.get("end_ts")
    ):
        sR, eR, sP, eP = _resolve_recent_past_window(
            now_utc=None,
            recent_minutes=recent_minutes,
            past_minutes=past_minutes,
            past_offset_minutes=past_offset_minutes,
        )
        if recent.get("start_ts") is None:
            recent["start_ts"] = sR.isoformat()
        if recent.get("end_ts") is None:
            recent["end_ts"] = eR.isoformat()

        if past.get("start_ts") is None:
            past["start_ts"] = sP.isoformat()
        if past.get("end_ts") is None:
            past["end_ts"] = eP.isoformat()

    # range を追加（v5.2仕様準拠）
    #
    # 重要: 「要求レンジ」と「データ実在レンジ」を混ぜない
    # - 要求レンジ: minutes（または caller start/end）から算出した query_range
    # - 実在レンジ: ログ実データの min/max（start_ts/end_ts）。0件なら None のまま
    recent["data_range"] = {"start": recent.get("start_ts"), "end": recent.get("end_ts")}
    past["data_range"] = {"start": past.get("start_ts"), "end": past.get("end_ts")}

    rq_r = (recent.get("query_range") or {})
    rq_p = (past.get("query_range") or {})
    recent["range"] = {"start": rq_r.get("start"), "end": rq_r.get("end")}
    past["range"] = {"start": rq_p.get("start"), "end": rq_p.get("end")}
    # --- Step2-11: window metadata for GUI (truth-only; derived from resolved minutes) ---
    out = {"recent": recent, "past": past}

    out.setdefault("evidence", {})
    out["evidence"]["window"] = {
        "mode": "recent_past",
        "recent_minutes": int(recent_minutes),
        "past_minutes": int(past_minutes),
        "past_offset_minutes": int(past_offset_minutes),
        "recent_range": {"start": recent["range"]["start"], "end": recent["range"]["end"]},
        "past_range": {"start": past["range"]["start"], "end": past["range"]["end"]},
    }
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
        out.setdefault("evidence", {})
        out["evidence"].update({k: v for k, v in ev.items() if not k.startswith("_")})
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
    past_minutes: int = 30,
    past_offset_minutes: int = 24 * 60,
):
    """recent/past の window をUTCで返すフォールバック"""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    end_recent = now_utc
    start_recent = end_recent - timedelta(minutes=int(recent_minutes))

    end_past = now_utc - timedelta(minutes=int(past_offset_minutes))
    start_past = end_past - timedelta(minutes=int(past_minutes))

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


def _summarize_decisions_list(decisions):
    """decisions(list[dict]) の軽量サマリ。例外は飲み込んで空サマリを返す。"""
    try:
        if not decisions:
            return {
                "n": 0,
                "ts_min": None,
                "ts_max": None,
                "keys_top": [],
                "symbol_dist": {},
            }

        # timestamp 抽出（ISO文字列 or datetime を想定）
        ts = []
        for d in decisions:
            if not isinstance(d, dict):
                continue
            v = (d.get("timestamp") or d.get("ts_jst")) or d.get("time") or d.get("ts")
            if v is not None:
                ts.append(str(v))

        # keys 頻度（上位のみ）
        from collections import Counter
        kc = Counter()
        sym = Counter()
        for d in decisions:
            if isinstance(d, dict):
                kc.update(list(d.keys()))
                s = d.get("symbol") or d.get("pair") or d.get("instrument")
                if s is not None:
                    sym.update([str(s)])

        keys_top = [{"key": k, "count": int(c)} for k, c in kc.most_common(20)]
        return {
            "n": int(len(decisions)),
            "ts_min": min(ts) if ts else None,
            "ts_max": max(ts) if ts else None,
            "keys_top": keys_top,
            "symbol_dist": dict(sym),
        }
    except Exception:
        return {
            "n": int(len(decisions)) if isinstance(decisions, list) else 0,
            "ts_min": None,
            "ts_max": None,
            "keys_top": [],
            "symbol_dist": {},
        }


def _check_window_consistency(win, label):
    """recent/past window(dict) の整合チェック。warnings(list[str]) を返す。"""
    w = []
    try:
        n = win.get("n")
        r = win.get("range") or {}
        start = r.get("start")
        end = r.get("end")
        decisions = win.get("decisions")

        if decisions is None:
            return w
        if n is not None and isinstance(decisions, list) and n != len(decisions):
            w.append(f"{label}:n_mismatch n={n} len={len(decisions)}")

        has_range = (start is not None) or (end is not None)
        if has_range and not decisions and (n not in (0, None)):
            w.append(f"{label}:range_exists_but_empty")

        if (not has_range) and decisions:
            w.append(f"{label}:decisions_exist_but_range_missing")

        s = _summarize_decisions_list(decisions)
        ts_min, ts_max = s.get("ts_min"), s.get("ts_max")

        # ISO文字列なら比較が効く（厳密datetime化は既存実装があればそちら優先）
        if start is not None and ts_min is not None and str(ts_min) < str(start):
            w.append(f"{label}:ts_min_before_start")
        if end is not None and ts_max is not None and str(end) < str(ts_max):
            w.append(f"{label}:ts_max_after_end")

    except Exception:
        w.append(f"{label}:consistency_check_failed")
    return w


def get_decisions_recent_past_window_info(symbol: str, profile=None, **kwargs):
    """
    Facade（不足している場合のみ追加）:
    recent/past の件数・期間（start/end）を summary から即取得。
    """
    out = get_decisions_recent_past_summary(symbol, profile=profile, **kwargs)
    return {
        "recent": {
            "n": out.get("recent", {}).get("n"),
            "range": out.get("recent", {}).get("range"),
            "data_range": out.get("recent", {}).get("data_range"),
        },
        "past": {
            "n": out.get("past", {}).get("n"),
            "range": out.get("past", {}).get("range"),
            "data_range": out.get("past", {}).get("data_range"),
        },
    }
def get_decisions_recent_past_min_stats(symbol: str, profile=None, **kwargs):
    """
    Facade（不足している場合のみ追加）:
    min_stats が summary に入っている前提で取り出す（なければ None）。
    """
    out = get_decisions_recent_past_summary(symbol, profile=profile, **kwargs)
    return {
        "recent": {"min_stats": out.get("recent", {}).get("min_stats")},
        "past": {"min_stats": out.get("past", {}).get("min_stats")},
    }


def get_condition_mining_ops_snapshot(symbol: str, profile=None, **kwargs):
    """
    Facade（不足している場合のみ追加）:
    Condition Mining 用の ops_snapshot を返す。
    既存 summary を土台に evidence を増量し、整合チェックの warnings を加える。
    """
    out = {
        "symbol": symbol,
        "warnings": [],
        "ops_cards_first": [],
        "evidence_kind": "decisions_summary",
        "evidence_src": "logs/decisions_*.jsonl",
        "evidence": {},
    }

    summary = get_decisions_recent_past_summary(symbol, profile=profile, **kwargs)

    # summary 側の縮退情報（warnings/ops_cards）を ops_snapshot に引き継ぐ
    if isinstance(summary, dict):
        sw = summary.get("warnings") or []
        if isinstance(sw, list):
            out["warnings"].extend([str(x) for x in sw])
        sc = summary.get("ops_cards") or []
        if isinstance(sc, list) and sc:
            # GUI は ops_cards_first を優先表示する想定
            out["ops_cards_first"] = sc


    # decisions リスト本体が summary に含まれない場合があるので、まずは range/n/min_stats をベースに evidence を組む
    recent = summary.get("recent", {}) or {}
    past = summary.get("past", {}) or {}

    # もし summary が decisions を持っていたら、keys/symbol_dist なども出せる
    recent_decisions = recent.get("decisions") if "decisions" in recent else None
    past_decisions = past.get("decisions") if "decisions" in past else None
    recent_sum = _summarize_decisions_list(recent_decisions) if recent_decisions else {"n": recent.get("n", 0)}
    past_sum = _summarize_decisions_list(past_decisions) if past_decisions else {"n": past.get("n", 0)}

    out["warnings"].extend(_check_window_consistency(
        {"n": recent.get("n"), "range": recent.get("range"), "decisions": recent_decisions},
        "recent"
    ))
    out["warnings"].extend(_check_window_consistency(
        {"n": past.get("n"), "range": past.get("range"), "decisions": past_decisions},
        "past"
    ))

    # evidence 情報量改善
    out["evidence"] = {
        "symbol": symbol,
        "recent": {
            "n": recent.get("n"),
            "range": recent.get("range"),
            "min_stats": recent.get("min_stats"),
            "ts_min": recent_sum.get("ts_min"),
            "ts_max": recent_sum.get("ts_max"),
        },
        "past": {
            "n": past.get("n"),
            "range": past.get("range"),
            "min_stats": past.get("min_stats"),
            "ts_min": past_sum.get("ts_min"),
            "ts_max": past_sum.get("ts_max"),
        },
        "recent_keys_top": recent_sum.get("keys_top", []),
        "past_keys_top": past_sum.get("keys_top", []),
        "recent_symbol_dist": recent_sum.get("symbol_dist", {}),
        "past_symbol_dist": past_sum.get("symbol_dist", {}),
    }


    # --- Step2-9: enrich evidence with window decisions (real data) ---
    try:
        recent_ws = get_decisions_window_summary(symbol=symbol, window="recent", profile=profile, include_decisions=True, **kwargs)
        past_ws   = get_decisions_window_summary(symbol=symbol, window="past",   profile=profile, include_decisions=True, **kwargs)

        recent_decisions = recent_ws.get("decisions") or []
        past_decisions   = past_ws.get("decisions") or []

        # 実window(start/end) を range に使って整合チェックを強化
        recent_range = (recent_ws.get("query_range") or {})
        past_range   = (past_ws.get("query_range") or {})

        recent_sum = _summarize_decisions_list(recent_decisions)
        past_sum   = _summarize_decisions_list(past_decisions)

        out["warnings"].extend(_check_window_consistency(
            {"n": len(recent_decisions), "range": {"start": recent_range.get("start"), "end": recent_range.get("end")}, "decisions": recent_decisions},
            "recent"
        ))
        out["warnings"].extend(_check_window_consistency(
            {"n": len(past_decisions), "range": {"start": past_range.get("start"), "end": past_range.get("end")}, "decisions": past_decisions},
            "past"
        ))

        # evidence を “実体” で上書き（ts_min/ts_max/keys_top/symbol_dist）
        out["evidence"]["recent"].update({
            "ts_min": recent_sum.get("ts_min"),
            "ts_max": recent_sum.get("ts_max"),
        })
        out["evidence"]["past"].update({
            "ts_min": past_sum.get("ts_min"),
            "ts_max": past_sum.get("ts_max"),
        })

        out["evidence"]["recent_keys_top"] = recent_sum.get("keys_top", [])
        out["evidence"]["past_keys_top"]   = past_sum.get("keys_top", [])
        out["evidence"]["recent_symbol_dist"] = recent_sum.get("symbol_dist", {})
        out["evidence"]["past_symbol_dist"]   = past_sum.get("symbol_dist", {})

        # デバッグ用：先頭だけ（重くしない）
        out["evidence"]["recent"]["sample"] = recent_decisions[:3]
        out["evidence"]["past"]["sample"]   = past_decisions[:3]
        # --- Step2-10: window metadata for GUI (truth-only, no display text) ---
        win = ((summary.get("evidence") or {}).get("window") or {})
        out["evidence"]["window"] = {
            "mode": win.get("mode") or "recent_past",
            "recent_minutes": int(win.get("recent_minutes") or 0),
            "past_minutes": int(win.get("past_minutes") or 0),
            "past_offset_minutes": int(win.get("past_offset_minutes") or 0),
            "recent_range": (win.get("recent_range") or {}),
            "past_range": (win.get("past_range") or {}),
        }
        # recent/past が 0 件の場合：ウィンドウ不一致の可能性が高いので、全期間（上限つき）で evidence を埋める
        if len(recent_decisions) == 0 and len(past_decisions) == 0:
            all_ws = get_decisions_window_summary(symbol=symbol, window=None, profile=profile, include_decisions=True, **kwargs)
            all_decisions = all_ws.get("decisions") or []
            if all_decisions:
                all_sum = _summarize_decisions_list(all_decisions)
                out["warnings"].append("no_decisions_in_recent_past_used_all")
                # Step2-10: tell UI this is an all-fallback situation (range mismatch signal)
                out["warnings"].append("window_range_mismatch")
                out["evidence"].setdefault("window", {})
                out["evidence"]["window"]["mode"] = "all_fallback"
                out["evidence"]["window"]["fallback_reason"] = "recent/past empty -> used all(window=None)"

                out["evidence"]["all"] = {
                    "n": int(len(all_decisions)),
                    "ts_min": all_sum.get("ts_min"),
                    "ts_max": all_sum.get("ts_max"),
                    "sample": all_decisions[:3],
                }
                out["evidence"]["all_keys_top"] = all_sum.get("keys_top", [])
                out["evidence"]["all_symbol_dist"] = all_sum.get("symbol_dist", {})

    except Exception as e:
        out["warnings"].append(f"evidence_enrich_failed:{type(e).__name__}")

    return out

