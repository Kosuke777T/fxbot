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
    if mt5.symbol_select(resolve_symbol(symbol), True):
        return symbol
    upper = symbol.upper()
    cands = [s.name for s in mt5.symbols_get() if s.name.upper().startswith(upper)]
    if not cands:
        raise RuntimeError(f"no candidates for '{symbol}'")
    best = sorted(cands, key=len)[0]
    best = str(best)
    if not mt5.symbol_select(resolve_symbol(symbol), True):
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
    # LGBMClassifier (base_model) が要求する 20特徴に合わせる
    # ['open','high','low','close','tick_volume','spread','real_volume',
    #  'ret1','ret5','ret20','sma_10','sma_50','ema_20','rsi_14',
    #  'bb_high_20_2','bb_low_20_2','stoch_k_14_3','stoch_d_14_3',
    #  'atr_14','vol_pct_20']

    out = pd.DataFrame(index=df.index)

    # 0) raw columns（無ければ0埋め）
    for c in ["open","high","low","close","tick_volume","spread","real_volume"]:
        if c in df.columns:
            out[c] = df[c].astype(float)
        else:
            out[c] = 0.0

    close = out["close"]
    high  = out["high"]
    low   = out["low"]
    tv    = out["tick_volume"]

    # 1) returns
    out["ret1"]  = (close / close.shift(1)  - 1.0)
    out["ret5"]  = (close / close.shift(5)  - 1.0)
    out["ret20"] = (close / close.shift(20) - 1.0)

    # 2) MA/EMA
    out["sma_10"] = close.rolling(10, min_periods=1).mean()
    out["sma_50"] = close.rolling(50, min_periods=1).mean()
    out["ema_20"] = close.ewm(span=20, adjust=False).mean()

    # 3) RSI(14)（Wilderに近い平滑）
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1/14, adjust=False).mean()
    roll_down = down.ewm(alpha=1/14, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, np.nan)
    out["rsi_14"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    # 4) Bollinger (20, 2)
    mid = close.rolling(20, min_periods=1).mean()
    sd  = close.rolling(20, min_periods=1).std(ddof=0).fillna(0.0)
    out["bb_high_20_2"] = mid + 2.0 * sd
    out["bb_low_20_2"]  = mid - 2.0 * sd

    # 5) Stoch (14,3) %K and %D
    ll14 = low.rolling(14, min_periods=1).min()
    hh14 = high.rolling(14, min_periods=1).max()
    denom = (hh14 - ll14).replace(0, np.nan)
    k = ((close - ll14) / denom * 100.0).fillna(50.0)
    out["stoch_k_14_3"] = k.rolling(3, min_periods=1).mean()
    out["stoch_d_14_3"] = out["stoch_k_14_3"].rolling(3, min_periods=1).mean()

    # 6) ATR(14)
    tr1 = (high - low).abs()
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    out["atr_14"] = tr.ewm(alpha=1/14, adjust=False).mean()

    # 7) vol_pct_20（tick_volume の 20期間変化率：現在/20本前 - 1）
    out["vol_pct_20"] = (tv / tv.shift(20) - 1.0)

    # clean
    return out.replace([np.inf, -np.inf], 0.0).fillna(0.0)

def main() -> None:
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}")
    atexit.register(mt5.shutdown)

    symbol = _ensure_symbol(SYMBOL)

    # 余裕を持って過去から取得
    utc_from = _to_utc(START) - timedelta(days=7)
    utc_to   = _to_utc(END)
    rates = mt5.copy_rates_range(resolve_symbol(symbol), TIMEFRAME, utc_from, utc_to)
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
        # feature order: model が持つ feature_name_ を最優先（運用でのモデル切替に強い）        base = getattr(model, "base_model", None) or getattr(model, "_base_model", None)
        order = list(getattr(base, "feature_name_", []) or [])
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







