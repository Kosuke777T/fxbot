"""
app/services/wfo_stability_service.py

WFO（Walk-Forward Optimization）の安定性評価サービス。

metrics_wfo.json の train/test 集約サマリーを使用して、
最終モデルが実運用に耐えるかを判定する。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from core.utils.timeutil import now_jst_iso


def _sha256_file(p: Path) -> Optional[str]:
    """ファイルのSHA256ハッシュを計算する。"""
    try:
        if not p.exists() or not p.is_file():
            return None
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _stability_path(run_id: str) -> Path:
    """安定性評価結果の保存パスを返す。"""
    return Path("logs") / "retrain" / f"stability_{run_id}.json"


def load_saved_stability(run_id: str) -> Optional[dict[str, Any]]:
    """
    保存済みの安定性評価結果を読み込む。

    Parameters
    ----------
    run_id : str
        実行ID

    Returns
    -------
    dict[str, Any] | None
        保存済みの結果（存在しない場合はNone）
    """
    p = _stability_path(run_id)
    try:
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_stability_result(result: dict[str, Any]) -> Optional[Path]:
    """
    安定性評価結果を保存する。

    Parameters
    ----------
    result : dict[str, Any]
        評価結果（run_id, stable, score, reasons等を含む）

    Returns
    -------
    Path | None
        保存先パス（失敗時はNone、例外は握り潰す）
    """
    try:
        run_id = str(result.get("run_id") or "").strip()
        if not run_id:
            return None

        p = _stability_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)

        # schema整形（最低限の保証）
        out = dict(result)
        out.setdefault("schema_version", "1")
        out.setdefault("created_at", now_jst_iso())

        # 安全に上書き（テンポラリ→置換）
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return p
    except Exception:
        return None


def evaluate_wfo_stability(
    metrics: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    run_id: str | None = None,
    metrics_path: str | Path | None = None,
    report_path: str | Path | None = None,
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

    run_id : str | None, optional
        実行ID（保存用、未指定時は自動生成）
    metrics_path : str | Path | None, optional
        metrics_wfo.json のパス（保存用）
    report_path : str | Path | None, optional
        report_*.json のパス（保存用、run_id抽出にも使用）

    Returns
    -------
    dict[str, Any]
        評価結果（判定実行時に logs/retrain/stability_{run_id}.json に保存される）:
        {
            "run_id": str,
            "stable": bool,
            "score": float,
            "reasons": [str],
            "summary": {
                "test_return": float,
                "test_dd": float,
                "test_pf": float,
                "trades": int
            },
            "sources": {
                "report_path": str,
                "metrics_path": str
            },
            "inputs_digest": {
                "report_sha256": str | None,
                "metrics_sha256": str | None
            },
            "schema_version": str,
            "created_at": str
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

    # run_idを生成（早期リターン時も必要）
    resolved_run_id = run_id
    if not resolved_run_id:
        if report_path:
            # report_*.json から run_id を抽出
            report_path_obj = Path(report_path)
            if report_path_obj.exists():
                try:
                    report_data = json.loads(report_path_obj.read_text(encoding="utf-8"))
                    resolved_run_id = str(report_data.get("run_id", ""))
                except Exception:
                    pass
        if not resolved_run_id and metrics_path:
            # metrics_wfo.json のパスからタイムスタンプベースのIDを生成
            metrics_path_obj = Path(metrics_path)
            if metrics_path_obj.exists():
                mtime = metrics_path_obj.stat().st_mtime
                resolved_run_id = f"wfo_{int(mtime)}"
        if not resolved_run_id:
            # フォールバック: 現在時刻ベース
            from datetime import datetime
            resolved_run_id = f"wfo_{int(datetime.now().timestamp())}"

    if missing_keys:
        reasons.append(f"Missing required keys: {', '.join(missing_keys)}")
        result = {
            "run_id": resolved_run_id,
            "stable": False,
            "score": 0.0,
            "reasons": reasons,
            "summary": {
                "test_return": test_metrics.get("total_return", 0.0),
                "test_dd": test_metrics.get("max_drawdown", 0.0),
                "test_pf": test_metrics.get("profit_factor", 0.0),
                "trades": test_metrics.get("trades", 0),
            },
            "sources": {
                "report_path": str(report_path) if report_path else "",
                "metrics_path": str(metrics_path) if metrics_path else "",
            },
            "inputs_digest": {
                "report_sha256": _sha256_file(Path(report_path)) if report_path else None,
                "metrics_sha256": _sha256_file(Path(metrics_path)) if metrics_path else None,
            },
        }
        save_stability_result(result)
        return result

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

    result = {
        "run_id": resolved_run_id,
        "stable": stable,
        "score": round(score, 2),
        "reasons": reasons,
        "summary": summary,
        "sources": {
            "report_path": str(report_path) if report_path else "",
            "metrics_path": str(metrics_path) if metrics_path else "",
        },
        "inputs_digest": {
            "report_sha256": _sha256_file(Path(report_path)) if report_path else None,
            "metrics_sha256": _sha256_file(Path(metrics_path)) if metrics_path else None,
        },
    }

    # 保存は失敗してもOK（判定を止めない）
    save_stability_result(result)

    return result

