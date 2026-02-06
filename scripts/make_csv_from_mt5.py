# scripts/make_csv_from_mt5.py
"""
MT5 から USDJPY の M5/M15/H1 を 2020-11-01 以降で CSV 化し、
以降は不足分のみを自動追記するユーティリティ。

- 保存先: <プロジェクトルート>/data （相対指定でも最終的に絶対パスへ解決）
- タイムゾーン: JST（Asia/Tokyo）で time 列を naive datetime64[ns] として保存
- 既存CSVがあれば末尾時刻以降を自動で追記（重複は除去）
- シンボル接尾辞（例: USDJPY-）は自動解決
- GaitameFinest 等で copy_rates_range() が失敗する環境に対して
  copy_rates_from() の「現在→過去へページング」フォールバックを搭載
- 実行環境名は --env で明示でき、未指定時は HOST_MAP とヒューリスティックで推定
- 保存レイアウトは --layout で切替（flat | per-symbol）
"""

from __future__ import annotations
from app.core.symbol_map import resolve_symbol

import argparse
import os
import socket
import sys
import time
from datetime import UTC
from datetime import datetime as pdt
from zoneinfo import ZoneInfo

# ミチビキ内部の naive datetime は JST とみなす
JST = ZoneInfo("Asia/Tokyo")
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import MetaTrader5 as mt5
except Exception as e:
    raise SystemExit(
        "[fatal] MetaTrader5 パッケージが見つかりません。仮想環境で `pip install MetaTrader5 pandas` を実行してください。"
    ) from e


# =========================
# 設定（必要なら編集）
# =========================

SYMBOL_DEFAULT = "USDJPY"
TIMEFRAMES_DEFAULT = ["M5", "M15", "H1"]
START_DATE_DEFAULT = "2020-11-01"  # ここ以前は取得しない

# プロジェクトルート（このスクリプトの1つ上のディレクトリ）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# デフォルトのデータ保存ディレクトリ（最終的に PROJECT_ROOT/data に解決）
DATA_DIR_DEFAULT = "data"

# allow importing fxbot_path from project root
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from fxbot_path import get_data_root, get_ohlcv_csv_path
except Exception:
    # import error will be surfaced when running main; keep module import-safe
    get_data_root = None  # type: ignore
    get_ohlcv_csv_path = None  # type: ignore

# MT5 の timeframe 定数マップ
TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

# CSV カラム順（MT5の戻り値に準拠）
CSV_COLS = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
]

# フォールバック時の1回あたり取得本数と最大ループ回数（必要に応じて調整可能）
PAGE = 20000
MAX_LOOPS = 300

# CSVファイル名で使う "接尾辞なしタグ" を main() 内で設定
FILE_TAG: str | None = None

# === MT5 server time offset (seconds) =========================
# MT5のepochがPC時刻とズレている場合の補正値（秒）
SERVER_OFFSET_SEC: int = 0
_PREV_OFFSET_SEC: int = 0  # 前回値（監視用）

# ジャンプ抑制の閾値（必要なら調整）
_MAX_JUMP_SEC = 3600  # 1時間以上変わるなら採用しない
_MIN_ADOPT_SEC = 15 * 60  # 15分未満の差はノイズとして無視

