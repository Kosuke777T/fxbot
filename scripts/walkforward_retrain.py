from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgbm
import numpy as np
import pandas as pd
from joblib import dump, load
from sklearn.metrics import log_loss, precision_recall_curve, roc_auc_score
from sklearn.model_selection import train_test_split
import traceback

# ------------------------------------------------------------
# 基本設定（フォルダなど）
# ------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
MODELS_DIR = PROJECT_ROOT / "models"
LOGS_DIR = PROJECT_ROOT / "logs"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ✅ active_model.json を壊さないための原子更新
def _write_json_atomic(path: Path, obj: dict) -> None:
    """JSONファイルを原子的に書き込む（途中で壊れたJSONが残らないようにする）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # ASCIIに寄せてエンコーディング事故を避ける
    payload = json.dumps(obj, ensure_ascii=True, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    # Windowsでも同一ボリュームなら原子的に置換される
    tmp.replace(path)


# ✅ apply安全装置
def _safe_apply_active_model(
    model_path: Path,
    meta_path: Path,
    best_threshold: float,
    expected_features: list[str],
) -> dict:
    """
    active_model.json を安全に更新するためのチェックと実行

    Returns:
        {
            "ok": bool,
            "reason": str,  # "updated" / "same_model" / "bad_features" / "model_missing" / "model_load_failed"
            "code": str,     # エラーコード（失敗時のみ）
            "message": str,  # エラーメッセージ（失敗時のみ）
            "trace": str,    # トレースバック（失敗時のみ）
        }
    """
    try:
        # 1. 新モデルファイルの存在チェック
        if not model_path.exists():
            return {
                "ok": False,
                "reason": "model_missing",
                "code": "MODEL_FILE_MISSING",
                "message": f"Model file not found: {model_path}",
                "trace": "",
            }

        # 2. expected_features のチェック
        if not expected_features or len(expected_features) == 0:
            return {
                "ok": False,
                "reason": "bad_features",
                "code": "BAD_EXPECTED_FEATURES",
                "message": "expected_features is empty or None",
                "trace": "",
            }

        # 3. 既存 active_model.json との比較（同一モデルチェック）
        active_path = MODELS_DIR / "active_model.json"
        if active_path.exists():
            try:
                existing = json.loads(active_path.read_text(encoding="utf-8"))
                existing_model_file = existing.get("model_file") or existing.get("file")
                if existing_model_file == model_path.name:
                    return {
                        "ok": False,
                        "reason": "same_model",
                        "code": "SAME_MODEL",
                        "message": f"Model file is same as existing: {model_path.name}",
                        "trace": "",
                    }
            except Exception:
                # 既存ファイルが壊れていても続行（上書きする）
                pass

        # 4. 新モデルのロードとダミー予測チェック
        try:
            model = load(model_path)
            # ダミー予測（shape 合わせ）
            # expected_features の数だけ 0.0 の配列を作成
            dummy_X = np.array([[0.0] * len(expected_features)], dtype=np.float32)
            if hasattr(model, "predict_proba"):
                _ = model.predict_proba(dummy_X)
            elif hasattr(model, "predict"):
                _ = model.predict(dummy_X)
            # 例外が出なければOK
        except Exception as e:
            return {
                "ok": False,
                "reason": "model_load_failed",
                "code": "MODEL_LOAD_FAIL",
                "message": f"Model load or predict failed: {e}",
                "trace": traceback.format_exc(),
            }

        # 5. すべてのチェックOK → 更新実行
        active = {
            "model_file": str(model_path.name),
            "meta_file": str(meta_path.name),
            "best_threshold": best_threshold,
            "updated_at": jst_now_str(),
            "features": expected_features,
        }
        _write_json_atomic(active_path, active)
        return {
            "ok": True,
            "reason": "updated",
            "code": None,
            "message": None,
            "trace": None,
        }

    except Exception as e:
        return {
            "ok": False,
            "reason": "unexpected_error",
            "code": "UNEXPECTED_ERROR",
            "message": str(e),
            "trace": traceback.format_exc(),
        }


def resolve_data_root(cli_data_dir: str | None) -> Path:
    """
    データのルート候補を複数試して、最初に存在したディレクトリを採用する。
    優先順位:
      1) --data-dir 引数
      2) 環境変数 FXBOT_DATA
      3) このスクリプトのプロジェクトルート配下の data/
      4) カレントディレクトリ配下の data/
    """
    candidates: list[Path] = []

    # 1) CLI 引数
    if cli_data_dir:
        candidates.append(Path(cli_data_dir))

    # 2) 環境変数
    env_dir = os.getenv("FXBOT_DATA")
    if env_dir:
        candidates.append(Path(env_dir))

    # 3) プロジェクトルートの data (C:\Users\...\fxbot\data / D:\...\fxbot\data / C:\fxbot\data)
    candidates.append(DATA_DIR)

    # 4) 念のためカレントディレクトリの data
    candidates.append(Path.cwd() / "data")

    existing = [p for p in candidates if p.is_dir()]
    if existing:
        return existing[0].resolve()

    # どれもなければ最後に DATA_DIR を返す（存在しなくてもエラー時のメッセージ用）
    return DATA_DIR.resolve()


RNG = np.random.default_rng(42)
pd.options.display.width = 200
warnings.filterwarnings("ignore", category=UserWarning)


# ------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------
def jst_now_str() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def safe_log(msg: str):
    ts = jst_now_str()
    print(f"{ts} | {msg}", flush=True)


def find_csv(symbol: str, timeframe: str, data_dir: str | None = None) -> Path:
    """
    CSVレイアウト両対応:
      - flat:       data/USDJPY_M5.csv
      - per-symbol: data/USDJPY/ohlcv/  内の  {symbol}_{tf}.csv もしくは  {tf}.csv
    優先順: 明示一致 → タイムスタンプが新しいもの
    """
    # ルート決定（--data-dir / FXBOT_DATA / PROJECT_ROOT/data / ./data の順で存在を確認）
    root = resolve_data_root(data_dir)

    symU = symbol.upper()
    symL = symbol.lower()
    tf = timeframe.upper()

    # 記号付きシンボル（USDJPY- 等）から英字だけのバージョンも作る
    symU_clean = "".join(ch for ch in symU if ch.isalpha())
    symL_clean = symU_clean.lower()

    candidates: list[Path] = []

    # --- flat layout (data/直下)
    candidates += list(root.glob(f"{symU}_{tf}.csv"))
    candidates += list(root.glob(f"{symL}_{tf}.csv"))
    candidates += list(root.glob(f"{symU_clean}_{tf}.csv"))
    candidates += list(root.glob(f"{symL_clean}_{tf}.csv"))
    candidates += list(root.glob(f"*_{tf}.csv"))  # 例: ANYTHING_M5.csv

    # --- per-symbol layout（推奨: data/USDJPY/ohlcv/）
    base_dirs = [
        root / symU / "ohlcv",
        root / symL / "ohlcv",
        root / symU_clean / "ohlcv",
        root / symL_clean / "ohlcv",
        root / symU,
        root / symL,
        root / symU_clean,
        root / symL_clean,
    ]

    for b in base_dirs:
        candidates += list(b.glob(f"{symU}_{tf}.csv"))
        candidates += list(b.glob(f"{symL}_{tf}.csv"))
        candidates += list(b.glob(f"{symU_clean}_{tf}.csv"))
        candidates += list(b.glob(f"{symL_clean}_{tf}.csv"))
        candidates += list(b.glob(f"*_{tf}.csv"))  # 例: anyprefix_M5.csv
        candidates += list(b.glob(f"{tf}.csv"))    # 例: M5.csv

    # 実在ファイルだけ、重複除去
    uniq: list[Path] = []
    seen = set()
    for p in candidates:
        if p.is_file():
            try:
                key = p.resolve()
            except Exception:
                key = p
            if key not in seen:
                seen.add(key)
                uniq.append(p)

    if not uniq:
        tried = [
            root / f"{symU}_{tf}.csv",
            root / f"{symU_clean}_{tf}.csv",
            root / symU / "ohlcv" / f"{symU}_{tf}.csv",
            root / symU_clean / "ohlcv" / f"{symU_clean}_{tf}.csv",
        ]
        msg = (
            "CSVが見つかりません。\\n"
            f"  symbol={symbol} timeframe={timeframe}\\n"
            f"  data_dir={root}\\n"
            "  試した場所の例:\\n    - " + "\\n    - ".join(str(p) for p in tried)
        )
        raise FileNotFoundError(msg)

    # 明示一致（{symbol}_{tf}.csv / clean版）があれば最優先
    exact = [
        p
        for p in uniq
        if p.name.lower()
        in {
            f"{symL}_{tf.lower()}.csv",
            f"{symL_clean}_{tf.lower()}.csv",
        }
    ]
    if exact:
        return exact[0]

    # それ以外は最終更新が新しいもの
    return max(uniq, key=lambda p: p.stat().st_mtime)


# ------------------------------------------------------------
# 特徴量生成
# ------------------------------------------------------------
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = np.clip(delta, 0, None)
    down = -np.clip(delta, None, 0)
    ma_up = up.rolling(period, min_periods=period).mean()
    ma_down = down.rolling(period, min_periods=period).mean()
    rs = ma_up / (ma_down + 1e-12)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def build_features(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    入力: time, open, high, low, close, tick_volume などのOHLCVを想定
    出力: 特徴量 DataFrame（欠損除去済み）
    """
    df = df_raw.copy()

    # 必須列チェック（ここで止まる場合はCSV修正が必要）
    need = {"time", "open", "high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        safe_log(f"[WFO][error] CSV missing columns: {sorted(miss)}")
        return pd.DataFrame()

    # 時刻整備
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time", kind="stable").drop_duplicates(subset=["time"])

    n = len(df)

    # --- ミニ特徴量モード（行数が少ないときの救済） ---
    # 60行未満なら、ロール系は使わずに最低限の特徴量だけで返す
    if n < 60:
        safe_log(
            f"[WFO][warn] tiny dataset detected ({n} rows). Using mini feature set."
        )
        rng = (df["high"] - df["low"]).replace(0, np.nan)
        mini = pd.DataFrame(
            {
                "time": df["time"],
                "open": df["open"],
                "high": df["high"],
                "low": df["low"],
                "close": df["close"],
                # 最低限：1本リターン、レンジ内位置
                "ret1": df["close"].pct_change().fillna(0.0),
                "pos_in_range": ((df["close"] - df["low"]) / rng).fillna(0.5),
            }
        )
        # 数学的におかしい値を除去
        mini = mini.replace([np.inf, -np.inf], np.nan).dropna()
        return mini

    # --- 通常のフル特徴量モード ---
    # 基本の戻りとボラ
    df["ret1"] = df["close"].pct_change()
    df["ret3"] = df["close"].pct_change(3)
    df["ret5"] = df["close"].pct_change(5)
    df["vol20"] = df["close"].pct_change().rolling(20, min_periods=10).std()

    # 移動平均・バンド（min_periodsで消滅を抑制）
    for w in (5, 10, 20, 50):
        df[f"sma{w}"] = df["close"].rolling(w, min_periods=max(2, w // 2)).mean()
        df[f"ema{w}"] = df["close"].ewm(span=w, adjust=False).mean()
    df["bb_mid"] = df["close"].rolling(20, min_periods=10).mean()
    df["bb_std"] = df["close"].rolling(20, min_periods=10).std()
    df["bb_p"] = (df["close"] - df["bb_mid"]) / (df["bb_std"] + 1e-12)

    # RSI / ATR
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        up = np.clip(delta, 0, None)
        down = -np.clip(delta, None, 0)
        ma_up = up.rolling(period, min_periods=period // 2).mean()
        ma_down = down.rolling(period, min_periods=period // 2).mean()
        rs = ma_up / (ma_down + 1e-12)
        return 100 - (100 / (1 + rs))

    def _atr(df_: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df_["high"], df_["low"], df_["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return tr.rolling(period, min_periods=period // 2).mean()

    df["rsi14"] = _rsi(df["close"], 14)
    df["atr14"] = _atr(df, 14)

    # ヒゲ比率（レンジのどこで引けたか）
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["pos_in_range"] = (df["close"] - df["low"]) / rng

    # 出来高代理（あれば）
    if "tick_volume" in df.columns:
        df["vol_sma20"] = df["tick_volume"].rolling(20, min_periods=10).mean()
        df["vol_chg"] = df["tick_volume"].pct_change()

    feature_cols = [
        "ret1",
        "ret3",
        "ret5",
        "vol20",
        "sma5",
        "sma10",
        "sma20",
        "sma50",
        "ema5",
        "ema10",
        "ema20",
        "ema50",
        "bb_p",
        "rsi14",
        "atr14",
        "pos_in_range",
        "vol_sma20",
        "vol_chg",
    ]
    feature_cols = [c for c in feature_cols if c in df.columns]

    keep_cols = ["time", "open", "high", "low", "close"] + feature_cols
    df = df[keep_cols].copy()

    # 先頭のNaNを一括でトリム（最大ウィンドウ50に合わせる）
    trim = 50
    if len(df) > trim:
        df = df.iloc[trim:].copy()

    # それでも残るNaN/infは除去
    df = df.replace([np.inf, -np.inf], np.nan).dropna()

    return df


def make_label(df: pd.DataFrame, horizon: int = 10, pips: float = 0.0) -> pd.Series:
    """
    horizon 後の方向ラベル:
      close_{t+h} - close_t > 0 なら 1, それ以外 0
    pips を与えた場合は閾値として使う（pipsは価格差 0.01=1pips 相当の口座もあるので注意）
    """
    future = df["close"].shift(-horizon)
    diff = future - df["close"]
    if pips and pips > 0:
        y = (diff > pips).astype(int)
    else:
        y = (diff > 0).astype(int)
    return y


# ------------------------------------------------------------
# WFO スキーム
# ------------------------------------------------------------
@dataclass
class WFOMetrics:
    fold: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_train: int
    n_test: int
    auc: float
    logloss: float
    f1_at_thr: float
    thr: float


def iter_wfo_slices(df: pd.DataFrame, train_bars: int, test_bars: int, step_bars: int):
    """
    walk-forward: 固定長学習→固定長テスト→stepで前進
    """
    n = len(df)
    start = 0
    fold = 0
    while True:
        train_start = start
        train_end = train_start + train_bars
        test_end = train_end + test_bars
        if test_end > n:
            break

        yield fold, slice(train_start, train_end), slice(train_end, test_end)
        fold += 1
        start += step_bars


# ------------------------------------------------------------
# しきい値最適化
# ------------------------------------------------------------
def pick_threshold(y_true: np.ndarray, prob: np.ndarray) -> tuple[float, float]:
    """
    PR 曲線から F1 最大点を採用。閾値を返す。
    """
    precision, recall, thresholds = precision_recall_curve(y_true, prob)
    # thresholds の長さは len(precision)-1
    f1 = 2 * precision[:-1] * recall[:-1] / (precision[:-1] + recall[:-1] + 1e-12)
    idx = int(np.nanargmax(f1))
    best_thr = float(np.clip(thresholds[idx], 0.05, 0.95))
    return best_thr, float(f1[idx])


# ------------------------------------------------------------
# 学習（LightGBM）
# ------------------------------------------------------------
def train_lgbm(X: pd.DataFrame, y: pd.Series) -> lgbm.LGBMClassifier:
    params = dict(
        objective="binary",
        boosting_type="gbdt",
        n_estimators=400,
        learning_rate=0.05,
        num_leaves=63,
        max_depth=-1,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_alpha=0.0,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=1,  # VPS 2GB 想定で控えめ
        verbose=-1,
    )
    model = lgbm.LGBMClassifier(**params)
    # DataFrame のまま渡す（列順・名前を維持）
    model.fit(X, y)
    return model


# ------------------------------------------------------------
# メイン
# ------------------------------------------------------------
def main() -> int:
    """
    メイン関数。最終行にJSONを出力し、exit codeを返す。

    Returns:
        0: 成功（ok=true, step=done）
        11: 実行スキップ（データ不足/期間ゼロ等）
        13: apply スキップ（同一モデル等）
        30: 失敗（ok=false）
    """
    # 引数なしでも安全に動くように、デフォルト値と説明を追加
    ap = argparse.ArgumentParser(
        description="LightGBM walk-forward retrain",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--symbol",
        default="USDJPY-",
        help="例: USDJPY- （未指定時は安全なデフォルト）",
    )
    ap.add_argument(
        "--timeframe",
        default="M5",
        help="例: M5, M15, H1",
    )
    ap.add_argument("--horizon", type=int, default=10, help="予測先 (bars)")
    ap.add_argument(
        "--train_bars",
        type=int,
        default=90_000,
        help="学習バー数（例: 90k≈数ヶ月~年）",
    )
    ap.add_argument(
        "--test_bars",
        type=int,
        default=7_000,
        help="テストバー数（例: 1週間分くらい）",
    )
    ap.add_argument(
        "--step_bars",
        type=int,
        default=7_000,
        help="前進幅（通常 test_bars と同じ）",
    )
    ap.add_argument(
        "--model_name",
        default="LightGBM_clf",
        help="保存名のベース",
    )
    ap.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="CSVのルートディレクトリ（未指定なら FXBOT_DATA / PROJECT_ROOT/data / ./data の順で探索）",
    )
    # 危険操作制御フラグ
    ap.add_argument(
        "--apply",
        action="store_true",
        help="新しいモデルとしきい値を active_model.json に反映する",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="モデル評価のみ行い、active_model.json などは一切更新しない",
    )
    ap.add_argument(
        "--dry",
        type=int,
        choices=[0, 1],
        default=None,
        help="compat alias for --dry-run (0/1)",
    )
    ap.add_argument(
        "--json",
        "--emit-json",
        dest="emit_json",
        type=int,
        default=1,
        choices=[0, 1],
        help="最終行に result JSON を出力するか (0=なし, 1=出力)",
    )
    ap.add_argument(
        "--out-json",
        type=str,
        default=None,
        help="結果JSONを保存するパス（未指定なら logs/retrain/ に保存）",
    )
    # 既存に profile 系オプションがあるなら、それに寄せる
    ap.add_argument(
        "--profile",
        type=str,
        default=None,
        help="profile name (alias for --model-name)",
    )
    ap.add_argument(
        "--profiles",
        type=str,
        default=None,
        help="comma-separated profile names (takes precedence over --profile)",
    )
    args = ap.parse_args()

    # --dry を --dry-run にマップ（既存ロジックとの互換性）
    if args.dry is not None:
        args.dry_run = bool(args.dry)

    # --json の後方互換性（emit_json に統一）
    if not hasattr(args, "emit_json"):
        args.emit_json = getattr(args, "json", 1)
    # 既存コードが args.json を参照している場合の互換性
    args.json = args.emit_json

    # --profiles が指定されている場合はそれを優先
    if args.profiles:
        # カンマ区切りで分割して正規化
        profile_list = [p.strip() for p in args.profiles.split(",") if p.strip()]
        if not profile_list:
            raise ValueError("--profiles に有効なプロファイルが指定されていません")
        # 複数プロファイルモード
        return _run_multiple_profiles(profile_list, args)

    # --profile を --model-name にマップ（既存の args.model_name に合わせる）
    if args.profile and not args.model_name:
        args.model_name = args.profile

    # 単一プロファイルモード（既存の動作）
    # 戦略プロファイル名を決定（args.profile が優先、なければ args.model_name、それもなければデフォルト）
    profile_name = args.profile or args.model_name or "LightGBM_clf"
    # 単一プロファイル時も JSON 出力とログ保存を行うため、_run_single_profile の結果を処理
    rc, result = _run_single_profile(profile_name, args)

    # JSON出力（最終行のみ）
    if args.emit_json == 1:
        json_line = json.dumps(
            result, ensure_ascii=True, separators=(",", ":"), default=str
        )
        print(json_line, flush=True)

    # ログファイル保存
    retrain_log_dir = LOGS_DIR / "retrain"
    retrain_log_dir.mkdir(parents=True, exist_ok=True)
    last_json_path = retrain_log_dir / "weekly_retrain_last.json"
    jsonl_path = retrain_log_dir / "weekly_retrain.jsonl"

    try:
        # last.json（上書き）
        last_json_path.write_text(
            json.dumps(result, ensure_ascii=True, indent=2, default=str),
            encoding="utf-8",
        )
        # jsonl（追記）
        jsonl_line = json.dumps(result, ensure_ascii=True, separators=(",", ":"), default=str)
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(jsonl_line + "\n")
    except Exception as e:
        # ログ保存失敗は無視（stderrに出力）
        print(f"[WFO][warn] failed to save logs: {e}", file=sys.stderr, flush=True)

    # --out-json が指定されていればそこにも保存
    if args.out_json:
        try:
            out_path = Path(args.out_json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(result, ensure_ascii=True, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[WFO][warn] failed to save --out-json: {e}", file=sys.stderr, flush=True)

    return rc


def _run_single_profile(profile_name: str, args: argparse.Namespace) -> tuple[int, dict]:
    """
    単一プロファイルのWFO実行

    Returns:
        (exit_code, result_dict)
    """
    # 結果JSON構築用の変数
    started_at = jst_now_str()
    profile = profile_name
    step = "init"
    ok = False
    rc = 30  # デフォルトは失敗
    error_info = None
    apply_info = {"performed": False, "reason": "not_applied"}
    outputs = {}
    model_path = None
    meta_path = None
    best_threshold = None
    expected_features = None

    # logs/retrain ディレクトリ準備
    retrain_log_dir = LOGS_DIR / "retrain"
    retrain_log_dir.mkdir(parents=True, exist_ok=True)
    last_json_path = retrain_log_dir / "weekly_retrain_last.json"
    jsonl_path = retrain_log_dir / "weekly_retrain.jsonl"

    try:
        safe_log(
            f"[WFO] start walkforward retrain | profile={profile} symbol={args.symbol} tf={args.timeframe}"
        )
        step = "load_data"

        # args.model_name を profile_name に一時的に上書き（既存ロジックとの互換性）
        original_model_name = args.model_name
        args.model_name = profile_name

        # CSV 探索 & 読み込み
        csv_path = find_csv(args.symbol, args.timeframe, data_dir=args.data_dir)
        print(f"[retrain] using CSV: {csv_path}", file=sys.stderr)
        safe_log(f"[WFO] load csv: {csv_path}")
        df_raw = pd.read_csv(csv_path)

        # 最低限の列チェック
        need_cols = {"time", "open", "high", "low", "close"}
        missing = need_cols - set(df_raw.columns)
        if missing:
            raise ValueError(f"CSV に必要な列が不足しています: {missing}")

        # 特徴量
        step = "build_features"
        feats = build_features(df_raw)
        if feats.empty:
            safe_log("[WFO] feature building aborted (not enough rows).")
            step = "skipped"
            ok = True
            rc = 11
            return rc

        # ラベル
        step = "make_label"
        y = make_label(feats, args.horizon)
        feats = feats.iloc[: -args.horizon, :].reset_index(drop=True)
        y = y.iloc[: -args.horizon].reset_index(drop=True)

        # 特徴量行数チェック
        if feats.shape[0] == 0:
            safe_log(
                "[WFO][error] no rows after feature engineering + horizon alignment. "
                "Likely because rows <= horizon. Provide a longer CSV or reduce --horizon."
            )
            step = "skipped"
            ok = True
            rc = 11
            return rc

        # 説明変数
        step = "prepare_X"
        drop_cols = ["time", "open", "high", "low", "close"]
        X = feats.drop(columns=[c for c in drop_cols if c in feats.columns])

        # 念のための欠損除去
        mask = ~X.isna().any(axis=1)
        X, y = X[mask], y[mask]
        X = X.astype(np.float32)

        n_total = len(X)
        expected_features = list(X.columns)

        if n_total < (args.train_bars + args.test_bars + 1):
            # データが少ない場合は 80/20 の単純スプリットで学習→保存のみ
            step = "train_simple"
            safe_log("[WFO] dataset is small; using simple 80/20 split instead of WFO.")
            Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, shuffle=False)

            clf = train_lgbm(Xtr, ytr)
            prob = clf.predict_proba(Xte)[:, 1]  # DataFrameのまま渡している
            auc = float(roc_auc_score(yte, prob))
            ll = float(log_loss(yte, np.clip(prob, 1e-6, 1 - 1e-6)))

            thr, f1 = pick_threshold(yte.values, prob)
            best_threshold = thr
            safe_log(
                f"[WFO] simple-split auc={auc:.4f} logloss={ll:.4f} thr={thr:.3f} f1={f1:.3f}"
            )

            # 全データ再学習→保存
            step = "train_final"
            final_clf = train_lgbm(X, y)
            model_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            model_path = MODELS_DIR / f"{args.model_name}_{model_ts}.pkl"
            dump(final_clf, model_path)
            meta = {
                "model_name": args.model_name,
                "version": model_ts,
                "features": list(X.columns),
                "horizon": args.horizon,
                "metrics": {"auc": auc, "logloss": ll, "thr": thr, "f1": f1},
                "source_csv": str(csv_path.name),
            }
            meta_path = MODELS_DIR / f"{args.model_name}_{model_ts}.meta.json"
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            outputs = {
                "active_model_json": str(MODELS_DIR / "active_model.json"),
                "model_path": str(model_path),
                "meta_path": str(meta_path),
                "model_name": args.model_name,  # 実際に使用された model_name
            }

            # アクティブモデル更新（--apply のときだけ）
            step = "apply"
            safe_log(f"[WFO] wrote: {model_path.name}, {meta_path.name}")
            if args.dry_run:
                safe_log(
                    "[WFO] DRY-RUN のため active_model.json は更新しません。"
                )
                apply_info = {"performed": False, "reason": "dry_run"}
                step = "done"
                ok = True
                rc = 0
            elif not args.apply:
                safe_log(
                    "[WFO] --apply が指定されていないため active_model.json は更新しません。"
                )
                apply_info = {"performed": False, "reason": "not_specified"}
                step = "done"
                ok = True
                rc = 0
            else:
                # ✅ apply安全装置
                apply_result = _safe_apply_active_model(
                    model_path, meta_path, best_threshold, expected_features
                )
                if apply_result["ok"]:
                    apply_info = {"performed": True, "reason": "updated"}
                    step = "done"
                    ok = True
                    rc = 0
                else:
                    apply_info = {"performed": False, "reason": apply_result["reason"]}
                    if apply_result["reason"] == "same_model":
                        step = "apply_skipped"
                        ok = True
                        rc = 13
                    else:
                        step = "apply"
                        error_info = {
                            "code": apply_result.get("code", "APPLY_FAILED"),
                            "message": apply_result.get("message", "apply failed"),
                            "where": "apply",
                            "trace": apply_result.get("trace", ""),
                        }
                        rc = 30
            return rc

        # --- WFO ---
        step = "wfo"
        safe_log(
            f"[WFO] bars: total={n_total} train={args.train_bars} "
            f"test={args.test_bars} step={args.step_bars}"
        )

        metrics: list[WFOMetrics] = []
        prob_oof = np.full(n_total, np.nan, dtype=np.float64)
        thr_list: list[float] = []

        for fold, s_tr, s_te in iter_wfo_slices(
            X, args.train_bars, args.test_bars, args.step_bars
        ):
            Xtr, ytr = X.iloc[s_tr], y.iloc[s_tr]
            Xte, yte = X.iloc[s_te], y.iloc[s_te]

            # 学習
            clf = train_lgbm(Xtr, ytr)

            # 予測（DataFrameのまま）
            proba = clf.predict_proba(Xte)[:, 1]

            # メトリクス
            try:
                auc = float(roc_auc_score(yte, proba))
            except ValueError:
                auc = float("nan")

            ll = float(log_loss(yte, np.clip(proba, 1e-6, 1 - 1e-6)))
            thr, f1 = pick_threshold(yte.values, proba)

            # OOF へ
            prob_oof[s_te] = proba
            thr_list.append(thr)

            # 期間情報
            t_idx = feats.iloc[s_tr, :]["time"]
            tr_start = str(t_idx.iloc[0]) if len(t_idx) else ""
            tr_end = str(t_idx.iloc[-1]) if len(t_idx) else ""
            t_idx2 = feats.iloc[s_te, :]["time"]
            te_start = str(t_idx2.iloc[0]) if len(t_idx2) else ""
            te_end = str(t_idx2.iloc[-1]) if len(t_idx2) else ""

            m = WFOMetrics(
                fold=fold,
                train_start=tr_start,
                train_end=tr_end,
                test_start=te_start,
                test_end=te_end,
                n_train=len(Xtr),
                n_test=len(Xte),
                auc=auc,
                logloss=ll,
                f1_at_thr=f1,
                thr=thr,
            )
            metrics.append(m)
            safe_log(
                f"[WFO][fold {fold}] auc={auc:.4f} logloss={ll:.4f} "
                f"thr={thr:.3f} f1={f1:.3f} n={len(Xtr)}/{len(Xte)}"
            )

        # WFO 全体まとめ
        valid_idx = ~np.isnan(prob_oof)
        if valid_idx.sum() == 0:
            safe_log("[WFO] no valid test predictions; abort.")
            step = "skipped"
            ok = True
            rc = 11
            return rc

        y_oof = y.values[valid_idx]
        p_oof = prob_oof[valid_idx]
        auc_oof = float(roc_auc_score(y_oof, p_oof))
        ll_oof = float(log_loss(y_oof, np.clip(p_oof, 1e-6, 1 - 1e-6)))
        thr_oof, f1_oof = pick_threshold(y_oof, p_oof)

        # 少し引き気味に（過適合/ズレ対策で 0.95 を掛ける）
        best_thr = float(np.clip(thr_oof * 0.95, 0.05, 0.95))
        best_threshold = best_thr

        safe_log(
            f"[WFO][OOF] auc={auc_oof:.4f} logloss={ll_oof:.4f} "
            f"thr*={best_thr:.3f} (raw={thr_oof:.3f}) f1={f1_oof:.3f}"
        )

        # 全データで最終モデル
        step = "train_final"
        final_clf = train_lgbm(X, y)
        model_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_path = MODELS_DIR / f"{args.model_name}_{model_ts}.pkl"
        dump(final_clf, model_path)

        meta = {
            "model_name": args.model_name,
            "version": model_ts,
            "features": list(X.columns),
            "horizon": args.horizon,
            "oof_metrics": {
                "auc": auc_oof,
                "logloss": ll_oof,
                "thr_oof": thr_oof,
                "f1_oof": f1_oof,
                "thr_final": best_thr,
            },
            "folds": [asdict(m) for m in metrics],
            "source_csv": str(csv_path.name),
            "bars": {
                "total": n_total,
                "train": args.train_bars,
                "test": args.test_bars,
                "step": args.step_bars,
            },
        }
        meta_path = MODELS_DIR / f"{args.model_name}_{model_ts}.meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        outputs = {
            "active_model_json": str(MODELS_DIR / "active_model.json"),
            "model_path": str(model_path),
            "meta_path": str(meta_path),
            "model_name": args.model_name,  # 実際に使用された model_name
        }

        safe_log(f"[WFO] wrote: {model_path.name}, {meta_path.name}")

        # active_model.json 更新（GUI/実運用が読むファイル）: --apply のときだけ
        step = "apply"
        if args.dry_run:
            safe_log(
                "[WFO] DRY-RUN のため active_model.json は更新しません。"
            )
            apply_info = {"performed": False, "reason": "dry_run"}
            step = "done"
            ok = True
            rc = 0
        elif not args.apply:
            safe_log(
                "[WFO] --apply が指定されていないため active_model.json は更新しません。"
            )
            apply_info = {"performed": False, "reason": "not_specified"}
            step = "done"
            ok = True
            rc = 0
        else:
            # ✅ apply安全装置
            apply_result = _safe_apply_active_model(
                model_path, meta_path, best_threshold, expected_features
            )
            if apply_result["ok"]:
                apply_info = {"performed": True, "reason": "updated"}
                step = "done"
                ok = True
                rc = 0
            else:
                apply_info = {"performed": False, "reason": apply_result["reason"]}
                if apply_result["reason"] == "same_model":
                    step = "apply_skipped"
                    ok = True
                    rc = 13
                else:
                    step = "apply"
                    error_info = {
                        "code": apply_result.get("code", "APPLY_FAILED"),
                        "message": apply_result.get("message", "apply failed"),
                        "where": "apply",
                        "trace": apply_result.get("trace", ""),
                    }
                    rc = 30
        safe_log("[WFO] done.")

        # args.model_name を元に戻す
        args.model_name = original_model_name

        ended_at = jst_now_str()
        elapsed_sec = (
            datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ).total_seconds()
        result = {
            "type": "weekly_retrain",
            "ok": ok,
            "profile": profile,
            "step": step,
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_sec": round(elapsed_sec, 1),
            "apply": apply_info,
            "outputs": outputs,
            "error": error_info,
        }
        return rc, result

    except Exception as e:
        # 例外発生時
        step = step or "error"
        ok = False
        rc = 30
        error_info = {
            "code": "UNEXPECTED_ERROR",
            "message": str(e),
            "where": step,
            "trace": traceback.format_exc(),
        }
        ended_at = jst_now_str()
        elapsed_sec = (
            datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ).total_seconds()
        result = {
            "type": "weekly_retrain",
            "ok": ok,
            "profile": profile,
            "step": step,
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_sec": round(elapsed_sec, 1),
            "apply": apply_info,
            "outputs": outputs,
            "error": error_info,
        }
        return rc, result


def _run_multiple_profiles(profile_list: list[str], args: argparse.Namespace) -> int:
    """
    複数プロファイルのWFO実行

    Returns:
        exit_code (最悪コードを採用: 30 > 13 > 11 > 0)
    """
    started_at = jst_now_str()
    per_profile: dict[str, dict] = {}
    all_rcs: list[int] = []

    # 各プロファイルを実行
    for profile_name in profile_list:
        safe_log(f"[WFO] processing profile: {profile_name}")
        try:
            rc, result = _run_single_profile(profile_name, args)
            per_profile[profile_name] = result
            all_rcs.append(rc)
        except Exception as e:
            # プロファイル実行中の例外
            error_result = {
                "type": "weekly_retrain",
                "ok": False,
                "profile": profile_name,
                "step": "exception",
                "started_at": jst_now_str(),
                "ended_at": jst_now_str(),
                "elapsed_sec": 0.0,
                "apply": {"performed": False, "reason": "exception"},
                "outputs": {},
                "error": {
                    "code": "PROFILE_EXCEPTION",
                    "message": str(e),
                    "where": profile_name,
                    "trace": traceback.format_exc(),
                },
            }
            per_profile[profile_name] = error_result
            all_rcs.append(30)

    # 最悪コードを決定（優先順位: 30 > 13 > 11 > 0）
    final_rc = 0
    if 30 in all_rcs:
        final_rc = 30
    elif 13 in all_rcs:
        final_rc = 13
    elif 11 in all_rcs:
        final_rc = 11

    # 全プロファイル成功で ok=True
    all_ok = all(r.get("ok", False) for r in per_profile.values())

    # 最終結果JSON構築
    ended_at = jst_now_str()
    elapsed_sec = (
        datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    ).total_seconds()

    result = {
        "type": "weekly_retrain",
        "ok": all_ok,
        "profile": None,  # 複数プロファイル時は None
        "step": "done" if all_ok else "partial_failure",
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_sec": round(elapsed_sec, 1),
        "apply": {"performed": False, "reason": "multi_profile"},  # 複数時は apply は個別に記録
        "outputs": {},
        "error": None if all_ok else {"code": "PARTIAL_FAILURE", "message": "一部のプロファイルで失敗"},
        "per_profile": per_profile,
    }

    # JSON出力（最終行のみ）
    if args.emit_json == 1:
        json_line = json.dumps(
            result, ensure_ascii=True, separators=(",", ":"), default=str
        )
        print(json_line, flush=True)

    # ログファイル保存
    retrain_log_dir = LOGS_DIR / "retrain"
    retrain_log_dir.mkdir(parents=True, exist_ok=True)
    last_json_path = retrain_log_dir / "weekly_retrain_last.json"
    jsonl_path = retrain_log_dir / "weekly_retrain.jsonl"

    try:
        # last.json（上書き）
        last_json_path.write_text(
            json.dumps(result, ensure_ascii=True, indent=2, default=str),
            encoding="utf-8",
        )
        # jsonl（追記）
        jsonl_line = json.dumps(result, ensure_ascii=True, separators=(",", ":"), default=str)
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(jsonl_line + "\n")
    except Exception as e:
        # ログ保存失敗は無視（stderrに出力）
        print(f"[WFO][warn] failed to save logs: {e}", file=sys.stderr, flush=True)

    # --out-json が指定されていればそこにも保存
    if args.out_json:
        try:
            out_path = Path(args.out_json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(result, ensure_ascii=True, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[WFO][warn] failed to save --out-json: {e}", file=sys.stderr, flush=True)

    return final_rc


def _emit_result_json(result: dict, emit_json: bool) -> None:
    """最終行にJSONを出力（argparseエラー時など）"""
    if not emit_json:
        return
    json_line = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), default=str
    )
    sys.stdout.write(json_line + "\n")
    sys.stdout.flush()


def _entry() -> int:
    """
    エントリポイント。argparseエラーでも拾えるように、main()呼び出し自体を try で囲う
    """
    started_at = None
    result = {
        "type": "weekly_retrain",
        "ok": False,
        "profile": None,
        "step": "init",
        "started_at": None,
        "ended_at": None,
        "elapsed_sec": None,
        "apply": {"performed": False, "reason": ""},
        "outputs": {},
        "error": None,
    }

    # --json / --emit-json の有無は argparse 前に軽く見る（最小）
    emit_json = True
    try:
        # --json または --emit-json を探す
        for opt in ["--json", "--emit-json"]:
            if opt in sys.argv:
                i = sys.argv.index(opt)
                if i + 1 < len(sys.argv):
                    emit_json = (sys.argv[i + 1] != "0")
                break
    except Exception:
        emit_json = True

    try:
        # 既存の main() を呼ぶ（main側で result を組み立てて return code を返す設計ならそれを使う）
        rc = main()
        return int(rc) if rc is not None else 0
    except SystemExit as e:
        # argparse は SystemExit(2) を投げる
        code = int(getattr(e, "code", 1) or 1)
        mapped = 30 if code == 2 else code  # argparseは運用失敗扱いに統一
        result["ok"] = False
        result["step"] = "argparse"
        result["error"] = {
            "code": "ARGPARSE",
            "message": f"SystemExit({code})",
            "where": "argparse",
            "trace": "",
        }
        # JSONには元codeも入れておくと調査が楽
        result["error"]["message"] = f"SystemExit({code})"
        _emit_result_json(result, emit_json)
        return mapped
    except Exception as ex:
        result["ok"] = False
        result["step"] = "exception"
        result["error"] = {
            "code": "EXCEPTION",
            "message": str(ex),
            "where": "entry",
            "trace": traceback.format_exc(),
        }
        _emit_result_json(result, emit_json)
        return 30


if __name__ == "__main__":
    raise SystemExit(_entry())
