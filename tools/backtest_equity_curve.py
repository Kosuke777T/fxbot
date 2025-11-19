# tools/backtest_equity_curve.py
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))  # ←これを追加

import argparse, json, glob, os
from typing import Any
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from core.ai.loader import load_lgb_clf
from core.ai.features import build_features

def _load_active_meta() -> dict[str, Any]:
    p = "models/active_model.json"
    j = json.load(open(p, encoding="utf-8"))
    best_t = float(j.get("best_threshold", 0.2))
    lookahead = int(j.get("selected_lookahead", 15))
    return {"best_threshold": best_t, "lookahead": lookahead}

def _load_dataset(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    # 必要列: time/open/high/low/close/volume を想定
    if not set(["open","high","low","close","volume"]).issubset(df.columns):
        raise ValueError("CSVにOHLCV列が不足しています")
    df = build_features(df)  # 学習時と同じ拡張20列
    df = df.dropna().reset_index(drop=True)
    return df

def _ensure_feature_order(df: pd.DataFrame, model: Any) -> tuple[pd.DataFrame, list[str]]:
    # ラッパは model.expected_features を持たせてある想定（無ければ推定）
    feat = getattr(model, "expected_features", None)
    if feat is None:
        # モデル側に無い場合は、学習で使っていそうな20列を拾う（応急）
        candidates = ["open","high","low","close","volume",
                      "ret_1","ret_3","ret_5","ret_10",
                      "ret_std_10","ret_std_20",
                      "tr","atr_14","rsi_14","adx_14","bbp_20",
                      "upper_wick_ratio","lower_wick_ratio","body_ratio","vol_zscore_20"]
        feat = [c for c in candidates if c in df.columns]
    X = df[feat].copy()
    return X, feat

def run_backtest(csv_path: str, out_csv: str, init_equity: float = 100_000.0, show: bool = True) -> None:
    meta = _load_active_meta()
    best_t = float(meta.get("best_threshold", 0.2))
    L = int(meta.get("lookahead", 15))
    model = load_lgb_clf("models/LightGBM_clf.pkl")  # calib付きでロードされる
    df_raw = pd.read_csv(csv_path)
    df = _load_dataset(csv_path)
    X, feat = _ensure_feature_order(df, model)

    # (B) build_features → dropna 後に行数チェック
    # 窓を使う指標（RSI14/ATR14/ret_std_20 等）で冒頭がNaNになるため、
    # 短すぎるCSVだと0行になってしまう。足りない場合は早期終了して案内。
    if X.shape[0] == 0 or X.shape[0] < 40:
        print("[bt][error] too few rows after feature engineering (need ≈40+ rows). "
              "Your CSV is too short; provide more bars (100+ recommended).")
        return

    # 予測
    # (A) 列名付き DataFrame のまま渡す（学習時の列名を維持）
    proba = model.predict_proba(X)  # shape (n,2) を想定（[neg, pos]）
    # ここでは [p_sell, p_buy] ではなく [p0,p1]=[neg,pos] を buy=pos とみなす
    if proba.shape[1] == 2:
        p_buy = proba[:,1]
        p_sell = proba[:,0]
    else:
        # 2クラスでない場合の保険
        p_buy = proba.ravel()
        p_sell = 1.0 - p_buy

    closes = df["close"].to_numpy()
    n = len(df)

    equity = init_equity
    equity_curve = []
    pos = 0        # 0:ノーポジ, +1:買い, -1:売り
    entry_idx = -1
    entry_price = np.nan

    for i in range(n):
        # エグジット判定
        if pos != 0 and (i - entry_idx) >= L:
            exit_price = closes[i]
            ret = (exit_price/entry_price - 1.0) * (1 if pos>0 else -1)
            equity *= (1.0 + ret)
            pos = 0
            entry_idx = -1
            entry_price = np.nan

        # エントリー判定（ノーポジ時のみ）
        if pos == 0:
            if (p_buy[i] >= best_t) and (p_buy[i] > p_sell[i]):
                pos = +1
                entry_idx = i
                entry_price = closes[i]
            elif (p_sell[i] >= best_t) and (p_sell[i] > p_buy[i]):
                pos = -1
                entry_idx = i
                entry_price = closes[i]

        equity_curve.append(equity)

    out = pd.DataFrame({
        "time": df_raw.loc[df.index, "time"] if "time" in df_raw.columns else np.arange(n),
        "close": closes,
        "p_buy": p_buy,
        "p_sell": p_sell,
        "equity": equity_curve
    })
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    out.to_csv(out_csv, index=False, encoding="utf-8")
    print(f"[bt] wrote equity: {out_csv}  (final={equity_curve[-1]:.2f}, ret={(equity_curve[-1]/init_equity-1)*100:.2f}%)")

    if show:
        plt.figure(figsize=(9,4))
        plt.plot(out["equity"])
        plt.title(f"Equity Curve (start={init_equity:.0f} JPY, L={L}, thr={best_t})")
        plt.xlabel("bars")
        plt.ylabel("equity (JPY)")
        plt.tight_layout()
        plt.show()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="バックテスト対象CSV。未指定なら data/*.csv の最新を使用")
    ap.add_argument("--out", default="logs/backtest/equity_curve.csv")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--no-show", action="store_true")
    args = ap.parse_args()

    csv = args.csv or sorted(glob.glob("data/*.csv"))[-1]
    run_backtest(csv, args.out, init_equity=args.capital, show=not args.no_show)
