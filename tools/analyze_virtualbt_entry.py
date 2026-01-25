# tools/analyze_virtualbt_entry.py
"""
VirtualBT の取引頻度分析ツール

decisions.jsonl を解析して、ENTRY候補から実ENTRYまでの減衰を段階別に集計する。
threshold/filter_level のスイープ分析も可能。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def find_latest_decisions_jsonl(root: Path) -> Optional[Path]:
    """
    decisions.jsonl を探索して最新のものを返す

    Parameters
    ----------
    root : Path
        探索起点ディレクトリ

    Returns
    -------
    Path or None
        最新の decisions.jsonl のパス（見つからない場合は None）
    """
    candidates = list(root.rglob("decisions.jsonl"))
    if not candidates:
        return None
    # 最新の mtime でソート
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def load_decisions_jsonl(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    decisions.jsonl を読み込む

    Parameters
    ----------
    path : Path
        decisions.jsonl のパス
    limit : int, optional
        読み込む最大行数（デバッグ用）

    Returns
    -------
    list[dict]
        決定レコードのリスト
    """
    decisions = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                decisions.append(rec)
            except json.JSONDecodeError:
                continue
    return decisions


def extract_decision_fields(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    決定レコードから集計に必要なフィールドを抽出

    Parameters
    ----------
    rec : dict
        決定レコード

    Returns
    -------
    dict
        抽出されたフィールド
    """
    # 基本フィールド
    prob_buy = rec.get("prob_buy")
    prob_sell = rec.get("prob_sell")
    filter_pass = rec.get("filter_pass")
    filter_reasons = rec.get("filter_reasons", [])
    decision = rec.get("decision")
    action = rec.get("action")

    # decision_detail から
    detail = rec.get("decision_detail", {})
    detail_action = detail.get("action")
    detail_side = detail.get("side")
    signal = detail.get("signal", {})
    signal_side = signal.get("side")
    signal_pass_threshold = signal.get("pass_threshold")
    signal_reason = signal.get("reason")
    signal_confidence = signal.get("confidence")

    # decision_context から
    ctx = rec.get("decision_context", {})
    ai_ctx = ctx.get("ai", {})
    threshold = ai_ctx.get("threshold")
    filters_ctx = ctx.get("filters", {})
    filter_level = filters_ctx.get("filter_level")

    # 最終的な action と side を決定
    final_action = action or detail_action or decision or "UNKNOWN"
    final_side = detail_side or signal_side

    return {
        "prob_buy": prob_buy,
        "prob_sell": prob_sell,
        "threshold": threshold,
        "filter_level": filter_level,
        "filter_pass": filter_pass,
        "filter_reasons": filter_reasons if isinstance(filter_reasons, list) else [],
        "decision": decision,
        "action": final_action,
        "side": final_side,
        "signal_side": signal_side,
        "signal_pass_threshold": signal_pass_threshold,
        "signal_reason": signal_reason,
        "signal_confidence": signal_confidence,
    }


def classify_entry_attempt(fields: Dict[str, Any]) -> str:
    """
    ENTRY試行を分類する

    Parameters
    ----------
    fields : dict
        抽出されたフィールド

    Returns
    -------
    str
        分類ラベル
    """
    prob_buy = fields.get("prob_buy")
    prob_sell = fields.get("prob_sell")
    threshold = fields.get("threshold")
    signal_pass_threshold = fields.get("signal_pass_threshold")
    filter_pass = fields.get("filter_pass")
    action = fields.get("action", "").upper()
    side = fields.get("side")

    # 確率が None または NaN
    if prob_buy is None or prob_sell is None:
        return "no_prob"
    try:
        if float(prob_buy) != float(prob_buy) or float(prob_sell) != float(prob_sell):  # NaN check
            return "prob_nan"
    except (TypeError, ValueError):
        return "prob_invalid"

    # threshold 未到達
    if threshold is not None:
        max_prob = max(prob_buy, prob_sell)
        if max_prob < threshold:
            return "below_threshold"

    # signal レベルで threshold 未通過
    if signal_pass_threshold is False:
        return "signal_threshold_ng"

    # filter で reject
    if filter_pass is False:
        return "filter_reject"

    # action が ENTRY でない
    if action != "ENTRY":
        return f"action_not_entry:{action}"

    # side が None
    if side is None:
        return "side_none"

    # 実ENTRY
    return "entry_actual"


def aggregate_decisions(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    決定レコードを集計する

    Parameters
    ----------
    decisions : list[dict]
        決定レコードのリスト

    Returns
    -------
    dict
        集計結果
    """
    total_bars = len(decisions)
    if total_bars == 0:
        return {
            "total_bars": 0,
            "n_candidate": 0,
            "n_filter_pass": 0,
            "n_entry": 0,
            "classification": {},
            "filter_reasons_top": [],
        }

    # フィールド抽出
    fields_list = [extract_decision_fields(rec) for rec in decisions]

    # 分類
    classifications = [classify_entry_attempt(f) for f in fields_list]
    classification_counts = Counter(classifications)

    # threshold 到達候補数（prob_buy または prob_sell が threshold 以上）
    n_candidate = 0
    for f in fields_list:
        prob_buy = f.get("prob_buy")
        prob_sell = f.get("prob_sell")
        threshold = f.get("threshold")
        if threshold is not None and prob_buy is not None and prob_sell is not None:
            try:
                if max(prob_buy, prob_sell) >= threshold:
                    n_candidate += 1
            except (TypeError, ValueError):
                pass

    # filter_pass 数
    n_filter_pass = sum(1 for f in fields_list if f.get("filter_pass") is True)

    # 実ENTRY数（action=ENTRY かつ side が存在）
    n_entry = sum(
        1
        for f in fields_list
        if f.get("action", "").upper() == "ENTRY" and f.get("side") is not None
    )

    # filter_reasons の集計
    all_reasons = []
    for f in fields_list:
        reasons = f.get("filter_reasons", [])
        if isinstance(reasons, list):
            all_reasons.extend(reasons)
    filter_reasons_top = Counter(all_reasons).most_common(10)

    return {
        "total_bars": total_bars,
        "n_candidate": n_candidate,
        "n_filter_pass": n_filter_pass,
        "n_entry": n_entry,
        "classification": dict(classification_counts),
        "filter_reasons_top": filter_reasons_top,
    }


def sweep_threshold(
    decisions: List[Dict[str, Any]], thresholds: List[float]
) -> pd.DataFrame:
    """
    threshold スイープ分析

    Parameters
    ----------
    decisions : list[dict]
        決定レコードのリスト
    thresholds : list[float]
        試行する threshold のリスト

    Returns
    -------
    pd.DataFrame
        スイープ結果（threshold, n_candidate, n_filter_pass, n_entry）
    """
    rows = []
    for th in thresholds:
        # 一時的に threshold を上書きして集計
        modified_decisions = []
        for rec in decisions:
            rec_copy = json.loads(json.dumps(rec))  # deep copy
            # decision_context.ai.threshold を上書き
            if "decision_context" not in rec_copy:
                rec_copy["decision_context"] = {}
            if "ai" not in rec_copy["decision_context"]:
                rec_copy["decision_context"]["ai"] = {}
            rec_copy["decision_context"]["ai"]["threshold"] = th
            # decision_detail.signal も更新（pass_threshold を再計算）
            if "decision_detail" in rec_copy:
                signal = rec_copy["decision_detail"].get("signal", {})
                if signal:
                    prob_buy = rec_copy.get("prob_buy")
                    prob_sell = rec_copy.get("prob_sell")
                    if prob_buy is not None and prob_sell is not None:
                        max_prob = max(prob_buy, prob_sell)
                        signal["pass_threshold"] = max_prob >= th
                        signal["best_threshold"] = th
            modified_decisions.append(rec_copy)

        agg = aggregate_decisions(modified_decisions)
        rows.append(
            {
                "threshold": th,
                "n_candidate": agg["n_candidate"],
                "n_filter_pass": agg["n_filter_pass"],
                "n_entry": agg["n_entry"],
            }
        )

    return pd.DataFrame(rows)


def sweep_filter_level(
    decisions: List[Dict[str, Any]], filter_levels: List[int]
) -> pd.DataFrame:
    """
    filter_level スイープ分析

    Parameters
    ----------
    decisions : list[dict]
        決定レコードのリスト
    filter_levels : list[int]
        試行する filter_level のリスト

    Returns
    -------
    pd.DataFrame
        スイープ結果（filter_level, n_candidate, n_filter_pass, n_entry）
    """
    rows = []
    for fl in filter_levels:
        # 一時的に filter_level を上書きして集計
        # 注意: filter_pass は実際のフィルタエンジンの結果なので、
        # ここでは既存の filter_pass をそのまま使用（実際の再評価は不可）
        # ただし、filter_level が異なる場合の影響は限定的
        modified_decisions = []
        for rec in decisions:
            rec_copy = json.loads(json.dumps(rec))  # deep copy
            # decision_context.filters.filter_level を上書き
            if "decision_context" not in rec_copy:
                rec_copy["decision_context"] = {}
            if "filters" not in rec_copy["decision_context"]:
                rec_copy["decision_context"]["filters"] = {}
            rec_copy["decision_context"]["filters"]["filter_level"] = fl
            # filters.filter_level も更新
            if "filters" in rec_copy:
                rec_copy["filters"]["filter_level"] = fl
            modified_decisions.append(rec_copy)

        agg = aggregate_decisions(modified_decisions)
        rows.append(
            {
                "filter_level": fl,
                "n_candidate": agg["n_candidate"],
                "n_filter_pass": agg["n_filter_pass"],
                "n_entry": agg["n_entry"],
            }
        )

    return pd.DataFrame(rows)


def print_summary(agg: Dict[str, Any]) -> None:
    """
    集計結果を表示

    Parameters
    ----------
    agg : dict
        集計結果
    """
    print("=" * 80)
    print("VirtualBT 取引頻度分析サマリ")
    print("=" * 80)
    print(f"総バー数 (N): {agg['total_bars']:,}")
    print(f"threshold到達候補数 (N_candidate): {agg['n_candidate']:,}")
    print(f"filter_pass=true 数 (N_filter_pass): {agg['n_filter_pass']:,}")
    print(f"実ENTRY数 (N_entry): {agg['n_entry']:,}")
    print()

    print("候補→非ENTRY の減衰（段階別分類）:")
    classification = agg["classification"]
    for label, count in sorted(classification.items(), key=lambda x: -x[1]):
        pct = (count / agg["total_bars"] * 100) if agg["total_bars"] > 0 else 0.0
        print(f"  {label:30s}: {count:6,} ({pct:5.2f}%)")
    print()

    print("filter_reasons 上位頻出（Top10）:")
    for reason, count in agg["filter_reasons_top"]:
        pct = (count / agg["total_bars"] * 100) if agg["total_bars"] > 0 else 0.0
        print(f"  {reason:50s}: {count:6,} ({pct:5.2f}%)")
    if not agg["filter_reasons_top"]:
        print("  (なし)")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="VirtualBT 取引頻度分析ツール")
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="探索起点ディレクトリ（デフォルト: プロジェクトルート）",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        help="decisions.jsonl の直接指定（指定時は自動探索をスキップ）",
    )
    parser.add_argument(
        "--thresholds",
        type=str,
        help="threshold スイープ範囲（例: 0.35:0.65:0.05）",
    )
    parser.add_argument(
        "--filter-levels",
        type=str,
        help="filter_level スイープ（例: 0,1,2,3）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="読み込む最大行数（デバッグ用）",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="CSV出力パス（スイープ結果用）",
    )

    args = parser.parse_args()

    # decisions.jsonl を発見
    if args.decisions:
        decisions_path = args.decisions
    else:
        decisions_path = find_latest_decisions_jsonl(args.root)
        if decisions_path is None:
            print(f"ERROR: decisions.jsonl が見つかりません（探索起点: {args.root}）", file=sys.stderr)
            sys.exit(1)

    print(f"[INFO] decisions.jsonl: {decisions_path}", flush=True)

    # 読み込み
    decisions = load_decisions_jsonl(decisions_path, limit=args.limit)
    print(f"[INFO] 読み込み完了: {len(decisions):,} 行", flush=True)

    if not decisions:
        print("ERROR: decisions.jsonl が空です", file=sys.stderr)
        sys.exit(1)

    # 基本集計
    agg = aggregate_decisions(decisions)
    print_summary(agg)

    # threshold スイープ
    if args.thresholds:
        parts = args.thresholds.split(":")
        if len(parts) == 3:
            start = float(parts[0])
            stop = float(parts[1])
            step = float(parts[2])
            thresholds = []
            th = start
            while th <= stop:
                thresholds.append(round(th, 3))
                th += step
        else:
            thresholds = [float(x) for x in args.thresholds.split(",")]
        print("=" * 80)
        print("threshold スイープ結果")
        print("=" * 80)
        df_th = sweep_threshold(decisions, thresholds)
        print(df_th.to_string(index=False))
        print()
        if args.csv:
            csv_path = Path(args.csv)
            if csv_path.suffix != ".csv":
                csv_path = csv_path.parent / f"{csv_path.name}_threshold_sweep.csv"
            df_th.to_csv(csv_path, index=False)
            print(f"[INFO] CSV出力: {csv_path}", flush=True)

    # filter_level スイープ
    if args.filter_levels:
        filter_levels = [int(x) for x in args.filter_levels.split(",")]
        print("=" * 80)
        print("filter_level スイープ結果")
        print("=" * 80)
        df_fl = sweep_filter_level(decisions, filter_levels)
        print(df_fl.to_string(index=False))
        print()
        if args.csv:
            csv_path = Path(args.csv)
            if csv_path.suffix != ".csv":
                csv_path = csv_path.parent / f"{csv_path.name}_filter_level_sweep.csv"
            else:
                csv_path = csv_path.parent / f"{csv_path.stem}_filter_level{csv_path.suffix}"
            df_fl.to_csv(csv_path, index=False)
            print(f"[INFO] CSV出力: {csv_path}", flush=True)


if __name__ == "__main__":
    main()
