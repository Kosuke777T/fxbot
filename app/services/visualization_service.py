from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Optional

import pandas as pd
from loguru import logger

from app.core.config_loader import load_config
from app.services import data_guard


_VIZ_LGBM_EMPTY_LOGGED: set[tuple[str, str]] = set()


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _parse_iso_dt(x: Any) -> Optional[datetime]:
    if not isinstance(x, str) or not x.strip():
        return None
    try:
        # "2025-12-11T19:59:32+09:00" 等を想定
        return datetime.fromisoformat(x)
    except Exception:
        return None


def get_default_symbol_timeframe() -> dict[str, Any]:
    """
    GUI の初期値用（表示のみ）。
    - symbol: configs/config*.yaml の runtime.symbol を優先し、無ければ USDJPY-
    - timeframe: configs/config*.yaml の runtime.timeframe を優先し、無ければ M5
    """
    cfg = load_config() or {}
    rt = cfg.get("runtime", {}) if isinstance(cfg.get("runtime"), dict) else {}
    symbol = str(rt.get("symbol") or "USDJPY-")
    timeframe = str(rt.get("timeframe") or "M5")
    return {"symbol": symbol, "timeframe": timeframe}


def get_recent_ohlcv(
    *,
    symbol: str,
    timeframe: str,
    count: int = 120,
    until: datetime | None = None,
) -> dict[str, Any]:
    """
    表示用の OHLCV を返す。
    - 取得/整形は services 層で実施（GUI は描画だけ）
    - 現段階は既存資産（data/.../ohlcv CSV）を優先（新規依存追加なし）
    - until が None の場合: 直近 N 本
    - until が指定された場合: until より過去（time < until）の中から末尾 N 本
    """
    symbol_tag = str(symbol or "USDJPY").rstrip("-").upper().strip()
    tf = str(timeframe or "M5").upper().strip()
    n = int(count or 0)
    n = max(10, min(n, 2000))

    csvp = data_guard.csv_path(symbol_tag=symbol_tag, timeframe=tf, layout="per-symbol")
    if not csvp.exists():
        return {
            "ok": False,
            "reason": f"ohlcv_csv_missing: {csvp}",
            "symbol_tag": symbol_tag,
            "timeframe": tf,
            "source": "csv",
            "csv_path": str(csvp),
        }

    try:
        df = pd.read_csv(csvp)
        if "time" not in df.columns:
            return {
                "ok": False,
                "reason": f"ohlcv_csv_no_time: columns={list(df.columns)}",
                "symbol_tag": symbol_tag,
                "timeframe": tf,
                "source": "csv",
                "csv_path": str(csvp),
            }
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.dropna(subset=["time"])
        # 必要列が足りない場合でも、できる範囲で返す（UIを落とさない）
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("time")
        if isinstance(until, datetime):
            df = df[df["time"] < pd.Timestamp(until)]
        tail = df.tail(n)
        out = {
            "ok": True,
            "symbol_tag": symbol_tag,
            "timeframe": tf,
            "source": "csv",
            "csv_path": str(csvp),
            "rows": int(len(tail)),
            "time": [t.to_pydatetime() for t in tail["time"].tolist()],
        }
        for col in ["open", "high", "low", "close"]:
            out[col] = [(_safe_float(v) or 0.0) for v in (tail[col].tolist() if col in tail.columns else [])]
        return out
    except Exception as e:
        return {
            "ok": False,
            "reason": f"ohlcv_csv_read_failed: {e}",
            "symbol_tag": symbol_tag,
            "timeframe": tf,
            "source": "csv",
            "csv_path": str(csvp),
        }


def _latest_decisions_log_path(project_root: Optional[Path] = None) -> Optional[Path]:
    root = Path(".") if project_root is None else Path(project_root)
    logs_dir = root / "logs"
    if not logs_dir.exists():
        return None
    # decisions_YYYY-MM-DD.jsonl を最新日付優先（mtimeではなくファイル名で安定）
    files = sorted(logs_dir.glob("decisions_????-??-??.jsonl"), reverse=True)
    return files[0] if files else None


