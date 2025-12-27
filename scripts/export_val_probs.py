# scripts/export_val_probs.py
from __future__ import annotations

import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import atexit, math, json
from datetime import datetime, timezone, timedelta
from typing import Any

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from app.core.symbol_map import resolve_symbol
from loguru import logger

from core.ai.loader import load_lgb_clf  # 既存ローダを利用
from pathlib import Path

# ====== 設定 ======
SYMBOL = "USDJPY"                 # ブローカー接尾辞は自動吸収します
TIMEFRAME = mt5.TIMEFRAME_M5
START = "2025-08-01 00:00:00"     # 検証開始（JST）
END   = "2025-10-01 00:00:00"     # 検証終了（JST）
BARS_MIN = 400                    # 最低必要バー数（EMA/BB等のため）
OUT_PBUY = Path("logs/val_p_buy_raw.npy")
OUT_Y    = Path("logs/val_y_true.npy")
OUT_META = Path("logs/val_export_meta.json")

# ====== MT5初期化 ======
def _ensure_symbol(symbol: str) -> str:
    if mt5.symbol_select(resolve_symbol(\), True):
        return symbol
    upper = symbol.upper()
    cands = [s.name for s in mt5.symbols_get() if s.name.upper().startswith(upper)]
    if not cands:
        raise RuntimeError(f"no candidates for '{symbol}'")
    best = sorted(cands, key=len)[0]
    best = str(best)
    if not mt5.symbol_select(resolve_symbol(\), True):
        raise RuntimeError(f"symbol_select failed for '{best}'")
    return best


def _read_feature_order(meta_path: str) -> list[str]:
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta: dict[str, Any] = json.load(fh)
        return list(meta.get("feature_order", []))
    except Exception:
        return []

def _to_utc(jst_str: str) -> datetime:
    jst = datetime.strptime(jst_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=9)))
    return jst.astimezone(timezone.utc)

# ====== 特徴量（dryrunと同じ定義） ======
def make_features_df(df: pd.DataFrame) -> pd.DataFrame:
    close, high, low, open_ = df["close"], df["high"], df["low"], df["open"]

    ema_5  = close.ewm(span=5, adjust=False).mean()
    ema_20 = close.ewm(span=20, adjust=False).mean()

    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1/14, adjust=False).mean()
    roll_down = down.ewm(alpha=1/14, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, np.nan)
    rsi_14 = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    tr1 = (high - low).abs()
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_14_abs = tr.ewm(alpha=1/14, adjust=False).mean()
    atr_14 = (atr_14_abs / close.replace(0, np.nan)).fillna(0.0)

    plus_dm  = (high.diff()).clip(lower=0.0)
    minus_dm = (-low.diff()).clip(lower=0.0)
    plus_dm[plus_dm < minus_dm] = 0.0
    minus_dm[minus_dm <= plus_dm] = 0.0
    tr_smooth = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / tr_smooth.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx_14 = dx.ewm(alpha=1/14, adjust=False).mean().fillna(20.0)

    bb_ma = close.rolling(20).mean()
    bb_std = close.rolling(20).std(ddof=0)
    bb_upper = bb_ma + 2 * bb_std
    bb_lower = bb_ma - 2 * bb_std
    bbp = ((close - bb_lower) / (bb_upper - bb_lower)).replace([np.inf, -np.inf], np.nan).clip(0.0, 1.0).fillna(0.5)

    # volume由来の特徴は省略（学習時に使っていれば追加）
    body_high = np.maximum(open_, close)
    body_low  = np.minimum(open_, close)
    upper_wick = (high - body_high).clip(lower=0.0)
    lower_wick = (body_low - low).clip(lower=0.0)
    rng = (high - low).replace(0, np.nan)
    wick_ratio = ((upper_wick + lower_wick) / rng).clip(0.0, 1.0).fillna(0.0)

    out = pd.DataFrame({
        "ema_5": ema_5 - ema_20,
        "ema_20": (ema_20 - close) / close.replace(0, np.nan),
        "rsi_14": rsi_14,
        "atr_14": atr_14,
        "adx_14": adx_14,
        "bbp": bbp,
        "wick_ratio": wick_ratio,
    })
    return out.replace([np.inf, -np.inf], 0.0).fillna(0.0)