def update_server_offset(symbol: str) -> int:
    """
    tick.time(=MT5 epoch) と PC epoch を比較し、SERVER_OFFSET_SEC を推定する。
    - 15分未満のズレは無視（ノイズ）
    - 推定値は「時間単位」に丸める
    - 前回値から1時間以上ジャンプする場合は採用しない（ログのみ）
    """
    global SERVER_OFFSET_SEC, _PREV_OFFSET_SEC
    try:
        import time
        now_epoch = int(time.time())
        tick = mt5.symbol_info_tick(resolve_symbol(symbol))
        if not tick or not tick.time:
            # 監視ログ：毎回 offset を出力
            log("[time_audit] tick is None -> keep SERVER_OFFSET_SEC={} prev={} delta=0".format(
                SERVER_OFFSET_SEC, _PREV_OFFSET_SEC
            ))
            return SERVER_OFFSET_SEC

        tick_epoch = int(tick.time)
        delta_sec = tick_epoch - now_epoch

        # ノイズ除去（小さい差は無視）
        if abs(delta_sec) < _MIN_ADOPT_SEC:
            # 監視ログ：毎回 offset を出力
            log("[time_audit] small delta_sec={} (<{}), keep SERVER_OFFSET_SEC={} prev={} delta=0".format(
                delta_sec, _MIN_ADOPT_SEC, SERVER_OFFSET_SEC, _PREV_OFFSET_SEC
            ))
            return SERVER_OFFSET_SEC

        # 時間単位に丸め
        delta_hours_round = int(round(delta_sec / 3600))
        candidate = delta_hours_round * 3600

        # ジャンプ抑制
        if SERVER_OFFSET_SEC != 0 and abs(candidate - SERVER_OFFSET_SEC) >= _MAX_JUMP_SEC:
            # 監視ログ：閾値超えの場合は WARNING
            offset_delta = candidate - SERVER_OFFSET_SEC
            log("[time_audit][WARNING] candidate jump too large: candidate={} prev={} delta={} (>= {}) -> ignore".format(
                candidate, SERVER_OFFSET_SEC, offset_delta, _MAX_JUMP_SEC
            ))
            return SERVER_OFFSET_SEC

        # 監視ログ：更新前の値を保存
        _PREV_OFFSET_SEC = SERVER_OFFSET_SEC
        offset_delta = candidate - SERVER_OFFSET_SEC
        SERVER_OFFSET_SEC = candidate

        # 監視ログ：毎回 offset を出力（更新時）
        if abs(offset_delta) >= 3600:
            log("[time_audit][WARNING] updated SERVER_OFFSET_SEC={} prev={} delta={} (>= 3600) (delta_sec={} hours~{})".format(
                SERVER_OFFSET_SEC, _PREV_OFFSET_SEC, offset_delta, delta_sec, delta_hours_round
            ))
        else:
            log("[time_audit] updated SERVER_OFFSET_SEC={} prev={} delta={} (delta_sec={} hours~{})".format(
                SERVER_OFFSET_SEC, _PREV_OFFSET_SEC, offset_delta, delta_sec, delta_hours_round
            ))
        return SERVER_OFFSET_SEC
    except Exception as e:
        log(f"[time_audit][warn] failed to update server offset: {e}")
        return SERVER_OFFSET_SEC


# =========================
# ユーティリティ
# =========================


def log(msg: str):
    host = os.environ.get("COMPUTERNAME", socket.gethostname())
    ts = pdt.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}][{host}] {msg}")


def ensure_mt5_initialized(terminal_path: str | None = None):
    """
    MT5 を初期化。terminal_path を指定すればその exe に紐づけ。
    Windows で MT5 が起動していなくても、通常は自動で起動・接続可能。
    """
    ok = mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
    if not ok:
        code, details = mt5.last_error()
        raise SystemExit(f"[fatal] MT5 initialize 失敗: {code} {details}")

    info = mt5.account_info()
    if info is None:
        log(
            "[warn] account_info() が None。未ログインの可能性。ターミナル側で口座ログインしてください。"
        )
    else:
        log(f"connected login={info.login} server={info.server} balance={info.balance}")


def jst_from_mt5_epoch(series):
    """
    MT5の 'time' (Unix秒) を JST の naive datetime64[ns] に変換。
    MT5が返すepochがUTCからズレている場合、SERVER_OFFSET_SECで補正する。
    series は pandas Series でも DatetimeIndex でも両対応。
    """
    s = pd.to_datetime(series, unit="s", utc=True)
    # MT5 epochがserver_offset分だけ「UTCとして誤って」進んでいる場合の補正
    if SERVER_OFFSET_SEC != 0:
        s = s - pd.Timedelta(seconds=SERVER_OFFSET_SEC)
    if isinstance(s, pd.DatetimeIndex):
        return s.tz_convert("Asia/Tokyo").tz_localize(None)
    else:
        return s.dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)


