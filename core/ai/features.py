import numpy as np
import pandas as pd


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / (roll_down + 1e-12)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up_arr = np.asarray(high.diff(), dtype=float)
    down_arr = np.asarray(-low.diff(), dtype=float)
    plus_dm = np.where((up_arr > down_arr) & (up_arr > 0), up_arr, 0.0)
    minus_dm = np.where((down_arr > up_arr) & (down_arr > 0), down_arr, 0.0)

    tr = _true_range(high, low, close).astype(float)
    tr_smooth = tr.rolling(period).sum()
    plus_series = pd.Series(plus_dm, index=df.index)
    minus_series = pd.Series(minus_dm, index=df.index)
    plus_di = 100 * plus_series.rolling(period).sum() / (tr_smooth + 1e-12)
    minus_di = 100 * minus_series.rolling(period).sum() / (tr_smooth + 1e-12)

    dx = ((plus_di - minus_di).abs() / ((plus_di + minus_di) + 1e-12)) * 100
    adx = dx.rolling(period).mean()
    return adx


def _bb_percent_b(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.Series:
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    upper = ma + k * sd
    lower = ma - k * sd
    bbp = (close - lower) / ((upper - lower) + 1e-12)
    return bbp.clip(0, 1)


def _wick_body_ratios(df: pd.DataFrame) -> pd.DataFrame:
    open_, high, low, close = df["open"], df["high"], df["low"], df["close"]
    body = (close - open_).abs()
    upper_wick = (high - np.maximum(open_, close)).clip(lower=0)
    lower_wick = (np.minimum(open_, close) - low).clip(lower=0)
    total = (high - low).replace(0, np.nan)
    return pd.DataFrame(
        {
            "upper_wick_ratio": (upper_wick / total).fillna(0),
            "lower_wick_ratio": (lower_wick / total).fillna(0),
            "body_ratio": (body / total).fillna(0),
        }
    )


def _zscore(series: pd.Series, win: int = 20) -> pd.Series:
    mean = series.rolling(win).mean()
    sd = series.rolling(win).std(ddof=0)
    return (series - mean) / (sd + 1e-12)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    入力: df には ["open","high","low","close","volume"] が必須
    出力: 元のOHLCV + 追加特徴列（NaNはdropnaで最終的に落としてください）
    """
    out = df.copy()

    for p in (1, 3, 5, 10):
        out[f"ret_{p}"] = df["close"].pct_change(p)

    out["ret_std_10"] = out["ret_1"].rolling(10).std(ddof=0)
    out["ret_std_20"] = out["ret_1"].rolling(20).std(ddof=0)

    out["tr"] = _true_range(df["high"], df["low"], df["close"])
    out["atr_14"] = out["tr"].rolling(14).mean()

    out["rsi_14"] = _rsi(df["close"], 14)
    out["adx_14"] = _adx(df, 14)
    out["bbp_20"] = _bb_percent_b(df["close"], 20, 2.0)

    wick = _wick_body_ratios(df)
    out = pd.concat([out, wick], axis=1)

    out["vol_zscore_20"] = _zscore(df["volume"], 20)

    return out

# --- ここから追記（任意） ---
def build_Xy(df_raw: pd.DataFrame, label_col: str = "label") -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """
    build_features() で作った特徴群から、学習用の X(DF) と y(Series) を返す。
    - label_col はあなたの既存ラベル列名に合わせて変更
    """
    feat_df = build_features(df_raw)

    # 学習に使う列をここで明示化（あなたの実際の特徴列に合わせて調整）
    feature_cols = [
        "open","high","low","close","volume",
        "ret_1","ret_3","ret_5","ret_10",
        "ret_std_10","ret_std_20",
        "tr","atr_14",
        "rsi_14","adx_14","bbp_20",
        "upper_wick_ratio","lower_wick_ratio","body_ratio",
        "vol_zscore_20",
    ]

    # ラベルがまだ無い場合は外で作ってから渡す想定
    if label_col not in feat_df.columns:
        raise ValueError(f"label col '{label_col}' not in DataFrame. 先にラベル作成を行ってください。")

    # 欠損落とし＆インデックス同期
    X_df = feat_df[feature_cols].copy()
    y_ser = feat_df[label_col].copy()
    mask = ~X_df.isna().any(axis=1)
    X_df = X_df.loc[mask]
    y_ser = y_ser.loc[X_df.index]

    return X_df, y_ser, feature_cols
# --- 追記ここまで ---
