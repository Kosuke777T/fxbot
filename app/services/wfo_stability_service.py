"""
app/services/wfo_stability_service.py

WFO（Walk-Forward Optimization）の安定性評価サービス。

metrics_wfo.json の train/test 集約サマリーを使用して、
最終モデルが実運用に耐えるかを判定する。
"""

from __future__ import annotations

from typing import Any


def evaluate_wfo_stability(
    metrics: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    WFOの安定性を評価する。

    Parameters
    ----------
    metrics : dict[str, Any]
        metrics_wfo.json の内容。以下の構造を想定:
        {
            "train": {
                "trades": int,
                "total_return": float,
                "max_drawdown": float,
                "profit_factor": float,
                ...
            },
            "test": {
                "trades": int,
                "total_return": float,
                "max_drawdown": float,
                "profit_factor": float,
                ...
            }
        }
    config : dict[str, Any] | None, optional
        評価設定。デフォルト値:
        {
            "min_trades": 20,
            "min_return": 0.0,
            "max_dd_limit": 0.20,
            "min_profit_factor": 1.05,
        }

    Returns
    -------
    dict[str, Any]
        評価結果:
        {
            "stable": bool,
            "score": float,
            "reasons": [str],
            "summary": {
                "test_return": float,
                "test_dd": float,
                "test_pf": float,
                "trades": int
            }
        }
    """
    # デフォルト設定
    default_config = {
        "min_trades": 20,
        "min_return": 0.0,
        "max_dd_limit": 0.20,
        "min_profit_factor": 1.05,
    }
    if config is None:
        config = {}
    cfg = {**default_config, **config}

    # metrics から train/test を取得
    train_metrics = metrics.get("train", {})
    test_metrics = metrics.get("test", {})

    # 必須キーの存在確認
    required_keys = ["trades", "total_return", "max_drawdown", "profit_factor"]
    reasons: list[str] = []
    missing_keys: list[str] = []

    for key in required_keys:
        if key not in train_metrics:
            missing_keys.append(f"train.{key}")
        if key not in test_metrics:
            missing_keys.append(f"test.{key}")

    if missing_keys:
        reasons.append(f"Missing required keys: {', '.join(missing_keys)}")
        return {
            "stable": False,
            "score": 0.0,
            "reasons": reasons,
            "summary": {
                "test_return": test_metrics.get("total_return", 0.0),
                "test_dd": test_metrics.get("max_drawdown", 0.0),
                "test_pf": test_metrics.get("profit_factor", 0.0),
                "trades": test_metrics.get("trades", 0),
            },
        }

    # 値を取得（型変換も行う）
    train_trades = int(train_metrics["trades"])
    train_return = float(train_metrics["total_return"])
    train_dd = float(train_metrics["max_drawdown"])
    train_pf = float(train_metrics["profit_factor"]) if train_metrics["profit_factor"] != "Infinity" else float("inf")

    test_trades = int(test_metrics["trades"])
    test_return = float(test_metrics["total_return"])
    test_dd = float(test_metrics["max_drawdown"])
    test_pf = float(test_metrics["profit_factor"]) if test_metrics["profit_factor"] != "Infinity" else float("inf")

    # ゲート条件チェック
    if test_trades < cfg["min_trades"]:
        reasons.append(f"Trades ({test_trades}) < min_trades ({cfg['min_trades']})")

    if test_return < cfg["min_return"]:
        reasons.append(f"Return ({test_return:.4f}) < min_return ({cfg['min_return']})")

    if abs(test_dd) > cfg["max_dd_limit"]:
        reasons.append(f"Max DD ({abs(test_dd):.4f}) > limit ({cfg['max_dd_limit']})")

    if test_pf != float("inf") and test_pf < cfg["min_profit_factor"]:
        reasons.append(f"Profit factor ({test_pf:.4f}) < min ({cfg['min_profit_factor']})")

    # ゲート違反の有無を判定
    gate_failed = bool(reasons)

    # スコア計算（参考値として常に計算）
    base = 100.0

    # DDペナルティ: abs(test.max_drawdown) * 200
    dd_penalty = abs(test_dd) * 200.0

    # Gapペナルティ: max(0, train.total_return - test.total_return) * 150
    gap_penalty = max(0.0, train_return - test_return) * 150.0

    # Tradeペナルティ: max(0, 50 - trades) * 0.5
    trade_penalty = max(0.0, 50.0 - test_trades) * 0.5

    # スコア = clip(100 - penalties, 0, 100)
    total_penalty = dd_penalty + gap_penalty + trade_penalty
    score = max(0.0, min(100.0, base - total_penalty))

    # stable判定: ゲート違反がなく、かつスコアが閾値以上
    stable_threshold = 60.0
    stable = (not gate_failed) and (score >= stable_threshold)

    # スコアが閾値未満の場合の理由追加（ゲート違反がない場合のみ）
    if not gate_failed and score < stable_threshold:
        reasons.append(f"Score ({score:.2f}) < threshold ({stable_threshold})")

    summary = {
        "test_return": test_return,
        "test_dd": test_dd,
        "test_pf": test_pf if test_pf != float("inf") else None,
        "trades": test_trades,
    }

    return {
        "stable": stable,
        "score": round(score, 2),
        "reasons": reasons,
        "summary": summary,
    }