def _read_tail_lines(path: Path, *, max_bytes: int = 2_000_000) -> list[str]:
    try:
        size = path.stat().st_size
    except Exception:
        size = 0
    start = max(0, int(size) - int(max_bytes))
    try:
        with path.open("rb") as f:
            f.seek(start)
            data = f.read()
        # 途中から読むので先頭は行途中の可能性あり → 最初の改行まで捨てる
        if start > 0:
            nl = data.find(b"\n")
            if nl >= 0:
                data = data[nl + 1 :]
        text = data.decode("utf-8", errors="replace")
        return [ln for ln in text.splitlines() if ln.strip()]
    except Exception:
        return []


@dataclass
class _DecisionsCache:
    path: Optional[Path] = None
    mtime_ns: int = -1
    buf_by_symbol: Dict[str, Deque[dict]] = None  # type: ignore[assignment]
    keys_seen: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.buf_by_symbol = {}
        self.keys_seen = set()

    def reset(self) -> None:
        self.path = None
        self.mtime_ns = -1
        self.buf_by_symbol = {}
        self.keys_seen = set()

    def update_from_file(self, path: Path, *, max_per_symbol: int) -> None:
        try:
            st = path.stat()
            mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        except Exception:
            mtime_ns = -1

        # ファイルが変わったらリセット（1ファイルのみキャッシュ）
        if self.path != path or self.mtime_ns != mtime_ns:
            self.reset()
            self.path = path
            self.mtime_ns = mtime_ns

            lines = _read_tail_lines(path)
            for ln in lines:
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                sym = str(rec.get("symbol") or "")
                if not sym:
                    continue
                self.keys_seen.update([k for k in rec.keys() if isinstance(k, str)])
                dq = self.buf_by_symbol.get(sym)
                if dq is None:
                    dq = deque(maxlen=max_per_symbol)
                    self.buf_by_symbol[sym] = dq
                dq.append(rec)


_DECISIONS_CACHE = _DecisionsCache()


