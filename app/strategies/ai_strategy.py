# app/strategies/ai_strategy.py
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import numpy as np
import math
import joblib
import pickle
import binascii
from typing import Tuple, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def _load_model_generic(path_str: str):
    """
    1) joblib.load()
    2) pickle.load()
    3) LightGBM Booster（.txtやバイナリ）
       └ pklが失敗したら「同名.txt」にも自動フォールバック
    全滅時は先頭バイトをdumpして原因特定メッセージを返す。
    """
    p = Path(path_str)

    # 1) joblib
    try:
        import joblib
        m = joblib.load(p)
        print(f"[wfo] model loaded via joblib: {p.name}", flush=True)
        return m
    except Exception as e1:
        e1_msg = str(e1)

    # 2) pickle
    try:
        with open(p, "rb") as f:
            m = pickle.load(f)
        print(f"[wfo] model loaded via pickle: {p.name}", flush=True)
        return m
    except Exception as e2:
        e2_msg = str(e2)

    # 3) Booster
    def _try_booster(mp: Path):
        import lightgbm as lgb
        booster = lgb.Booster(model_file=str(mp))
        class _BoosterWrapper:
            def __init__(self, bst): self.bst = bst
            def predict_proba(self, X):
                prob1 = self.bst.predict(X)
                prob1 = np.asarray(prob1).reshape(-1)
                prob0 = 1.0 - prob1
                return np.vstack([prob0, prob1]).T
        print(f"[wfo] model loaded via booster: {mp.name}", flush=True)
        return _BoosterWrapper(booster)

    e3_msg = ""
    try:
        return _try_booster(p)
    except Exception as e3:
        e3_msg = str(e3)

    # pkl→txt フォールバック
    alt_txt = p.with_suffix(".txt")
    if alt_txt.exists():
        try:
            return _try_booster(alt_txt)
        except Exception as e4:
            e3_msg += f" | alt_txt='{alt_txt.name}': {e4}"

    # 先頭バイトを表示
    try:
        head = p.read_bytes()[:16]
        head_hex = binascii.hexlify(head).decode("ascii")
    except Exception:
        head_hex = "unreadable"

    raise RuntimeError(
        f"model load failed: joblib='{e1_msg}' | pickle='{e2_msg}' | booster='{e3_msg}' | head={head_hex}"
    )

    
# =====================================================
# active_model.json の読み込み
# =====================================================
def load_active_model() -> Tuple[str, str, float, Dict[str, Any]]:
    meta = PROJECT_ROOT / "active_model.json"
    if not meta.exists():
        raise FileNotFoundError(f"{meta} not found.")

    j = json.loads(meta.read_text(encoding="utf-8"))
    model_name = j.get("model_name", "").strip()
    threshold = float(j.get("best_threshold", 0.5))
    params = j.get("params", {}) or {}

    if model_name.startswith("builtin_"):
        return ("builtin", model_name, threshold, params)

    # 外部モデル
    pkl = PROJECT_ROOT / "models" / f"{model_name}.pkl"
    txt = PROJECT_ROOT / "models" / f"{model_name}.txt"

    # ここを txt 優先にする
    if txt.exists():
        return ("pickle", str(txt), threshold, params)
    if pkl.exists():
        return ("pickle", str(pkl), threshold, params)

    raise FileNotFoundError(f"Model file not found: {pkl} nor {txt}")


# =====================================================
# 特徴量レシピ
# =====================================================
def _rsi(x: pd.Series, period: int = 14) -> pd.Series:
    delta = x.diff()
    up = (delta.clip(lower=0)).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / (down + 1e-12)
    return 100 - (100 / (1 + rs))

def _ema(x: pd.Series, span: int) -> pd.Series:
    return x.ewm(span=span, adjust=False).mean()

def _bbands(x: pd.Series, window: int = 20, n_sigma: float = 2.0):
    ma = x.rolling(window).mean()
    sd = x.rolling(window).std(ddof=0)
    upper = ma + n_sigma * sd
    lower = ma - n_sigma * sd
    return upper, lower

def _stoch(high: pd.Series, low: pd.Series, close: pd.Series, k_win: int = 14, d_win: int = 3):
    ll = low.rolling(k_win).min()
    hh = high.rolling(k_win).max()
    k = (close - ll) / (hh - ll + 1e-12) * 100
    d = k.rolling(d_win).mean()
    return k, d

