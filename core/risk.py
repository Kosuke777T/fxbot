# core/risk.py
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Optional


@dataclass
class LotSizingResult:
    """
    target_monthly_return / max_monthly_dd と ATR ストップから
    「推奨ロット」と「月間想定ボラ」を計算した結果をまとめたデータクラス。
    """

    # 既存フィールド（そのまま）
    lot: float  # 実際に使うロット（min/max でクランプ済み）
    per_trade_risk_pct: float  # 1トレードあたりのリスク（％）
    est_monthly_volatility_pct: float  # 月間想定ボラ（ざっくり標準偏差イメージ％）
    est_max_monthly_dd_pct: float  # 想定最大DD（％、max_monthly_dd に近い値になるよう設計）

    # 追加フィールド（オプション扱い）
    equity: float | None = None      # ロット計算に使った口座残高
    atr: float | None = None         # ロット計算に使った ATR 値

    # リスク関連（％と金額）
    risk_pct: float | None = None    # トレード1回あたりのリスク率
    risk_jpy: float | None = None    # 想定リスク額（円）
    risk_amount: float | None = None # 将来通貨単位変えたくなったときの余地。現状 risk_jpy と同じ。

    # 将来用：エントリー価格と損切り価格から計算して入れる「穴」
    sl_price: float | None = None


# ----------------------------------------------------------------------
# Backtest-based lot adjustment utility
# ----------------------------------------------------------------------

def compute_lot_scaler_from_backtest(
    monthly_returns_csv: str,
    target_monthly_return: float,
    max_monthly_dd: float
) -> float:
    """
    バックテストの monthly_returns.csv を読み込み、
    実際の月次リターンのボラ（標準偏差）と最大DDから
    ロット倍率の補正値（0.1〜3.0 の範囲）を返す。

    Parameters
    ----------
    monthly_returns_csv : str
        backtests/{profile}/monthly_returns.csv のパス
    target_monthly_return : float
        例: 0.03 (3%)
    max_monthly_dd : float
        許容最大DD (例: 0.20)

    Returns
    -------
    float
        ロットに掛ける補正倍率。大きすぎる/危険であれば小さくなる。
        （例）0.7, 1.0, 1.3 など
    """
    import pandas as pd
    import numpy as np

    try:
        df = pd.read_csv(monthly_returns_csv)
    except Exception:
        # 読めなければ補正なし
        return 1.0

    # 想定するカラム:
    # yyyy-mm, return_pct, dd_pct
    if "return_pct" not in df.columns or "dd_pct" not in df.columns:
        return 1.0

    ret = df["return_pct"].astype(float)
    dd = df["dd_pct"].astype(float)

    # 実際の最大DD（dd_pct は負値想定なので符号反転）
    real_dd = float(dd.min() * -1.0) if len(dd) > 0 else 0.0

    # 月次ボラ（標準偏差）
    vol = float(ret.std()) if len(ret) > 1 else 0.0

    # 平均リターン
    mean_ret = float(ret.mean()) if len(ret) > 0 else 0.0

    # --- 1. リターン側の係数 k_ret ---------------------------------
    # 目標リターンに対する達成度。
    # 成績がマイナス or 目標が0以下なら「かなり弱い」とみなして 0.1 に固定。
    if target_monthly_return > 0 and mean_ret > 0:
        k_ret = mean_ret / target_monthly_return
        if not np.isfinite(k_ret) or k_ret <= 0:
            k_ret = 0.1
    else:
        k_ret = 0.1

    # --- 2. DD側の係数 k_dd -----------------------------------------
    # DDが20%許容に対して、実DDが10% → 余裕あり → 2.0倍まで許容
    if real_dd > 0:
        k_dd = max_monthly_dd / real_dd
        if not np.isfinite(k_dd) or k_dd <= 0:
            k_dd = 1.0
    else:
        k_dd = 1.0

    # --- 3. ボラ側の係数 k_vol ---------------------------------------
    # ボラが高すぎればロットを抑制。
    # 「月次ボラ15％を1.0」とする。
    if vol > 0:
        k_vol = 0.15 / vol
        if not np.isfinite(k_vol) or k_vol <= 0:
            k_vol = 1.0
    else:
        k_vol = 1.0

    # --- 4. 3つを合わせる（幾何平均） --------------------------------
    prod = k_ret * k_dd * k_vol

    # 計算が変なとき（負, 0, nan, inf）は安全側に 0.1 とみなす
    import math
    if not np.isfinite(prod) or prod <= 0:
        ks = 0.1
    else:
        ks = math.sqrt(prod)

    # 係数は 0.1〜3.0 の範囲に収める
    ks = float(np.clip(ks, 0.1, 3.0))
    return ks


