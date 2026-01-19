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
from datetime import UTC
from datetime import datetime as pdt
from pathlib import Path

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

# CSVファイル名で使う “接尾辞なしタグ” を main() 内で設定
FILE_TAG: str | None = None


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
    MT5の 'time' (Unix秒, UTC) を JST の naive datetime64[ns] に変換。
    series は pandas Series でも DatetimeIndex でも両対応。
    """
    s = pd.to_datetime(series, unit="s", utc=True)
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


def _to_utc_naive(ts_jst: pd.Timestamp) -> pdt:
    """JST naive -> UTC naive（tzinfoなし）"""
    return (
        ts_jst.tz_localize("Asia/Tokyo")
        .tz_convert("UTC")
        .to_pydatetime()
        .replace(tzinfo=None)
    )


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


def _range_attempts(
    symbol: str, tf: int, start_ts: pd.Timestamp, end_ts: pd.Timestamp
) -> tuple[pd.DataFrame | None, str]:
    """
    copy_rates_range() を 3 方式（UTC-naive / local-naive / UTC-aware）で試す。
    成功時は (DataFrame, "ok:<tag>")、全滅なら (None, "fail")
    """
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

    # 1) range 試行
    df_range, status = _range_attempts(symbol, tf, start_ts, end_ts)
    if status.startswith("ok"):
        return df_range

    # 2) フォールバック: from で過去にページング
    log("[fallback] using copy_rates_from() paging backward")
    dt_to = _to_utc_naive(end_ts)  # MT5は tzinfo なしの UTC を好む

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

        # マージ＆重複除去
        merged = merge_and_dedup(old, df_new)

    # 型最適化（省メモリ）
    if not merged.empty:
        for c in ["open", "high", "low", "close"]:
            merged[c] = merged[c].astype("float32")
        for c in ["tick_volume", "spread", "real_volume"]:
            merged[c] = merged[c].astype("int32")

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