def merge_and_dedup(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """
    old と new を縦結合して time 重複を除去（後勝ち）→ time 昇順に揃える。
    """
    if old is None or old.empty:
        out = new.copy()
    else:
        out = pd.concat([old, new], axis=0, ignore_index=True)
        out = out.drop_duplicates(subset=["time"], keep="last")
    return out.sort_values("time").reset_index(drop=True)


# =========================
# データ取得コア
# =========================


def _to_utc_naive(ts: pd.Timestamp) -> pdt:
    """
    ミチビキ内の naive datetime は JST とみなす。
    MT5 API には UTC naive を渡す。
    """
    if ts.tzinfo is None:
        ts = ts.tz_localize(JST)
    ts_utc = ts.tz_convert("UTC")
    return ts_utc.to_pydatetime().replace(tzinfo=None)


def _to_local_naive(ts_jst: pd.Timestamp) -> pdt:
    """JST naive -> JST naive（ローカルnaiveを要求する環境向け）"""
    return ts_jst.to_pydatetime().replace(tzinfo=None)


def _to_utc_aware(ts_jst: pd.Timestamp) -> pdt:
    """JST naive -> UTC aware（timezone.utc）"""
    return (
        ts_jst.tz_localize("Asia/Tokyo")
        .tz_convert("UTC")
        .to_pydatetime()
        .replace(tzinfo=UTC)
    )


def _to_server_naive(ts_jst: pd.Timestamp) -> pdt:
    """
    JST naive -> server時刻 naive（MT5 API用）
    ts_jst は「JST naive（ミチビキ内部標準）」前提。
    server_offset(+2h等) を足して「サーバ時刻」に寄せる。
    MT5側が naive datetime をUTCではなく「サーバローカル」として解釈している場合に使用。
    """
    if ts_jst.tzinfo is None:
        ts_jst = ts_jst.tz_localize(JST)
    # 「壁時計」として扱うため tzを落とした値に offset を足す（最小差分）
    naive = ts_jst.tz_localize(None)
    naive_server = naive + pd.Timedelta(seconds=SERVER_OFFSET_SEC)
    return naive_server.to_pydatetime().replace(tzinfo=None)


def _range_attempts(
    symbol: str, tf: int, start_ts: pd.Timestamp, end_ts: pd.Timestamp
) -> tuple[pd.DataFrame | None, str]:
    """
    copy_rates_range() を 3 方式（UTC-naive / local-naive / UTC-aware）で試す。
    成功時は (DataFrame, "ok:<tag>")、全滅なら (None, "fail")
    成功判定：rows>0 かつ df.time.max が end_ts 近傍（1バー分以内）
    """
    # tf（定数）→分 のマッピング
    tf_to_min = {
        mt5.TIMEFRAME_M1: 1,
        mt5.TIMEFRAME_M5: 5,
        mt5.TIMEFRAME_M15: 15,
        mt5.TIMEFRAME_M30: 30,
        mt5.TIMEFRAME_H1: 60,
        mt5.TIMEFRAME_H4: 240,
        mt5.TIMEFRAME_D1: 1440,
    }
    tol = pd.Timedelta(minutes=tf_to_min.get(tf, 5))

    variants = [
        ("utc_naive", _to_utc_naive(start_ts), _to_utc_naive(end_ts)),
        ("local_naive", _to_local_naive(start_ts), _to_local_naive(end_ts)),
        ("utc_aware", _to_utc_aware(start_ts), _to_utc_aware(end_ts)),
    ]
    for tag, dfrom, dto in variants:
        rates = mt5.copy_rates_range(resolve_symbol(symbol), tf, dfrom, dto)
        if rates is None:
            code, details = mt5.last_error()
            log(f"[try:{tag}] copy_rates_range returned None: {code} {details}")
            continue
        df = pd.DataFrame(rates)
        if len(df) == 0:
            log(f"[ok:{tag}] fetched rows=0")
            return pd.DataFrame(columns=CSV_COLS), f"ok:{tag}"
        df["time"] = jst_from_mt5_epoch(df["time"])
        df = df.sort_values("time").reset_index(drop=True)
        # 成功判定強化：df.time.max が end_ts 近傍（1バー分以内）かチェック
        max_time = df["time"].max()
        if max_time < (end_ts - tol):
            log(f"[range_insufficient:{tag}] max={max_time} end={end_ts} tol={tol} -> fallback")
            continue  # 次のvariantへ（全部ダメなら fail で fallback）
        log(f"[ok:{tag}] fetched rows={len(df)}")
        return df[CSV_COLS], f"ok:{tag}"
    return None, "fail"


def fetch_rates(
    symbol: str, tf: int, start_ts: pd.Timestamp, end_ts: pd.Timestamp
) -> pd.DataFrame:
    """
    指定期間のレートを取得して DataFrame で返す（JSTに変換）。
    まず copy_rates_range() を試し、全滅したら copy_rates_from() のバックページングで補う。
    """
    if not isinstance(start_ts, pd.Timestamp):
        start_ts = pd.Timestamp(start_ts)
    if not isinstance(end_ts, pd.Timestamp):
        end_ts = pd.Timestamp(end_ts)
    if end_ts <= start_ts:
        raise ValueError(f"start >= end: {start_ts} .. {end_ts}")

    # 観測ログ：tick 情報を取得
    tick = mt5.symbol_info_tick(resolve_symbol(symbol))
    if tick and tick.time:
        tick_epoch = int(tick.time)
        tick_utc = pdt.fromtimestamp(tick_epoch, tz=UTC)
        tick_jst = tick_utc.astimezone(ZoneInfo("Asia/Tokyo"))
    else:
        tick_epoch = None
        tick_utc = None
        tick_jst = None

    # 1) range 試行
    df_range, status = _range_attempts(symbol, tf, start_ts, end_ts)
    if status.startswith("ok"):
        # 観測ログ：1行で出力（range成功時）
        if len(df_range) > 0:
            rates_last_jst = df_range["time"].max()
            rates_last_utc = pd.Timestamp(rates_last_jst).tz_localize("Asia/Tokyo").tz_convert("UTC")
            rates_last_epoch = int(rates_last_utc.timestamp())
        else:
            rates_last_epoch = None
            rates_last_utc = None
            rates_last_jst = None
        log(f"tick_epoch={tick_epoch} tick_utc={tick_utc} tick_jst={tick_jst} rates_last_epoch={rates_last_epoch} rates_last_utc={rates_last_utc} rates_last_jst={rates_last_jst}")
        return df_range

    # 2) フォールバック: from で過去にページング
    log("[fallback] using copy_rates_from() paging backward")
    dt_to = _to_server_naive(end_ts)  # MT5がserver時刻として解釈するnaive datetime

    frames = []
    safety_loops = 0

    while safety_loops < MAX_LOOPS:
        safety_loops += 1
        rates = mt5.copy_rates_from(resolve_symbol(symbol), tf, dt_to, PAGE)
        if rates is None:
            code, details = mt5.last_error()
            log(f"[fallback] copy_rates_from returned None: {code} {details}")
            break
        if len(rates) == 0:
            log("[fallback] no more bars returned")
            break

        # 生のDataFrame（UTC epoch秒）
        df_raw = pd.DataFrame(rates)

        # 観測ログ：df_raw["time"].max() を UTC/JST 両方で表示
        if len(df_raw) > 0:
            raw_max_epoch = int(df_raw["time"].max())
            raw_max_utc = pdt.fromtimestamp(raw_max_epoch, tz=UTC)
            raw_max_jst = raw_max_utc.astimezone(JST)
            log(f"[fallback] df_raw.time.max epoch={raw_max_epoch} utc={raw_max_utc} jst={raw_max_jst} start_ts={start_ts} end_ts={end_ts}")

        # まずはJSTへ
        df = df_raw.copy()
        df["time"] = jst_from_mt5_epoch(df["time"])
        df = df.sort_values("time").reset_index(drop=True)

        # 目標期間に重なる分だけ保持（JST基準でフィルタ）
        df_keep = df[(df["time"] >= start_ts) & (df["time"] <= end_ts)]
        if len(df_keep):
            frames.append(df_keep[CSV_COLS])

        # 次ページの終端（さらに過去へ）
        oldest_utc_epoch = int(df_raw["time"].min())  # epoch秒
        # DeprecationWarning 回避：UTC aware で作ってから tzinfo=None で naive UTC へ
        dt_to = pdt.fromtimestamp(oldest_utc_epoch - 1, tz=UTC).replace(tzinfo=None)

        # もう十分遡れたか？
        if len(df) and df["time"].min() <= start_ts:
            break

    if not frames:
        log("[fallback] collected 0 rows")
        return pd.DataFrame(columns=CSV_COLS)

    out = (
        pd.concat(frames, axis=0, ignore_index=True)
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )
    log(
        f"[fallback] fetched rows={len(out)} (min={out['time'].min()} .. max={out['time'].max()})"
    )
    # 観測ログ：1行で出力（fallback成功時）
    if len(out) > 0:
        rates_last_jst = out["time"].max()
        rates_last_utc = pd.Timestamp(rates_last_jst).tz_localize("Asia/Tokyo").tz_convert("UTC")
        rates_last_epoch = int(rates_last_utc.timestamp())
    else:
        rates_last_epoch = None
        rates_last_utc = None
        rates_last_jst = None
    log(f"tick_epoch={tick_epoch} tick_utc={tick_utc} tick_jst={tick_jst} rates_last_epoch={rates_last_epoch} rates_last_utc={rates_last_utc} rates_last_jst={rates_last_jst}")
    return out[CSV_COLS]


# =========================
# CSV 作成・更新
# =========================


def ensure_csv_for_timeframe(
    symbol: str,
    tf_name: str,
    start_date: str,
    data_dir: Path,
    end_date: str | None = None,
    layout: str = "per-symbol",
) -> Path:
    """
    単一タイムフレームのCSVを作成/更新する。
    - start_date ～ end_date の範囲で作成（end_date が None の場合は現在まで）
    - 既存があれば末尾以降のみ追記
    - 返り値: 保存した CSV のパス
    """
    log(f"=== begin timeframe={tf_name} ===")
    if tf_name not in TF_MAP:
        raise ValueError(f"未知のタイムフレーム: {tf_name}")

    tf_const = TF_MAP[tf_name]
    assert FILE_TAG is not None, "FILE_TAG が未設定です（main() で設定されます）"
    # 統一パス生成
    if get_ohlcv_csv_path is not None:
        csv_path = get_ohlcv_csv_path(
            symbol, tf_name, data_root=data_dir, layout=layout
        )
    else:
        # fall back to legacy behavior (data_dir / FILE_TAG_tf.csv)
        csv_path = data_dir / f"{FILE_TAG}_{tf_name}.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)

    start_ts = pd.Timestamp(start_date)  # JST naive

    if end_date is not None:
        end_ts = pd.Timestamp(end_date)
    else:
        end_ts = pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None)

    # 既存CSVの読み込み
    if csv_path.exists():
        old = pd.read_csv(csv_path, parse_dates=["time"])
        old = old[CSV_COLS]
        last_time = old["time"].max()
        # 既存の最終時刻の次のバーから取得（重複回避）
        # M5の場合は5分、M15の場合は15分、H1の場合は1時間を加算
        bar_interval_map = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
        interval_min = bar_interval_map.get(tf_name, 5)
        fetch_from = max(start_ts, last_time + pd.Timedelta(minutes=interval_min))
        log(
            f"{csv_path.name}: existing rows={len(old)} last={last_time} -> fetch_from={fetch_from} (last+{interval_min}min)"
        )
    else:
        old = None
        fetch_from = start_ts
        log(f"{csv_path.name}: not found -> fresh export from {fetch_from}")

    # 観測ログ：fetch_from と end_ts を raw/UTC/JST で表示
    fetch_from_raw = fetch_from
    end_ts_raw = end_ts
    # fetch_from と end_ts は JST naive として扱われている
    fetch_from_jst = fetch_from.tz_localize("Asia/Tokyo") if fetch_from.tz is None else fetch_from.tz_convert("Asia/Tokyo")
    end_ts_jst = end_ts.tz_localize("Asia/Tokyo") if end_ts.tz is None else end_ts.tz_convert("Asia/Tokyo")
    fetch_from_utc = fetch_from_jst.tz_convert("UTC")
    end_ts_utc = end_ts_jst.tz_convert("UTC")
    log(f"fetch_from_raw={fetch_from_raw} end_ts_raw={end_ts_raw} fetch_from_utc={fetch_from_utc} end_ts_utc={end_ts_utc} fetch_from_jst={fetch_from_jst} end_ts_jst={end_ts_jst}")

    # end_ts より進んでいたら、新規取得は行わない
    if end_ts <= fetch_from:
        log(
            f"{csv_path.name}: end_ts <= fetch_from ({end_ts} <= {fetch_from}) -> no new fetch"
        )
        merged = old if old is not None else pd.DataFrame(columns=CSV_COLS)
    else:
        # データ取得（少し余分に取り直して重複で吸収）
        df_new = fetch_rates(symbol, tf_const, fetch_from, end_ts)
        log(f"{csv_path.name}: fetched rows={len(df_new)} [{fetch_from} .. {end_ts}]")

        # 観測：df_new.time の型とサンプル
        if "time" in df_new.columns:
            log(f"{csv_path.name}: df_new.time dtype(before)={df_new['time'].dtype} head={df_new['time'].head(1).tolist()} tail={df_new['time'].tail(1).tolist()}")

        # fetch_rates() は time を JST naive datetime64[ns] で返す設計。
        # ここで再 to_datetime すると NaT 化するケースがあるため、dtype が object の時だけ矯正する。
        if "time" in df_new.columns and str(df_new["time"].dtype) == "object":
            df_new["time"] = pd.to_datetime(df_new["time"], errors="coerce")
            log(f"{csv_path.name}: df_new.time dtype(after)={df_new['time'].dtype} max={df_new['time'].max()}")

        # マージ＆重複除去
        merged = merge_and_dedup(old, df_new)

        # 観測ログ（max時刻とdtype確認）
        log(f"{csv_path.name}: old.max={old['time'].max() if old is not None and len(old) else None}")
        log(f"{csv_path.name}: new.max={df_new['time'].max() if df_new is not None and len(df_new) else None} new.dtypes.time={df_new['time'].dtype if df_new is not None and 'time' in df_new.columns else None}")
        log(f"{csv_path.name}: merged.max={merged['time'].max() if merged is not None and len(merged) else None} merged.dtypes.time={merged['time'].dtype if merged is not None and 'time' in merged.columns else None}")

    # 型最適化（省メモリ）
    if not merged.empty:
        for c in ["open", "high", "low", "close"]:
            merged[c] = merged[c].astype("float32")
        int_cols = ["tick_volume", "spread", "real_volume"]
        # OBS: which int cols have NaN/inf
        for c in int_cols:
            s = pd.to_numeric(merged[c], errors="coerce")
            na = int(s.isna().sum())
            inf = int(np.isinf(s.to_numpy(dtype="float64", copy=False)).sum()) if len(s) else 0
            if na or inf:
                print(f"[OBS][int_cast] col={c} na={na} inf={inf}")
        for c in int_cols:
            s = pd.to_numeric(merged[c], errors="coerce")
            s = s.replace([np.inf, -np.inf], np.nan).fillna(0)
            merged[c] = s.astype("int32")

    # CSV保存前ガード：単調増加チェック
    if not merged.empty and "time" in merged.columns:
        # time列が単調増加かチェック（直接チェック）
        is_monotonic = merged["time"].is_monotonic_increasing

        if not is_monotonic:
            # 単調増加が崩れている場合
            # tail_time より古い行が混ざった数をカウント
            if old is not None and len(old) > 0:
                tail_time = old["time"].max()
                older_rows = len(merged[merged["time"] < tail_time])
            else:
                tail_time = None
                older_rows = 0

            # 最小限の観測：head(3) と tail(3) をログ出力
            time_head = merged["time"].head(3).tolist()
            time_tail = merged["time"].tail(3).tolist()

            log(f"[time_audit][WARNING] {csv_path.name}: time column is not monotonic increasing! "
                f"tail_time={tail_time} older_rows={older_rows} total_rows={len(merged)} -> skip save")
            log(f"[time_audit][WARNING] {csv_path.name}: time range: min={merged['time'].min()} max={merged['time'].max()}")
            log(f"[time_audit][WARNING] {csv_path.name}: time head(3)={time_head} tail(3)={time_tail}")
            # 保存しない（return）
            log(f"{csv_path.name}: skipped save due to non-monotonic time column")
            log(f"=== end timeframe={tf_name} ===")
            return csv_path

    merged.to_csv(csv_path, index=False)
    log(f"{csv_path.name}: wrote rows={len(merged)}")
    log(f"filepath: {csv_path.resolve()}")
    log(f"=== end timeframe={tf_name} ===")
    return csv_path