def build_features_recipe(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """
    内蔵レシピで特徴量を作成。time列は残します。
    name:
      - "ohlcv_tech_v1": 代表的なテクニカル群
    """
    out = df.copy()
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low  = out["low"].astype(float)

    if name == "ohlcv_tech_v1":
        out["ret1"]  = close.pct_change()
        out["ret5"]  = close.pct_change(5)
        out["ret20"] = close.pct_change(20)

        out["sma_10"] = close.rolling(10, min_periods=1).mean()
        out["sma_50"] = close.rolling(50, min_periods=1).mean()
        out["ema_20"] = _ema(close, 20)

        out["rsi_14"] = _rsi(close, 14)
        u,l = _bbands(close, 20, 2.0)
        out["bb_high_20_2"] = u
        out["bb_low_20_2"]  = l

        k,d = _stoch(high, low, close, 14, 3)
        out["stoch_k_14_3"] = k
        out["stoch_d_14_3"] = d

        # シンプルなATR風（高低差のEMA）
        tr = (high - low).abs()
        out["atr_14"] = tr.rolling(14).mean()

        # ボラ指標
        out["vol_pct_20"] = close.pct_change().rolling(20).std() * math.sqrt(20)

        # -------------------------------------------------
        # feature name alias / compatibility (最小差分)
        # active_model.json の expected_features（例: ret_1 等）に合わせて列を補完する。
        # - 既存列は壊さない（不足分のみ追加）
        # - “推測で別名を当てる”のではなく、現レシピで確定している列/元データ列から埋める
        # -------------------------------------------------
        # ret_1 / ret_5: 既存の ret1 / ret5 から copy
        if "ret_1" not in out.columns and "ret1" in out.columns:
            out["ret_1"] = out["ret1"]
        if "ret_5" not in out.columns and "ret5" in out.columns:
            out["ret_5"] = out["ret5"]

        # ema_5 / ema_ratio: close と ema_20（本レシピ既存）から補完
        if "ema_5" not in out.columns:
            out["ema_5"] = _ema(close, 5)
        if "ema_ratio" not in out.columns:
            out["ema_ratio"] = out["ema_5"] / (out["ema_20"] + 1e-12)

        # range: 高低差（本レシピ内でも tr として使用している値）を列として残す
        if "range" not in out.columns:
            out["range"] = (high - low).abs()

        # vol_chg: tick_volume / real_volume がある場合のみ生成
        if "vol_chg" not in out.columns:
            if "tick_volume" in out.columns:
                out["vol_chg"] = pd.to_numeric(out["tick_volume"], errors="coerce").pct_change()
            elif "real_volume" in out.columns:
                out["vol_chg"] = pd.to_numeric(out["real_volume"], errors="coerce").pct_change()

    else:
        raise ValueError(f"unknown feature recipe: {name}")

    out = out.dropna().reset_index(drop=True)
    return out

def build_features(df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """外部モデル・内蔵双方で使う特徴量ビルドの統一入口"""
    recipe = (params or {}).get("feature_recipe", "ohlcv_tech_v1")
    feat = build_features_recipe(df, recipe)
    return feat

# =====================================================
# 予測とシグナル
# =====================================================
def _load_scaler_if_any(params: Dict[str, Any]):
    name = params.get("scaler_name")
    if not name:
        return None
    p = PROJECT_ROOT / "models" / "scalers" / f"{name}.pkl"
    if not p.exists():
        raise FileNotFoundError(f"Scaler not found: {p}")

    # まずヘッダを見て形式推定
    head_hex = None
    try:
        b = p.read_bytes()
        head_hex = binascii.hexlify(b[:8]).decode("ascii")
        # NumPy .npy の典型ヘッダは \x93NUMPY (= 93 4e 55 4d 50 59)
        is_npy = b[:6] == b"\x93NUMPY"
    except Exception:
        is_npy = False

    # 1) 標準pickle
    if not is_npy:
        try:
            with open(p, "rb") as f:
                scaler = pickle.load(f)
            typename = type(scaler).__name__
            print(f"[wfo] using scaler: {name} ({typename})", flush=True)
            return scaler
        except Exception as e1:
            # 2) joblib
            try:
                import joblib
                scaler = joblib.load(p)
                typename = type(scaler).__name__
                print(f"[wfo] using scaler: {name} ({typename}) via joblib", flush=True)
                return scaler
            except Exception as e2:
                # 3) 最後に NumPy ローダ
                try:
                    arr = np.load(str(p), allow_pickle=True)
                    # .npz の場合は最初のキーを取り出す
                    if hasattr(arr, "files"):
                        key0 = arr.files[0]
                        arr = arr[key0]
                    print(f"[wfo] using scaler: {name} (ndarray via np.load)", flush=True)
                    return arr
                except Exception as e3:
                    raise RuntimeError(
                        f"Scaler load failed: pickle='{e1}' | joblib='{e2}' | numpy='{e3}' | head={head_hex}"
                    )

    # ヘッダで .npy と判定された場合は最初から NumPy
    try:
        arr = np.load(str(p), allow_pickle=True)
        if hasattr(arr, "files"):
            key0 = arr.files[0]
            arr = arr[key0]
        print(f"[wfo] using scaler: {name} (ndarray via np.load)", flush=True)
        return arr
    except Exception as e:
        raise RuntimeError(f"Scaler load failed (npy fast-path): {e} | head={head_hex}")


def _ensure_feature_order(feat_df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    cols = params.get("feature_cols")
    if cols:
        # 明示された列順に合わせ、不足はエラー、余分は削除
        missing = [c for c in cols if c not in feat_df.columns]
        if missing:
            raise ValueError(f"Missing features for model: {missing}")
        return feat_df.loc[:, cols]
    # 明示なしなら、price系を除いた派生列をアルファベット順で安定化
    skip = {"time","open","high","low","close","tick_volume","real_volume","spread"}
    cols = [c for c in feat_df.columns if c not in skip]
    return feat_df.loc[:, sorted(cols)]

def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))

def _predict_proba_generic(model, X) -> np.ndarray:
    """
    LightGBM / XGBoost / Sklearn いずれでも陽性確率を返す汎用ハンドラー

    重要:
      - sklearn(LGBMClassifier含む) は feature_names_in_ を持つ場合があり、
        ndarray を渡すと "X does not have valid feature names" warning になる。
      - そのため sklearn系では可能な限り DataFrame(列名あり) を維持して predict_proba に渡す。
    """
    import lightgbm as lgb

    # ----------------------------
    # 1) LightGBM Booster 系（列名不要）
    # ----------------------------
    if isinstance(model, lgb.Booster):
        Xb = X.values if isinstance(X, pd.DataFrame) else (np.asarray(X) if not isinstance(X, np.ndarray) else X)
        try:
            prob1 = model.predict(Xb)
        except Exception as e:
            print(f"[wfo] warn: Booster.predict failed: {e}  -> fallback sigmoid(raw score)", flush=True)
            raw = model.predict(Xb, raw_score=True)
            prob1 = 1.0 / (1.0 + np.exp(-raw))
        prob1 = np.asarray(prob1).reshape(-1)
        prob0 = 1.0 - prob1
        return np.vstack([prob0, prob1]).T

    # ラッパー（_BoosterWrapper）なら predict_proba を直接呼ぶ（列名不要）
    if hasattr(model, "bst") and hasattr(model.bst, "predict"):
        Xb = X.values if isinstance(X, pd.DataFrame) else (np.asarray(X) if not isinstance(X, np.ndarray) else X)
        try:
            prob1 = model.bst.predict(Xb)
            prob1 = np.asarray(prob1).reshape(-1)
            prob0 = 1.0 - prob1
            return np.vstack([prob0, prob1]).T
        except Exception as e:
            print(f"[wfo] warn: BoosterWrapper predict failed: {e}", flush=True)

    # ----------------------------
    # 2) Sklearn系：predict_proba（feature names を尊重）
    # ----------------------------
    if hasattr(model, "predict_proba"):
        # Ensure feature names are preserved for sklearn-wrapped LightGBM (avoid warnings)
        # Some pickled LGBMClassifier may not keep feature_names_in_ but may keep feature_name_ / booster_.feature_name()
        cols = None
        if hasattr(model, "feature_names_in_"):
            cols = list(getattr(model, "feature_names_in_"))
        elif hasattr(model, "feature_name_"):
            try:
                cols = list(getattr(model, "feature_name_"))
            except Exception:
                cols = None
        elif hasattr(model, "booster_") and hasattr(model.booster_, "feature_name"):
            try:
                cols = list(model.booster_.feature_name())
            except Exception:
                cols = None

        if cols:
            if isinstance(X, pd.DataFrame):
                missing = [c for c in cols if c not in X.columns]
                if missing:
                    raise ValueError(f"Missing features for model: {missing}")
                Xs = X.loc[:, cols]
            else:
                Xa = np.asarray(X) if not isinstance(X, np.ndarray) else X
                if Xa.ndim == 1:
                    Xa = Xa.reshape(1, -1)
                Xs = pd.DataFrame(Xa, columns=cols)
        else:
            # No reliable feature names -> pass through (may warn for some estimators, but we did our best)
            Xs = X.values if isinstance(X, pd.DataFrame) else (np.asarray(X) if not isinstance(X, np.ndarray) else X)

        proba = model.predict_proba(Xs)
        proba = np.asarray(proba)
        if proba.ndim == 1:
            return proba
        if proba.shape[1] == 2:
            return proba[:, 1]
        return proba[:, -1]

    # ----------------------------
    # 3) decision_function（SVMなど）
    # ----------------------------
    if hasattr(model, "decision_function"):
        Xa = X.values if isinstance(X, pd.DataFrame) else (np.asarray(X) if not isinstance(X, np.ndarray) else X)
        score = model.decision_function(Xa)
        return _sigmoid(score)

    # ----------------------------
    # 4) それ以外は predict() の結果を確率扱い
    # ----------------------------
    Xa = X.values if isinstance(X, pd.DataFrame) else (np.asarray(X) if not isinstance(X, np.ndarray) else X)
    pred = model.predict(Xa)
    return np.asarray(pred).astype(float)
def predict_signals(kind: str, payload, df_feat: pd.DataFrame, threshold: float = 0.0, params=None) -> pd.Series:
    """
    - builtin_sma: fast/slow のクロスで +1/-1 を返す
    - pickle: 外部モデルの proba > threshold → +1、それ以外 → -1（long_short）等、params['mode'] で制御
    """
    params = params or {}
    mode = params.get("mode", "long_short")  # long_only / short_only / long_flat / long_short

    if kind == "builtin":
        name = str(payload)
        if name == "builtin_sma":
            fast = int(params.get("fast", 10))
            slow = int(params.get("slow", 50))
            sma_fast = df_feat["close"].rolling(fast, min_periods=1).mean()
            sma_slow = df_feat["close"].rolling(slow, min_periods=1).mean()
            raw = np.where(sma_fast > sma_slow, 1, -1)
        else:
            raise ValueError(f"unknown builtin model: {name}")

    else:
        # 外部モデル推論
        model = _load_model_generic(payload)
        X = _ensure_feature_order(df_feat, params)
        scaler = _load_scaler_if_any(params)
        if scaler is not None:
            Xv = X.values
            try:
                # 標準のsklearn系（StandardScaler など）
                Xv = scaler.transform(Xv)
            except AttributeError:
                # dict / (mean, scale) / ndarray を許容
                if isinstance(scaler, dict) and ("mean" in scaler or "scale" in scaler):
                    mean = np.asarray(scaler.get("mean", np.zeros(Xv.shape[1])))
                    scale = np.asarray(scaler.get("scale", np.ones(Xv.shape[1])))
                    Xv = (Xv - mean) / (scale + 1e-12)
                elif isinstance(scaler, (tuple, list)) and len(scaler) >= 2:
                    mean = np.asarray(scaler[0])
                    scale = np.asarray(scaler[1])
                    Xv = (Xv - mean) / (scale + 1e-12)
                elif isinstance(scaler, np.ndarray):
                    # 平均だけが保存されているケース
                    mean = scaler
                    Xv = (Xv - mean)
                else:
                    # 想定外の型は未スケールで続行
                    print(f"[wfo] warn: unknown scaler type -> skip scaling ({type(scaler).__name__})", flush=True)
            # DataFrameに戻す（列名は維持）
            X = pd.DataFrame(Xv, index=X.index, columns=X.columns)
        ##
        proba = _predict_proba_generic(model, X)

        # 2次元(=確率2列)なら陽性側だけを採用
        if proba.ndim == 2 and proba.shape[1] == 2:
            proba = proba[:, 1]

        raw = (proba > float(threshold)).astype(int)  # 1 or 0
        ##
        # raw(1/0) → モードごとの最終signal
        if mode == "long_only" or mode == "long_flat":
            # 1=long, 0=flat
            sig = np.where(raw==1, 1, 0)
            return pd.Series(sig, index=df_feat.index, name="signal")
        elif mode == "short_only":
            # 1=flat, 0=short
            sig = np.where(raw==1, 0, -1)
            return pd.Series(sig, index=df_feat.index, name="signal")
        else:
            # long_short: 1=long, 0=short
            sig = np.where(raw==1, 1, -1)
            return pd.Series(sig, index=df_feat.index, name="signal")

    # builtinのモード切替
    if mode == "long_only" or mode == "long_flat":
        sig = np.where(raw==1, 1, 0)
    elif mode == "short_only":
        sig = np.where(raw==1, 0, -1)
    else:
        sig = raw
    return pd.Series(sig, index=df_feat.index, name="signal")

# =====================================================
# トレード生成（動的サイズ・ロング/ショート対応）
# =====================================================
def trades_from_signals(df_feat: pd.DataFrame, initial_capital: float, params=None) -> pd.DataFrame:
    """
    signal列（+1/-1/0）に基づいて IN/OUT/反転。
    ポジションサイズは equity * risk_pct / price を lot_step で丸め。
    PnL = (exit - entry) * dir * units - (spread+commission) * units
    """
    if "signal" not in df_feat.columns:
        raise ValueError("signal列が必要です。")

    p = params or {}
    spread_pips = float(p.get("spread_pips", 0.2))
    risk_pct = float(p.get("risk_pct", 0.01))
    lot_step = int(p.get("lot_step", 1000))
    min_units = int(p.get("min_units", lot_step))
    max_units = int(p.get("max_units", 200000))
    commission_per_unit = float(p.get("commission_per_unit", 0.0))

    spread_yen_per_unit = spread_pips * 0.01
    fee_yen_per_unit = commission_per_unit
    cost_yen_per_unit = spread_yen_per_unit + fee_yen_per_unit

    position = 0           # +1 / -1 / 0
    entry_price = None
    entry_time = None
    units = 0
    equity = float(initial_capital)

    trades = []
    idx_time = df_feat["time"].tolist()
    prices  = df_feat["close"].astype(float).tolist()
    sigs    = df_feat["signal"].astype(int).tolist()

    def _round_units(u: float) -> int:
        if u <= 0:
            return 0
        u = int(u // lot_step * lot_step)
        return max(min_units, min(u, max_units)) if u > 0 else 0

    # entry_time の行番号検索を高速化するための辞書
    time_to_index = {t:i for i,t in enumerate(idx_time)}

    for i in range(len(df_feat)):
        sig = sigs[i]
        price = prices[i]
        t = idx_time[i]

        if sig != position:
            if position != 0 and entry_price is not None and units > 0:
                pnl = (price - entry_price) * position * units - cost_yen_per_unit * units
                equity += pnl
                et_idx = time_to_index.get(entry_time, i)
                trades.append({
                    "entry_time": entry_time,
                    "exit_time": t,
                    "pnl": float(pnl),
                    "direction": "LONG" if position > 0 else "SHORT",
                    "units": int(units),
                    "entry_price": float(entry_price),
                    "exit_price": float(price),
                    "holding_bars": i - et_idx,
                    "holding_days": (pd.Timestamp(t) - pd.Timestamp(entry_time)).days,
                    "win": int(pnl > 0),
                    "equity_after": float(equity)
                })

            position = sig
            entry_price = price
            entry_time = t
            if position != 0:
                raw_units = (equity * risk_pct) / max(price, 1e-9)
                units = _round_units(raw_units)
            else:
                units = 0

    if position != 0 and entry_price is not None and units > 0:
        price = prices[-1]
        t = idx_time[-1]
        pnl = (price - entry_price) * position * units - cost_yen_per_unit * units
        equity += pnl
        et_idx = time_to_index.get(entry_time, len(df_feat)-1)
        trades.append({
            "entry_time": entry_time,
            "exit_time": t,
            "pnl": float(pnl),
            "direction": "LONG" if position > 0 else "SHORT",
            "units": int(units),
            "entry_price": float(entry_price),
            "exit_price": float(price),
            "holding_bars": (len(df_feat)-1) - et_idx,
            "holding_days": (pd.Timestamp(t) - pd.Timestamp(entry_time)).days,
            "win": int(pnl > 0),
            "equity_after": float(equity)
        })

    cols = [
        "entry_time","exit_time","pnl","direction","units",
        "entry_price","exit_price","holding_bars","holding_days","win","equity_after"
    ]
    return pd.DataFrame(trades, columns=cols)