def compute_lot_size_from_atr(
    *,
    equity: float,
    atr: float,
    atr_mult_sl: float,
    target_monthly_return: float,
    max_monthly_dd: float,
    tick_value: float,
    tick_size: float,
    expected_trades_per_month: int = 40,
    worst_case_trades_for_dd: int = 10,
    avg_r_multiple: float = 0.6,
    min_lot: float = 0.01,
    max_lot: float = 1.0,
) -> LotSizingResult:
    """
    target_monthly_return / max_monthly_dd と ATR ストップから
    自動ロットを計算するユーティリティ。

    Parameters
    ----------
    equity:
        現在の口座残高 or 有効証拠金（口座通貨）。
    atr:
        ATR 値（価格単位）。例: USDJPY なら 0.25 など。
    atr_mult_sl:
        ストップ幅の係数。SL 距離 = atr_mult_sl * atr。
    target_monthly_return:
        目標月次リターン (例: 0.03)。
    max_monthly_dd:
        許容最大月次 DD (例: -0.20)。符号付きでもよいが絶対値を使用する。
    tick_value:
        1ティック動いたときの損益（1 ロットあたり、口座通貨）。
        MT5 の symbol_info(...).trade_tick_value を想定。
    tick_size:
        1ティックの価格幅。symbol_info(...).trade_tick_size を想定。
    expected_trades_per_month:
        月あたり想定トレード回数。
    worst_case_trades_for_dd:
        「この回数連続で負けたら max_dd に達する」とみなす回数。
    avg_r_multiple:
        1トレードあたりの平均 R（リスクリワード）。0.6 などの経験値。
    min_lot, max_lot:
        ロットの下限・上限（ブローカー仕様に合わせて調整）。

    Returns
    -------
    LotSizingResult
    """

    if equity <= 0:
        raise ValueError("equity は正の値である必要があります。")
    if atr <= 0:
        raise ValueError("atr は正の値である必要があります。")
    if atr_mult_sl <= 0:
        raise ValueError("atr_mult_sl は正の値である必要があります。")
    if tick_value <= 0 or tick_size <= 0:
        raise ValueError("tick_value / tick_size は正の値である必要があります。")
    if expected_trades_per_month <= 0:
        raise ValueError("expected_trades_per_month は正の整数である必要があります。")
    if worst_case_trades_for_dd <= 0:
        raise ValueError("worst_case_trades_for_dd は正の整数である必要があります。")
    if avg_r_multiple <= 0:
        raise ValueError("avg_r_multiple は正の値である必要があります。")

    # DD は絶対値を使う（仕様書では -0.20 などになっている想定）
    max_dd_abs = abs(max_monthly_dd)

    # --- 1. DD 制約から見た 1トレードあたり許容リスク ---
    risk_from_dd = max_dd_abs / float(worst_case_trades_for_dd)

    # --- 2. 目標リターンから見た 1トレードあたり必要リスク ---
    expected_return_per_trade = target_monthly_return / float(expected_trades_per_month)
    risk_from_return = expected_return_per_trade / avg_r_multiple

    # --- 3. 実際に使う 1トレードのリスク％ ---
    # 安全側に振るため、DD 制約と 3% 目標のうち「小さい方」を採用する。
    per_trade_risk_pct = min(risk_from_dd, risk_from_return)

    # 念のため、極端な値をクランプ（0.01%〜10% の範囲に収める）
    per_trade_risk_pct = max(0.0001, min(per_trade_risk_pct, 0.10))

    # --- 4. ATR ストップから 1ロットあたりの損失額を計算 ---
    # 1ポイントあたり損益（1 ロット）
    value_per_point_per_lot = tick_value / tick_size

    # ストップまでのポイント数
    sl_points = (atr_mult_sl * atr) / tick_size

    # 1ロットあたりの損失額（口座通貨）
    risk_per_lot = sl_points * value_per_point_per_lot

    if risk_per_lot <= 0:
        raise ValueError("risk_per_lot が 0 以下です。tick_value / tick_size / atr の指定を確認してください。")

    # --- 5. 口座残高からロットを逆算 ---
    risk_per_trade_money = equity * per_trade_risk_pct
    raw_lot = risk_per_trade_money / risk_per_lot

    # ロットをクランプ（0.01〜max_lot）
    lot = max(min_lot, min(raw_lot, max_lot))

    # --- 6. 月間想定ボラと DD のざっくり推定 ---
    # ガウシアンっぽい近似で、「標準偏差 ~ sqrt(N) * per_trade_risk_pct」と置く。
    est_monthly_volatility_pct = per_trade_risk_pct * sqrt(float(expected_trades_per_month))

    # 「worst_case_trades_for_dd 回負けたらこの DD」とみなす。
    est_max_monthly_dd_pct = per_trade_risk_pct * float(worst_case_trades_for_dd)

    # --- 追加：リスク関連を LotSizingResult に埋め込む ----------------
    risk_pct = float(per_trade_risk_pct)
    risk_jpy = float(equity) * risk_pct if equity is not None else None

    return LotSizingResult(
        lot=lot,
        per_trade_risk_pct=per_trade_risk_pct,
        est_monthly_volatility_pct=est_monthly_volatility_pct,
        est_max_monthly_dd_pct=est_max_monthly_dd_pct,

        # ここから追加フィールド
        equity=float(equity),
        atr=float(atr),
        risk_pct=risk_pct,
        risk_jpy=risk_jpy,
        risk_amount=risk_jpy,
        sl_price=None,  # ここでは SL 価格が分からないので、将来用の穴だけ開けておく
    )