def main() -> None:
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}")
    atexit.register(mt5.shutdown)

    symbol = _ensure_symbol(SYMBOL)

    # 余裕を持って過去から取得
    utc_from = _to_utc(START) - timedelta(days=7)
    utc_to   = _to_utc(END)
    rates = mt5.copy_rates_range(symbol, TIMEFRAME, utc_from, utc_to)
    if rates is None or len(rates) < BARS_MIN:
        raise SystemExit(f"not enough bars: {len(rates) if rates is not None else 0}")

    df = pd.DataFrame(rates)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
    df = df[ (df["ts"] >= pd.Timestamp(START, tz="Asia/Tokyo")) & (df["ts"] < pd.Timestamp(END, tz="Asia/Tokyo")) ].copy()
    df = df.reset_index(drop=True)

    feats = make_features_df(df)
    # ラベル定義（次バーの上げ下げ、同値は0とする）
    y_true = (df["close"].shift(-1) > df["close"]).astype(int)[:-1].values
    feats = feats.iloc[:-1, :].copy()
#
    # ===== モデルと列順（返り値の型差を吸収） =====
    lm = load_lgb_clf()  # LoadedModel / sklearn LGBMClassifier / lightgbm.Booster 等

    # モデル本体候補を広く探索
    candidates = [lm]
    for attr in ("model", "clf", "estimator", "inner", "wrapped", "lgbm", "booster", "booster_"):
        if hasattr(lm, attr):
            candidates.append(getattr(lm, attr))

    model = None
    for cand in candidates:
        if hasattr(cand, "predict_proba"):
            model = cand  # sklearn 互換
            use_proba = "predict_proba"
            break
        if hasattr(cand, "predict"):
            model = cand  # Booster 等（predictで確率が返る想定）
            use_proba = "predict"
            # break しないで続けてもいいが、まずはこれで採用
            break

    if model is None:
        raise TypeError("No usable model found: neither predict_proba nor predict detected.")

    # 特徴量順（存在しなければ現在列で進む）
    try:
        order = _read_feature_order("models/LightGBM_clf.features.json")
    except Exception:
        order = None
    if not order:
        order = list(feats.columns)

    # 欠け列は0で補い、余分は落とす
    for col in order:
        if col not in feats.columns:
            feats[col] = 0.0
    X = feats[order].astype(float)

    # ===== 生BUY確率の取得 =====
    if use_proba == "predict_proba":
        proba = model.predict_proba(X)  # (N,2 or 3)
    else:
        # LightGBM Booster 等: predict で確率が返る（binary は陽性確率、multiclass は (N, K)）
        # raw_score=False を渡せる場合は渡す（無くてもOK）
        try:
            proba = model.predict(X, raw_score=False)
        except TypeError:
            proba = model.predict(X)

    # shape を正規化
    proba = np.asarray(proba)
    if proba.ndim == 1:
        # binary の陽性確率が 1 列で返ったケース
        p_buy_raw = proba
    elif proba.ndim == 2:
        # (N,2) or (N,K) を想定。BUY列（陽性）を列1と仮定（必要なら調整）
        if proba.shape[1] >= 2:
            p_buy_raw = proba[:, 1]
        else:
            # よほどの特殊形状。安全側で最大スコア列をBUYとみなす
            idx = np.argmax(proba, axis=1)
            p_buy_raw = (idx == 1).astype(float)
    else:
        raise ValueError(f"Unsupported prediction output shape: {proba.shape}")

    OUT_PBUY.parent.mkdir(parents=True, exist_ok=True)
    np.save(OUT_PBUY, p_buy_raw.astype(float))
    np.save(OUT_Y,    y_true.astype(int))
    OUT_META.write_text(json.dumps({
        "symbol": symbol,
        "timeframe": "M5",
        "start": START,
        "end": END,
        "N": int(len(p_buy_raw)),
        "feature_order_used": order,
        "inferred_api": use_proba,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"wrote: {OUT_PBUY} ({len(p_buy_raw)}), {OUT_Y} ({len(y_true)}), api={use_proba}")

#
if __name__ == "__main__":
    main()