def get_recent_lgbm_series(
    *,
    symbol: str,
    count: int = 120,
    keys: Iterable[str] = ("prob_buy",),
) -> dict[str, Any]:
    """
    表示用：decisions_YYYY-MM-DD.jsonl から直近 N 件の AI 出力（prob_buy 等）を返す。
    - 既存ログを「読むだけ」（売買ロジックには影響しない）
    """
    sym = str(symbol or "USDJPY-").strip()
    n = int(count or 0)
    n = max(10, min(n, 2000))
    want_keys = [str(k) for k in (keys or []) if str(k).strip()]
    if not want_keys:
        want_keys = ["prob_buy"]

    path = _latest_decisions_log_path()
    if path is None or (not path.exists()):
        return {"ok": False, "reason": "decisions_log_missing", "symbol": sym, "path": None}

    try:
        _DECISIONS_CACHE.update_from_file(path, max_per_symbol=max(2000, n * 5))

        def _norm_symbol(s: str) -> str:
            return str(s or "").strip().upper().rstrip("-")

        def _parse_dt_fallback(rec: dict) -> Optional[datetime]:
            # ISO文字列候補
            for k in ("ts_jst", "ts", "time", "datetime", "timestamp", "ts_utc"):
                dt = _parse_iso_dt(rec.get(k))
                if dt is not None:
                    return dt
            # epoch候補（秒/ミリ秒）
            for k in ("ts_epoch", "ts_ms", "timestamp_ms", "epoch_ms", "epoch"):
                v = rec.get(k)
                if isinstance(v, (int, float)):
                    try:
                        vv = float(v)
                        if vv > 1e12:  # ms
                            vv = vv / 1000.0
                        return datetime.fromtimestamp(vv)
                    except Exception:
                        pass
            return None

        def _get_prob_value(rec: dict, key: str) -> Optional[float]:
            v = _safe_float(rec.get(key))
            if v is not None:
                return v
            for parent in ("probs", "lgbm", "ai", "model", "pred", "decision_detail"):
                obj = rec.get(parent)
                if isinstance(obj, dict):
                    vv = _safe_float(obj.get(key))
                    if vv is not None:
                        return vv
            # 観測: prob_* が null の場合があるため、利用可能なスコアをフォールバック
            if str(key) == "prob_buy":
                try:
                    dd = rec.get("decision_detail")
                    if isinstance(dd, dict):
                        vv = _safe_float(dd.get("ai_margin"))
                        if vv is not None:
                            return vv
                except Exception:
                    pass
            return None

        # 完全一致 → 正規化一致へ（USDJPY- / USDJPY 等の揺れ吸収）
        sym_norm = _norm_symbol(sym)
        symbol_used = sym  # 既定
        dq = _DECISIONS_CACHE.buf_by_symbol.get(sym)

        if dq is None:
            # cacheキーを総当りして norm一致するものを拾う
            for k in list(_DECISIONS_CACHE.buf_by_symbol.keys()):
                if _norm_symbol(k) == sym_norm:
                    dq = _DECISIONS_CACHE.buf_by_symbol.get(k)
                    symbol_used = k
                    break

        if dq is None:
            dq = deque()
            # 観測用：cacheに存在するsymbol一覧を出す（rows=0原因の確定に使う）
            try:
                avail = list(_DECISIONS_CACHE.buf_by_symbol.keys())
                logger.info(
                    "[viz] lgbm: no rows for symbol={} (norm={}); available_symbols={}",
                    sym,
                    sym_norm,
                    avail[:50],
                )
            except Exception:
                pass
        elif symbol_used != sym:
            # 観測用：揺れ吸収で実際に拾ったキーを記録
            try:
                logger.info("[viz] lgbm: symbol matched by norm: requested={} used={}", sym, symbol_used)
            except Exception:
                pass

        rows = list(dq)[-n:]
        times: list[datetime] = []
        series: dict[str, list[float]] = {k: [] for k in want_keys}

        for rec in rows:
            ts = _parse_dt_fallback(rec)
            if ts is None:
                continue
            ok_any = False
            vals: dict[str, float] = {}
            for k in want_keys:
                v = _get_prob_value(rec, k)
                if v is None:
                    continue
                vals[k] = float(v)
                ok_any = True
            if not ok_any:
                continue
            times.append(ts)
            for k in want_keys:
                if k in vals:
                    series[k].append(vals[k])
                else:
                    # 欠損は前値で埋める（表示を途切れさせない）
                    prev = series[k][-1] if series[k] else 0.0
                    series[k].append(prev)

        if not times:
            try:
                log_key = (sym_norm, str(path))
                if log_key not in _VIZ_LGBM_EMPTY_LOGGED:
                    _VIZ_LGBM_EMPTY_LOGGED.add(log_key)
                    sample = rows[-1] if rows else None
                    if isinstance(sample, dict):
                        cand = {
                            k: sample.get(k)
                            for k in (
                                "ts_jst",
                                "ts",
                                "time",
                                "datetime",
                                "timestamp",
                                "ts_utc",
                                "ts_epoch",
                                "ts_ms",
                                "timestamp_ms",
                                "epoch_ms",
                                "epoch",
                            )
                            if k in sample
                        }
                        dd = sample.get("decision_detail")
                        ai_margin = _safe_float(dd.get("ai_margin")) if isinstance(dd, dict) else None
                        logger.info(
                            "[viz] lgbm rows=0; symbol={} used={} path={} sample_ts_fields={} sample_prob_buy={} sample_ai_margin={} sample_keys_top30={}",
                            sym,
                            symbol_used,
                            str(path),
                            cand,
                            sample.get("prob_buy"),
                            ai_margin,
                            list(sample.keys())[:30],
                        )
                    else:
                        logger.info(
                            "[viz] lgbm rows=0; symbol={} used={} path={} (no sample row)",
                            sym,
                            symbol_used,
                            str(path),
                        )
            except Exception:
                pass

        keys_seen = sorted([k for k in (_DECISIONS_CACHE.keys_seen or set()) if isinstance(k, str)])
        return {
            "ok": True,
            "symbol": sym,
            "symbol_used": symbol_used,
            "path": str(path),
            "rows": len(times),
            "keys": want_keys,
            "keys_seen": keys_seen,
            "time": times,
            "series": series,
        }
    except Exception as e:
        return {"ok": False, "reason": f"decisions_log_read_failed: {e}", "symbol": sym, "path": str(path)}


def log_viz_info(*, ohlc_n: int, lgbm_keys: list[str], threshold: float, markers: int) -> None:
    try:
        logger.info(
            "[viz] ohlc_n={} lgbm_keys={} threshold={} markers={}",
            int(ohlc_n),
            list(lgbm_keys or []),
            float(threshold),
            int(markers),
        )
    except Exception:
        pass

