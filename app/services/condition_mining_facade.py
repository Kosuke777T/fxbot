from __future__ import annotations

from app.services.condition_mining_data import get_decisions_window_summary, build_ops_cards_for_zero_decisions
from datetime import datetime, timezone

from typing import Any, Dict, List, Optional

from app.services.condition_mining_data import get_decisions_recent_past_summary
from app.services import mt5_account_store


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

        # CM_FACADE_INJECT_MIN_STATS: prefer data-layer min_stats over facade defaults

        out = {
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

        try:

            from app.services.condition_mining_data import get_decisions_window_summary

            _r = get_decisions_window_summary(symbol, window='recent', profile=profile, include_decisions=False)

            _p = get_decisions_window_summary(symbol, window='past', profile=profile, include_decisions=False)

            _rms = (_r or {}).get('min_stats') or {}

            _pms = (_p or {}).get('min_stats') or {}

            if isinstance(out.get('recent'), dict):

                out['recent']['min_stats'] = _rms if isinstance(_rms, dict) else {}

            if isinstance(out.get('past'), dict):

                out['past']['min_stats'] = _pms if isinstance(_pms, dict) else {}

        except Exception:

            pass

        return out

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


def get_condition_candidates(symbol: str, top_n: int = 10, profile=None, **kwargs):
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
def get_condition_mining_ops_snapshot(
    symbol: str,
    profile: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Ops向け: condition mining の状態スナップショット。
    window 設定は profile → kwargs → fallback の順で解決する。
    """

    # --- Step2-11: resolve window BEFORE data access ---
    if (
        "recent_minutes" not in kwargs
        and "past_minutes" not in kwargs
        and "past_offset_minutes" not in kwargs
    ):
        win = mt5_account_store.get_condition_mining_window(profile=profile)
        if isinstance(win, dict):
            if win.get("recent_minutes") is not None:
                kwargs["recent_minutes"] = win.get("recent_minutes")
            if win.get("past_minutes") is not None:
                kwargs["past_minutes"] = win.get("past_minutes")
            if win.get("past_offset_minutes") is not None:
                kwargs["past_offset_minutes"] = win.get("past_offset_minutes")

    # --- main data ---
    out = get_decisions_recent_past_summary(
        symbol=symbol,
        profile=profile,
        **kwargs,
    )

    recent = out.get("recent") or {}
    past = out.get("past") or {}

    rn = int(recent.get("n") or 0)
    pn = int(past.get("n") or 0)

    warnings: list[str] = []
    ops_cards_first: list[dict] = []

    if rn == 0 and pn == 0:
        warnings.append("no_decisions_in_recent_and_past")

        insp = _inspect_decisions_log_dir(symbol)
        files_n = int((insp or {}).get("files") or 0)
        latest_file = (insp or {}).get("latest_file")
        latest_mtime = (insp or {}).get("latest_mtime")
        latest_size = (insp or {}).get("latest_size")

        bullets: list[str] = []
        if files_n <= 0:
            bullets.append(
                "decisions_*.jsonl が存在しません（稼働停止/出力設定/権限/パスの可能性）"
            )
        else:
            bullets.append(
                f"decisions_*.jsonl は {files_n} 件 見つかりました（最新: {latest_file} size={latest_size} mtime={latest_mtime}）"
            )
            bullets.append(
                "ただし recent/past の時間窓内に 0 件です（稼働停止・時刻窓・タイムゾーン・ログ遅延などの可能性）"
            )

        ops_cards_first.append(
            {
                "title": "decisions が 0 件です（原因の推定）",
                "summary": f"symbol={symbol} で recent/past ともに decisions=0 のため、探索AIは縮退動作中です。",
                "bullets": bullets,
            }
        )

    return {
        "symbol": symbol,
        "warnings": warnings,
        "ops_cards_first": ops_cards_first,
        "evidence": out.get("evidence"),
        "evidence_kind": out.get("evidence_kind"),
        "evidence_src": out.get("evidence_src"),
    }

# ==============================
# Condition Mining window settings (profile-scoped)
# ==============================
def get_condition_mining_window_settings(profile=None):
    """
    Facade: Condition Mining の recent/past window 設定を取得（profile別）。
    """
    return mt5_account_store.get_condition_mining_window(profile=profile)


def set_condition_mining_window_settings(patch, profile=None):
    """
    Facade: Condition Mining の recent/past window 設定を更新（profile別）。
    patch は部分更新可。
    """
    return mt5_account_store.set_condition_mining_window(patch=patch, profile=profile)

# --- v5.2 wiring: safe wrappers for condition mining facade ---
# --- v5.2 wiring: safe wrappers for condition mining facade ---
# NOTE:
# - 既存実装を壊さず、返却の「形」だけを固定するための末尾ラッパ。
# - recent_delta の実値は次ステップで埋める（ここではキーだけ保証する）。

def _cm__ensure_candidate_shape(cands):
    """
    Ops/Facade で要求される最低限キーを強制付与する（形を先に固定）。
    - score / stable / reasons / recent_delta
    """
    if not isinstance(cands, list):
        return []
    out = []
    for d in cands:
        if not isinstance(d, dict):
            continue
        d.setdefault("score", None)
        d.setdefault("stable", None)
        d.setdefault("reasons", [])
        d.setdefault("recent_delta", None)  # Step2-20 配線未完のため、まずキーを固定
        out.append(d)
    return out


def _build_condition_mining_adoption(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """
    Step2-22: Condition Mining の「採択(adoption)」を構築する（services層のみ）。

    方針:
    - candidates の先頭から、ゲートに落ちない最初の候補を採択する
    - past-only fallback（recent_empty_use_past_only）が出ている場合は safety shrink:
      weight=0.5 / confidence_cap="MID" を付与
    - degradation=True は基本 reject
    - condition_confidence="LOW" は基本 reject
    """
    warnings = snapshot.get("warnings") or []
    past_only = isinstance(warnings, list) and ("recent_empty_use_past_only" in warnings)

    weight = 0.5 if past_only else 1.0
    confidence_cap = "MID" if past_only else None

    rejected: List[Dict[str, Any]] = []
    adopted: Optional[Dict[str, Any]] = None

    cands = snapshot.get("candidates") or snapshot.get("condition_candidates") or []
    if not isinstance(cands, list):
        cands = []

    def _cap_conf(conf: Any) -> Any:
        if confidence_cap == "MID" and isinstance(conf, str) and conf.upper() == "HIGH":
            return "MID"
        return conf

    for c in cands:
        if not isinstance(c, dict):
            continue

        cid = None
        cond = c.get("condition")
        if isinstance(cond, dict) and isinstance(cond.get("id"), str):
            cid = cond.get("id")
        elif isinstance(c.get("id"), str):
            cid = c.get("id")

        reason_codes: List[str] = []

        if c.get("degradation") is True:
            reason_codes.append("degradation_gate")

        conf = c.get("condition_confidence")
        if isinstance(conf, str) and conf.upper() == "LOW":
            reason_codes.append("confidence_low")

        if reason_codes:
            rejected.append({"id": cid, "reason_codes": reason_codes})
            continue

        adopted = {
            "id": cid,
            "weight": float(weight),
            "condition_confidence": _cap_conf(conf),
            "note": "past_only_fallback" if past_only else None,
        }
        break

    notes: List[str] = []
    if past_only:
        notes.append("adoption_past_only_fallback")

    return {
        "status": "adopted" if adopted else "none",
        "adopted": adopted,
        "rejected": rejected,
        "weight": float(weight),
        "confidence_cap": confidence_cap,
        "notes": notes,
    }


# --- wrap get_condition_candidates (if exists) ---
try:
    _cm__orig_get_condition_candidates = get_condition_candidates  # type: ignore[name-defined]
except Exception:
    _cm__orig_get_condition_candidates = None

if callable(_cm__orig_get_condition_candidates):
    def get_condition_candidates(*args, **kwargs):  # type: ignore[override]
        out = _cm__orig_get_condition_candidates(*args, **kwargs)
        # dict で返す実装にも対応
        if isinstance(out, dict):
            c = out.get("candidates")
            if isinstance(c, list):
                out["candidates"] = _cm__ensure_candidate_shape(c)
            return out
        return _cm__ensure_candidate_shape(out)


# --- wrap get_condition_mining_ops_snapshot (if exists) ---
try:
    _cm__orig_get_ops_snapshot = get_condition_mining_ops_snapshot  # type: ignore[name-defined]
except Exception:
    _cm__orig_get_ops_snapshot = None

if callable(_cm__orig_get_ops_snapshot):
    def get_condition_mining_ops_snapshot(*args, **kwargs):  # type: ignore[override]
        out = _cm__orig_get_ops_snapshot(*args, **kwargs)
        if not isinstance(out, dict):
            return out
        # candidates が無ければ後付け（read-only）
        if "candidates" not in out:
            try:
                # symbol は kwargs / 位置引数の両方から解決（smoke の呼び方差分に耐える）
                _sym = None
                try:
                    if "symbol" in kwargs and kwargs.get("symbol") is not None:
                        _sym = kwargs.get("symbol")
                    elif len(args) >= 1 and args[0] is not None:
                        _sym = args[0]
                except Exception:
                    _sym = None
                cands = get_condition_candidates(symbol=(_sym or "USDJPY-"), top_n=10)
                if isinstance(cands, dict):
                    out["candidates"] = cands.get("candidates", [])
                    # T-43-4 Step1: top_candidates の件数根拠（top_k）を ops_snapshot に加法で同梱
                    # - 既存キーは壊さない（setdefault）
                    try:
                        out.setdefault("top_k", int(cands.get("top_k") or cands.get("top_n") or 10))
                    except Exception:
                        out.setdefault("top_k", 10)
                else:
                    out["candidates"] = cands
            except Exception:
                out["candidates"] = []
        # 形だけは必ず固定
        if isinstance(out.get("candidates"), list):
            out["candidates"] = _cm__ensure_candidate_shape(out.get("candidates"))

        # [CM_KEYS_MIRROR] unify candidate keys without breaking compatibility
        try:
            if "candidates" not in out and "condition_candidates" in out:
                out["candidates"] = out.get("condition_candidates") or []
            if "condition_candidates" not in out and "candidates" in out:
                out["condition_candidates"] = out.get("candidates") or []
        except Exception:
            pass

        # top_candidates（表示用サマリ）を付与（既存キーは壊さない）
        if "top_candidates" not in out:
            try:
                cands = out.get("candidates") or []
                if isinstance(cands, list) and cands and isinstance(cands[0], dict):
                    # T-43-4 Step1: top_k 件を常設（順序は candidates の並びを尊重。再ソートしない）
                    try:
                        top_k = int(out.get("top_k") or 10)
                    except Exception:
                        top_k = 10
                    if top_k < 0:
                        top_k = 0
                    top = cands[:top_k]
                    out["top_candidates"] = [
                        {
                            "id": (
                                c.get("id")
                                or (
                                    ((c.get("condition") or {}).get("id"))
                                    if isinstance(c.get("condition"), dict)
                                    else None
                                )
                            ),
                            "description": (
                                c.get("description")
                                or (
                                    ((c.get("condition") or {}).get("description"))
                                    if isinstance(c.get("condition"), dict)
                                    else ""
                                )
                            ),
                            "score": (c.get("score") if c.get("score") is not None else c.get("weight")),
                            "support": c.get("support"),
                            "condition_confidence": c.get("condition_confidence"),
                            "degradation": c.get("degradation"),
                            "tags": (
                                c.get("tags")
                                if isinstance(c.get("tags"), list)
                                else (
                                    ((c.get("condition") or {}).get("tags"))
                                    if isinstance(c.get("condition"), dict)
                                    else []
                                )
                            ),
                        }
                        for c in top
                    ]
                else:
                    out["top_candidates"] = []
            except Exception:
                out["top_candidates"] = []

        # adoption（採択）を付与（既存キーは壊さない）
        if "adoption" not in out:
            try:
                out["adoption"] = _build_condition_mining_adoption(out)
                # adoption の note を warnings にも載せる（加法）
                for n in (out.get("adoption") or {}).get("notes") or []:
                    if isinstance(n, str) and n not in (out.get("warnings") or []):
                        out.setdefault("warnings", []).append(n)
            except Exception:
                out["adoption"] = {
                    "status": "none",
                    "adopted": None,
                    "rejected": [],
                    "weight": 1.0,
                    "confidence_cap": None,
                    "notes": ["adoption_failed"],
                }

        # --- T-43-4 Step2-B: adoption rationale ops card (add-only / no re-sort) ---
        # 方針:
        # - ops_cards_first に「採択理由カード」1枚を追加（既存カード削除なし）
        # - adopted は adoption.adopted を優先。説明/スコア等は top_candidates から補完（追加のみ）
        # - not_adopted は top_candidates[1:4] を “候補として見えていたが採択されなかった” として列挙（再ソートなし）
        # - 文章生成はしない：存在するキーだけを key=value で列挙
        try:
            ops_cards = out.get("ops_cards_first")
            if isinstance(ops_cards, list):
                _already = False
                for _c in ops_cards:
                    if not isinstance(_c, dict):
                        continue
                    # 既に同等カードがあれば二重挿入しない（kind 優先、title も補助）
                    if _c.get("kind") == "condition_mining_adoption_rationale":
                        _already = True
                        break
                    if _c.get("title") == "採択理由（Condition Mining）":
                        _already = True
                        break
                if not _already:
                    tc = out.get("top_candidates")
                    if not isinstance(tc, list):
                        tc = []

                    ad = out.get("adoption")
                    adopted = None
                    adopted_id = None
                    if isinstance(ad, dict) and isinstance(ad.get("adopted"), dict):
                        adopted = dict(ad.get("adopted") or {})
                        adopted_id = adopted.get("id")

                    # enrich adopted from top_candidates (same id) without re-sort
                    if isinstance(adopted, dict) and isinstance(adopted_id, str) and adopted_id:
                        for _t in tc:
                            if isinstance(_t, dict) and _t.get("id") == adopted_id:
                                for k in (
                                    "id",
                                    "description",
                                    "tags",
                                    "score",
                                    "support",
                                    "condition_confidence",
                                    "degradation",
                                ):
                                    adopted.setdefault(k, _t.get(k))
                                break

                    # fallback: top_candidates[0]
                    if not isinstance(adopted, dict):
                        if tc and isinstance(tc[0], dict):
                            adopted = dict(tc[0])

                    def _fmt_kv(d: dict, keys: list[str]) -> list[str]:
                        xs: list[str] = []
                        for k in keys:
                            if k in d:
                                xs.append(f"{k}={d.get(k)}")
                        return xs

                    if isinstance(adopted, dict):
                        # list only existing fields (no prose)
                        keys_main = [
                            "id",
                            "description",
                            "tags",
                            "score",
                            "support",
                            "condition_confidence",
                            "degradation",
                            "weight",
                            "note",
                        ]
                        bullets: list[str] = []
                        bullets.append("adopted: " + " / ".join(_fmt_kv(adopted, keys_main)))

                        not_adopted: list[dict] = []
                        for _t in tc[1:4]:
                            if not isinstance(_t, dict):
                                continue
                            not_adopted.append(
                                {
                                    "id": _t.get("id"),
                                    "description": _t.get("description"),
                                    "facts": _fmt_kv(
                                        _t,
                                        [
                                            "score",
                                            "support",
                                            "condition_confidence",
                                            "degradation",
                                            "tags",
                                        ],
                                    ),
                                }
                            )
                        for i, _r in enumerate(not_adopted, start=1):
                            bullets.append(
                                f"not_adopted[{i}]: "
                                + " / ".join(
                                    [
                                        f"id={_r.get('id')}",
                                        f"description={_r.get('description')}",
                                        f"facts={_r.get('facts')}",
                                    ]
                                )
                            )

                        # ops_cards_first は既存互換（title/summary/bullets/evidence）を維持しつつ、kind を固定する
                        card = {
                            "kind": "condition_mining_adoption_rationale",
                            "title": "採択理由（Condition Mining）",
                            "summary": "adopted / not_adopted の観測値（key=value）を列挙",
                            "bullets": list(bullets),
                            "caveats": [],
                            "evidence": {
                                "adopted": {k: adopted.get(k) for k in keys_main if k in adopted},
                                "not_adopted": not_adopted,
                            },
                        }
                        # ops sees it first; keep existing cards intact
                        ops_cards.insert(0, card)
        except Exception:
            # ops card は説明情報。ここで落ちないよう縮退。
            pass
        # --- end: adoption rationale ops card ---
        return out
