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
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """
    表示用：proba CSV から AI 出力（prob_buy 等）を返す。
    - proba CSV: data/<symbol>/lgbm/<symbol>_M5_proba.csv
    - start_time/end_time が指定された場合、その範囲を返す
    - 既存ログを「読むだけ」（売買ロジックには影響しない）
    """
    sym = str(symbol or "USDJPY-").strip()
    symbol_tag = sym.rstrip("-").upper().strip()
    n = int(count or 0)
    n = max(10, min(n, 2000))
    want_keys = [str(k) for k in (keys or []) if str(k).strip()]
    if not want_keys:
        want_keys = ["prob_buy"]

    # proba CSVのパスを取得
    from app.services import data_guard
    proba_dir = data_guard.csv_path(symbol_tag=symbol_tag, timeframe="M5", layout="per-symbol").parent.parent / "lgbm"
    proba_csv_path = proba_dir / f"{symbol_tag}_M5_proba.csv"

    if not proba_csv_path.exists():
        return {"ok": False, "reason": "proba_csv_missing", "symbol": sym, "path": str(proba_csv_path)}

    try:
        # proba CSVを読み込む
        df_proba = pd.read_csv(proba_csv_path, parse_dates=["time"])
        if df_proba.empty or "time" not in df_proba.columns:
            return {"ok": False, "reason": "proba_csv_empty", "symbol": sym, "path": str(proba_csv_path)}

        # ★ 読み取り直後に必ずtimeで昇順ソート（mergesort: 安定ソート）
        df_proba = df_proba.sort_values("time", kind="mergesort").reset_index(drop=True)

        # model_idを取得（active_model.jsonから、または最新時刻の行から）
        current_model_id: str | None = None
        try:
            from app.services.ai_service import load_active_model_meta
            meta = load_active_model_meta()
            model_path = meta.get("model_path")
            if not model_path:
                file = meta.get("file")
                if file:
                    model_path = f"models/{file}"
            if model_path:
                from pathlib import Path
                current_model_id = Path(model_path).stem
            else:
                file = meta.get("file")
                if file:
                    from pathlib import Path
                    current_model_id = Path(file).stem
        except Exception:
            pass

        # current_model_idが取得できない場合は、最新時刻（max(time)）の行のmodel_idを使用
        if current_model_id is None and "model_id" in df_proba.columns and not df_proba.empty:
            t_max = df_proba["time"].max()
            df_latest = df_proba[df_proba["time"] == t_max]
            if not df_latest.empty:
                current_model_id = str(df_latest["model_id"].iloc[-1])

        # 現在のmodel_idの行だけを抽出（世代管理、ソート済みdfで実行）
        if current_model_id and "model_id" in df_proba.columns:
            df_proba = df_proba[df_proba["model_id"] == current_model_id].copy()

        # start_time/end_timeで範囲を絞る（ソート済みdfで実行）
        if start_time is not None:
            df_proba = df_proba[df_proba["time"] >= pd.Timestamp(start_time)].copy()
        if end_time is not None:
            df_proba = df_proba[df_proba["time"] <= pd.Timestamp(end_time)].copy()

        # count指定時は末尾n件（ソート済みdfなので正しく動作）
        if start_time is None and end_time is None:
            df_proba = df_proba.tail(n).copy()

        # 念のため再ソート（フィルタ後もtime昇順を保証）
        df_proba = df_proba.sort_values("time", kind="mergesort").reset_index(drop=True)

        # 観測ログ（任意だが推奨）
        if not df_proba.empty:
            t_min = df_proba["time"].min()
            t_max = df_proba["time"].max()
            logger.debug(
                "[viz] lgbm loaded: symbol={} rows={} time_range=[{}..{}] sorted=True",
                sym,
                len(df_proba),
                t_min,
                t_max,
            )

        # proba CSVからデータを抽出
        times: list[datetime] = []
        series: dict[str, list[float]] = {k: [] for k in want_keys}

        for _, row in df_proba.iterrows():
            ts = row["time"]
            if not isinstance(ts, pd.Timestamp):
                ts = pd.to_datetime(ts)
            times.append(ts.to_pydatetime())

            for k in want_keys:
                if k in df_proba.columns:
                    v = row[k]
                    series[k].append(float(v) if pd.notna(v) else 0.0)
                else:
                    # 欠損は0で埋める
                    series[k].append(0.0)

        if not times:
            logger.info(
                "[viz] lgbm rows=0; symbol={} path={} start_time={} end_time={}",
                sym,
                str(proba_csv_path),
                start_time,
                end_time,
            )

        keys_seen = sorted([k for k in want_keys if k in df_proba.columns])
        return {
            "ok": True,
            "symbol": sym,
            "symbol_used": sym,
            "path": str(proba_csv_path),
            "rows": len(times),
            "keys": want_keys,
            "keys_seen": keys_seen,
            "time": times,
            "series": series,
        }
    except Exception as e:
        return {"ok": False, "reason": f"proba_csv_read_failed: {e}", "symbol": sym, "path": str(proba_csv_path)}


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

