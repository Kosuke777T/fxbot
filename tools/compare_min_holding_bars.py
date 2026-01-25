# tools/compare_min_holding_bars.py
"""
min_holding_bars を 0/1/2/3 で比較するツール

同一データ・同一期間・同一モデルで min_holding_bars を切り替えてバックテストを実行し、
各回の指標を summary.csv にまとめる。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.backtest_run import run_backtest


def compare_min_holding_bars(
    data_csv: Path,
    start: str | None,
    end: str | None,
    capital: float,
    out_root: Path,
    profile: str = "michibiki_std",
    symbol: str = "USDJPY-",
    init_position: str = "flat",
    min_holding_bars_list: list[int] | None = None,
) -> pd.DataFrame:
    """
    min_holding_bars を複数値で比較して結果を集計

    Parameters
    ----------
    data_csv : Path
        OHLCVデータのCSVファイル
    start : str | None
        開始日（YYYY-MM-DD形式）
    end : str | None
        終了日（YYYY-MM-DD形式）
    capital : float
        初期資本
    out_root : Path
        出力ルートディレクトリ（各試行のサブディレクトリと summary.csv を配置）
    profile : str
        プロファイル名
    symbol : str
        シンボル名
    init_position : str
        初期ポジション
    min_holding_bars_list : list[int], optional
        比較する min_holding_bars のリスト（デフォルト: [0, 1, 2, 3]）

    Returns
    -------
    pd.DataFrame
        比較結果（min_holding_bars, trades, profit_factor, max_drawdown, avg_holding_bars）
    """
    if min_holding_bars_list is None:
        min_holding_bars_list = [0, 1, 2, 3]

    out_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for min_hold in min_holding_bars_list:
        print(f"\n{'=' * 80}", flush=True)
        print(f"[compare] min_holding_bars={min_hold}", flush=True)
        print(f"{'=' * 80}", flush=True)

        # 各試行の出力ディレクトリ（min_holding_bars を含める）
        trial_dir = out_root / f"min_hold_{min_hold}"
        trial_dir.mkdir(parents=True, exist_ok=True)

        # ExitPolicy を構築（min_holding_bars のみ指定、他はデフォルト）
        exit_policy = None
        if min_hold > 0:
            exit_policy = {
                "min_holding_bars": min_hold,
                "tp_sl_eval_from_next_bar": False,
                "exit_on_reverse_signal_only": False,
            }

        try:
            # バックテスト実行（同一プロセス呼び出し）
            run_backtest(
                data_csv=data_csv,
                start=start,
                end=end,
                capital=capital,
                out_dir=trial_dir,
                profile=profile,
                symbol=symbol,
                init_position=init_position,
                exit_policy=exit_policy,
            )

            # metrics.json から指標を読み込み
            metrics_path = trial_dir / "metrics.json"
            if metrics_path.exists():
                with open(metrics_path, "r", encoding="utf-8") as f:
                    metrics = json.load(f)

                row = {
                    "min_holding_bars": min_hold,
                    "trades": metrics.get("trades", 0),
                    "profit_factor": metrics.get("profit_factor", 0.0),
                    "max_drawdown": metrics.get("max_drawdown", 0.0),
                    "avg_holding_bars": metrics.get("avg_holding_bars", 0.0),
                }
                rows.append(row)
                print(
                    f"[compare] 完了: trades={row['trades']}, "
                    f"pf={row['profit_factor']:.4f}, "
                    f"dd={row['max_drawdown']:.4f}, "
                    f"avg_hold={row['avg_holding_bars']:.2f}",
                    flush=True,
                )
            else:
                print(f"[compare] WARN: metrics.json が見つかりません: {metrics_path}", flush=True)
                rows.append(
                    {
                        "min_holding_bars": min_hold,
                        "trades": 0,
                        "profit_factor": 0.0,
                        "max_drawdown": 0.0,
                        "avg_holding_bars": 0.0,
                    }
                )

        except Exception as e:
            print(f"[compare] エラー: {e}", flush=True)
            rows.append(
                {
                    "min_holding_bars": min_hold,
                    "trades": 0,
                    "profit_factor": 0.0,
                    "max_drawdown": 0.0,
                    "avg_holding_bars": 0.0,
                }
            )

    # DataFrame に変換
    df = pd.DataFrame(rows)

    # summary.csv を出力
    summary_path = out_root / "summary.csv"
    df.to_csv(summary_path, index=False)
    print(f"\n[compare] 結果を出力しました: {summary_path}", flush=True)

    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="min_holding_bars 比較ツール")
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="バックテスト対象のCSVファイルパス（固定）",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="開始日付 YYYY-MM-DD（固定）",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="終了日付 YYYY-MM-DD（固定）",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100000.0,
        help="初期資本（デフォルト: 100000.0）",
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
        "--out-root",
        type=Path,
        required=True,
        help="出力ルートディレクトリ（summary.csv と各試行のサブディレクトリを配置）",
    )
    parser.add_argument(
        "--min-holding-bars",
        type=str,
        help="比較する min_holding_bars のリスト（カンマ区切り、例: 0,1,2,3）",
    )

    args = parser.parse_args()

    # min_holding_bars リストをパース
    if args.min_holding_bars:
        min_holding_bars_list = [int(x.strip()) for x in args.min_holding_bars.split(",")]
    else:
        min_holding_bars_list = [0, 1, 2, 3]

    print("=" * 80)
    print("min_holding_bars 比較ツール")
    print("=" * 80)
    print(f"CSV: {args.csv}")
    print(f"期間: {args.start_date} .. {args.end_date}")
    print(f"比較値: {min_holding_bars_list}")
    print(f"出力先: {args.out_root}")
    print()

    # 比較実行
    df_results = compare_min_holding_bars(
        data_csv=args.csv,
        start=args.start_date,
        end=args.end_date,
        capital=args.capital,
        out_root=args.out_root,
        profile=args.profile,
        symbol=args.symbol,
        init_position=args.init_position,
        min_holding_bars_list=min_holding_bars_list,
    )

    print()
    print("=" * 80)
    print("比較完了")
    print("=" * 80)
    print("\n結果サマリ:")
    print(df_results.to_string(index=False))
    print()


if __name__ == "__main__":
    main()