# =========================
# エントリポイント
# =========================


def main():
    parser = argparse.ArgumentParser(
        description="Export/Update MT5 rates to CSV per timeframe."
    )
    parser.add_argument(
        "--symbol", default=SYMBOL_DEFAULT, help="シンボル（例: USDJPY）"
    )
    parser.add_argument(
        "--timeframes", nargs="+", default=TIMEFRAMES_DEFAULT, help="例: M5 M15 H1"
    )
    parser.add_argument(
        "--start", default=START_DATE_DEFAULT, help="開始日（例: 2020-11-01）"
    )
    parser.add_argument(
        "--end",
        default=None,
        help="終了日（例: 2024-07-10、省略時は現在時刻まで）",
    )
    parser.add_argument(
        "--data-dir",
        default=DATA_DIR_DEFAULT,
        help="保存先ディレクトリ（相対はプロジェクトルート基準）",
    )
    parser.add_argument(
        "--terminal", default=None, help="MT5 terminal.exe のフルパス（必要な場合のみ）"
    )

    # 環境と保存レイアウト
    parser.add_argument(
        "--env",
        choices=["laptop", "desktop", "vps"],
        default=None,
        help="環境名を明示（laptop/desktop/vps）。未指定ならホスト名ヒューリスティック。",
    )
    parser.add_argument(
        "--layout",
        choices=["flat", "per-symbol"],
        default="per-symbol",
        help="CSV保存レイアウト。flat= data/直下, per-symbol= data/<SYMBOL>/ohlcv/ 下に保存",
    )

    args = parser.parse_args()

    symbol = args.symbol.upper()
    tfs = [tf.upper() for tf in args.timeframes]

    # data_root を決定（FXBOT_DATA 環境変数、--data-dir を考慮）
    if get_data_root is not None:
        data_root = get_data_root(cli_data_dir=args.data_dir)
    else:
        raw_data_dir = Path(args.data_dir)
        data_root = (
            raw_data_dir
            if raw_data_dir.is_absolute()
            else (PROJECT_ROOT / raw_data_dir)
        )
        data_root = data_root.resolve()

    end_display = args.end or "NOW"
    log(
        f"start export: symbol={symbol} tfs={tfs} start={args.start} end={end_display} data_root={data_root}"
    )
    log(f"cwd={Path.cwd()} project_root={PROJECT_ROOT}")

    # 環境推定（明示指定優先 → HOST_MAP → ヒューリスティック）
    host = os.environ.get("COMPUTERNAME", socket.gethostname()).lower()
    HOST_MAP = {
        # 必要に応じて固定マッピングを追加
        # 例: "desktop-8rrd83d": "laptop",
        # "sakura-vps": "vps",
    }
    if args.env:
        env_resolved = args.env
    else:
        env_resolved = HOST_MAP.get(host)
        if not env_resolved:
            if (
                "vps" in host
                or "sakura" in host
                or "administrator" in str(Path.home()).lower()
            ):
                env_resolved = "vps"
            elif "desk" in host:
                env_resolved = "desktop"
            else:
                env_resolved = "laptop"
    log(f"環境: {env_resolved} (host={host})")

    # MT5 初期化
    ensure_mt5_initialized(terminal_path=args.terminal)

    # シンボル解決（USDJPY / USDJPY- など）
    resolved_symbol = resolve_symbol(symbol)
    if resolved_symbol != symbol:
        log(f"symbol resolved: {symbol} -> {resolved_symbol}")
    symbol = resolved_symbol
    mt5.symbol_select(resolve_symbol(symbol), True)

    # symbol resolved の直後
    update_server_offset(symbol)

    # CSV 用の “接尾辞なしタグ” を作成（英字のみ抽出）
    global FILE_TAG
    FILE_TAG = "".join([c for c in symbol if c.isalpha()]) or symbol

    # 保存先ルートは data_root（一つの場所から get_ohlcv_csv_path で個別ファイルを決定）
    save_root = data_root
    log(f"save_root={save_root}")

    # 取得・保存
    created: list[Path] = []
    for tf_name in tfs:
        path = ensure_csv_for_timeframe(
            symbol,
            tf_name,
            args.start,
            save_root,
            end_date=args.end,
            layout=args.layout,
        )
        created.append(path)

    mt5.shutdown()
    log("done.")


if __name__ == "__main__":
    main()


