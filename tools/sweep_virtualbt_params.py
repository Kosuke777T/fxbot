# tools/sweep_virtualbt_params.py
"""
VirtualBT パラメータスイープツール

threshold と filter_level の組み合わせをスイープし、
各条件の頻度KPIと成績をCSVに出力する。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

# プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.backtest.backtest_engine import BacktestEngine
from app.strategies.ai_strategy import (
    build_features,
    get_active_model_meta,
    validate_feature_order_fail_fast,
)


def parse_thresholds(thresholds_str: str) -> List[float]:
    """
    threshold 文字列をパース

    Parameters
    ----------
    thresholds_str : str
        "0.50:0.80:0.05" または "0.5,0.6,0.7" 形式

    Returns
    -------
    list[float]
        threshold のリスト
    """
    if ":" in thresholds_str:
        # start:stop:step 形式
        parts = thresholds_str.split(":")
        if len(parts) == 3:
            start = float(parts[0])
            stop = float(parts[1])
            step = float(parts[2])
            thresholds = []
            th = start
            while th <= stop:
                thresholds.append(round(th, 3))
                th += step
            return thresholds
    # カンマ区切り
    return [float(x.strip()) for x in thresholds_str.split(",")]


def parse_filter_levels(filter_levels_str: str) -> List[int]:
    """
    filter_level 文字列をパース

    Parameters
    ----------
    filter_levels_str : str
        "0,1,2,3" 形式

    Returns
    -------
    list[int]
        filter_level のリスト
    """
    return [int(x.strip()) for x in filter_levels_str.split(",")]


def run_single_backtest(
    data_csv: Path,
    start: str | None,
    end: str | None,
    threshold: float,
    filter_level: int,
    out_dir: Path,
    profile: str = "michibiki_std",
    symbol: str = "USDJPY-",
    init_position: str = "flat",
    capital: float = 100000.0,
) -> Dict[str, Any]:
    """
    単一のパラメータ組み合わせでバックテストを実行

    Parameters
    ----------
    data_csv : Path
        OHLCV CSV のパス
    start : str | None
        開始日付
    end : str | None
        終了日付
    threshold : float
        threshold 値
    filter_level : int
        filter_level 値
    out_dir : Path
        出力ディレクトリ
    profile : str
        プロファイル名
    symbol : str
        シンボル名
    init_position : str
        初期ポジション
    capital : float
        初期資本

    Returns
    -------
    dict
        結果（N_intent, N_actual, execution_rate, metrics 等）
    """
    # データ読み込み
    df = pd.read_csv(data_csv, parse_dates=["time"])

    # 期間スライス
    if start:
        try:
            ts = pd.Timestamp(start)
            trade_start_ts = ts
            warm_start = (ts - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            warm_start = start
            trade_start_ts = None
    else:
        warm_start = None
        trade_start_ts = None

    if warm_start:
        df = df[df["time"] >= pd.Timestamp(warm_start)]
    if end:
        df = df[df["time"] <= pd.Timestamp(end)]
    df = df.reset_index(drop=True)

    if df.empty:
        raise RuntimeError("No data in the requested period.")

    # BacktestEngine を初期化（threshold は active_model.json から取得されるが、
    # ここでは一時的に上書きするため、BacktestEngine の __init__ 後に best_threshold を上書き）
    engine = BacktestEngine(
        profile=profile,
        initial_capital=capital,
        filter_level=filter_level,
        init_position=init_position,
        trade_start_ts=trade_start_ts,
    )

    # threshold を一時的に上書き
    original_threshold = engine.best_threshold
    engine.best_threshold = threshold

    try:
        # バックテスト実行（BacktestEngine.run() は元のOHLCVデータを受け取る）
        results = engine.run(df, out_dir, symbol=symbol)

        # 結果を集計
        decisions_path = results.get("decisions")
        trades_path = results.get("trades")
        equity_path = results.get("equity_curve")
        metrics_path = out_dir / "metrics.json"

        # N_intent（decisions行数）
        n_intent = 0
        if decisions_path and Path(decisions_path).exists():
            with open(decisions_path, "r", encoding="utf-8") as f:
                n_intent = sum(1 for line in f if line.strip())

        # N_actual（trades行数、ヘッダー除く）
        n_actual = 0
        if trades_path and Path(trades_path).exists():
            trades_df = pd.read_csv(trades_path)
            n_actual = len(trades_df)

        # execution_rate
        execution_rate = n_actual / n_intent if n_intent > 0 else 0.0

        # metrics.json を生成（backtest_run.py と同じロジック）
        metrics = {}
        if equity_path and Path(equity_path).exists():
            try:
                from tools.backtest_run import metrics_from_equity, trade_metrics
                eq_df = pd.read_csv(equity_path)
                base = metrics_from_equity(
                    pd.Series(eq_df["equity"].values, index=pd.to_datetime(eq_df["time"]))
                )

                if trades_path and Path(trades_path).exists():
                    trades_df = pd.read_csv(trades_path)
                    if not trades_df.empty:
                        tmet = trade_metrics(trades_df)
                        base.update(tmet)

                # metrics.json を保存
                with open(metrics_path, "w", encoding="utf-8") as f:
                    json.dump(base, f, ensure_ascii=False, indent=2)

                metrics = base
            except Exception as e:
                print(f"[sweep] WARN: metrics.json生成失敗: {e}", flush=True)

        # metrics.json から読み込み（既に存在する場合）
        if not metrics and metrics_path.exists():
            try:
                with open(metrics_path, "r", encoding="utf-8") as f:
                    metrics = json.load(f)
            except Exception:
                pass

        # 主要指標を抽出（存在するキーのみ）
        result = {
            "threshold": threshold,
            "filter_level": filter_level,
            "n_intent": n_intent,
            "n_actual": n_actual,
            "execution_rate": execution_rate,
        }

        # metrics から主要指標を追加（存在するキーのみ）
        metric_keys = [
            "total_return",
            "max_drawdown",
            "trades",
            "win_rate",
            "avg_pnl",
            "profit_factor",
            "sharpe_like",
            "start_equity",
            "end_equity",
            "max_consec_win",
            "max_consec_loss",
            "avg_holding_bars",
            "avg_holding_days",
        ]
        for key in metric_keys:
            if key in metrics:
                # NaN/Inf を None に変換（CSV出力時に問題になるため）
                val = metrics[key]
                if isinstance(val, float):
                    import math
                    if math.isnan(val) or math.isinf(val):
                        result[key] = None
                    else:
                        result[key] = val
                else:
                    result[key] = val

        # total_pnl / profit_jpy の計算（end_equity - start_equity）
        if "start_equity" in result and "end_equity" in result:
            if result["start_equity"] is not None and result["end_equity"] is not None:
                result["total_pnl"] = result["end_equity"] - result["start_equity"]
                result["profit_jpy"] = result["total_pnl"]  # 別名
            else:
                result["total_pnl"] = None
                result["profit_jpy"] = None

        return result

    finally:
        # threshold を復元
        engine.best_threshold = original_threshold


def sweep_parameters(
    data_csv: Path,
    start: str | None,
    end: str | None,
    thresholds: List[float],
    filter_levels: List[int],
    base_out_dir: Path,
    profile: str = "michibiki_std",
    symbol: str = "USDJPY-",
    init_position: str = "flat",
    capital: float = 100000.0,
) -> pd.DataFrame:
    """
    パラメータをスイープして結果を集計

    Parameters
    ----------
    data_csv : Path
        OHLCV CSV のパス
    start : str | None
        開始日付
    end : str | None
        終了日付
    thresholds : list[float]
        threshold のリスト
    filter_levels : list[int]
        filter_level のリスト
    base_out_dir : Path
        ベース出力ディレクトリ
    profile : str
        プロファイル名
    symbol : str
        シンボル名
    init_position : str
        初期ポジション
    capital : float
        初期資本

    Returns
    -------
    pd.DataFrame
        スイープ結果（各組み合わせ1行）
    """
    rows = []
    total_combinations = len(thresholds) * len(filter_levels)
    current = 0

    for threshold in thresholds:
        for filter_level in filter_levels:
            current += 1
            print(
                f"[sweep] {current}/{total_combinations}: threshold={threshold:.3f}, filter_level={filter_level}",
                flush=True,
            )

            # 試行ごとの出力ディレクトリ
            trial_dir = base_out_dir / f"th{threshold:.3f}_fl{filter_level}"
            trial_dir.mkdir(parents=True, exist_ok=True)

            try:
                result = run_single_backtest(
                    data_csv=data_csv,
                    start=start,
                    end=end,
                    threshold=threshold,
                    filter_level=filter_level,
                    out_dir=trial_dir,
                    profile=profile,
                    symbol=symbol,
                    init_position=init_position,
                    capital=capital,
                )
                rows.append(result)
                print(f"[sweep] 完了: execution_rate={result['execution_rate']:.4f}, n_actual={result['n_actual']}", flush=True)
            except Exception as e:
                print(f"[sweep] エラー: {e}", flush=True)
                # エラー時も行を追加（NaN値で）
                rows.append(
                    {
                        "threshold": threshold,
                        "filter_level": filter_level,
                        "n_intent": 0,
                        "n_actual": 0,
                        "execution_rate": 0.0,
                        "error": str(e)[:200],
                    }
                )

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="VirtualBT パラメータスイープツール")
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="バックテスト対象のCSVファイルパス",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="開始日付 YYYY-MM-DD",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="終了日付 YYYY-MM-DD",
    )
    parser.add_argument(
        "--thresholds",
        type=str,
        required=True,
        help="threshold スイープ範囲（例: 0.50:0.80:0.05 または 0.5,0.6,0.7）",
    )
    parser.add_argument(
        "--filter-levels",
        type=str,
        required=True,
        help="filter_level スイープ（例: 0,1,2,3）",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="michibiki_std",
        help="プロファイル名（デフォルト: michibiki_std）",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="USDJPY-",
        help="シンボル名（デフォルト: USDJPY-）",
    )
    parser.add_argument(
        "--init-position",
        type=str,
        choices=["flat", "carry"],
        default="flat",
        help="初期ポジション（デフォルト: flat）",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100000.0,
        help="初期資本（デフォルト: 100000.0）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV出力パス（デフォルト: sweep_results.csv）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="試行ごとの出力ディレクトリ（デフォルト: logs/backtest/sweep_<timestamp>）",
    )

    args = parser.parse_args()

    # パラメータパース
    thresholds = parse_thresholds(args.thresholds)
    filter_levels = parse_filter_levels(args.filter_levels)

    print("=" * 80)
    print("VirtualBT パラメータスイープ")
    print("=" * 80)
    print(f"thresholds: {thresholds}")
    print(f"filter_levels: {filter_levels}")
    print(f"総組み合わせ数: {len(thresholds) * len(filter_levels)}")
    print()

    # 出力ディレクトリ
    if args.output_dir:
        base_out_dir = Path(args.output_dir)
    else:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_out_dir = PROJECT_ROOT / "logs" / "backtest" / f"sweep_{timestamp}"
    base_out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] 出力ディレクトリ: {base_out_dir}", flush=True)

    # スイープ実行
    df_results = sweep_parameters(
        data_csv=args.csv,
        start=args.start_date,
        end=args.end_date,
        thresholds=thresholds,
        filter_levels=filter_levels,
        base_out_dir=base_out_dir,
        profile=args.profile,
        symbol=args.symbol,
        init_position=args.init_position,
        capital=args.capital,
    )

    # CSV出力
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = base_out_dir / "sweep_results.csv"

    df_results.to_csv(output_path, index=False)
    print()
    print("=" * 80)
    print("スイープ完了")
    print("=" * 80)
    print(f"結果CSV: {output_path}")
    print(f"試行数: {len(df_results)}")
    print()
    print("結果サマリ:")
    print(df_results.to_string(index=False))
    print()


if __name__ == "__main__":
    main()
