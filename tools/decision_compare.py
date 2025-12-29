# tools/decision_compare.py
"""
Live vs Backtest Decision Log Comparison Tool

【目的】
    live/dryrun と backtest の decision ログを集計・比較し、
    profile/timeframe/symbol 単位でパフォーマンス差分を可視化する。

【使用方法】
    python -X utf8 tools/decision_compare.py \
        --decisions-glob "logs/decisions_*.jsonl" \
        --backtest-glob "logs/backtest/**/decisions.jsonl" \
        --out-json reports/decision_compare.json \
        --out-md reports/decision_compare.md

【出力】
    - JSON: 集計結果の構造化データ
    - Markdown: テーブル形式の比較レポート（標準出力にも表示）
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict, Counter
from glob import glob

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_decision_logs(glob_pattern: str) -> List[Dict[str, Any]]:
    """
    glob パターンで指定された decision ログファイルを読み込む。

    Parameters
    ----------
    glob_pattern : str
        glob パターン（例: "logs/decisions_*.jsonl"）

    Returns
    -------
    List[Dict[str, Any]]
        読み込んだ decision record のリスト（type=="decision" のみ）
    """
    records: List[Dict[str, Any]] = []
    files = glob(glob_pattern, recursive=True)
    
    for file_path in files:
        path = Path(file_path)
        if not path.exists():
            continue
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        # type=="decision" のみ対象
                        if record.get("type") == "decision":
                            records.append(record)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"[warn] Failed to read {path}: {e}", file=sys.stderr)
            continue
    
    return records


def extract_runtime_info(record: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    record から runtime 情報を抽出する（フォールバック付き）。

    Parameters
    ----------
    record : Dict[str, Any]
        decision record

    Returns
    -------
    Optional[Dict[str, str]]
        {
            "schema_version": str,
            "mode": str,
            "source": str,
            "profile": str,
            "timeframe": str,
            "symbol": str,
        }
        runtime が無い/古い場合は None を返す
    """
    runtime = record.get("runtime")
    if not isinstance(runtime, dict):
        return None
    
    # schema_version が無い/古い場合はスキップ
    schema_version = runtime.get("schema_version")
    if schema_version not in (1, 2):
        return None
    
    mode = runtime.get("mode")
    source = runtime.get("source")
    
    # profile の取得（フォールバック）
    profile = runtime.get("profile")
    if not profile:
        filters = record.get("filters", {})
        profile_stats = filters.get("profile_stats", {})
        profile = profile_stats.get("current_profile") or profile_stats.get("profile_name")
    if not profile:
        profile = "unknown"
    
    # timeframe の取得（フォールバック）
    timeframe = runtime.get("timeframe")
    if not timeframe:
        timeframe = record.get("timeframe")
    if not timeframe:
        meta = record.get("meta", {})
        timeframe = meta.get("timeframe")
    if not timeframe:
        timeframe = "unknown"
    
    # symbol の取得
    symbol = runtime.get("symbol") or record.get("symbol", "unknown")
    
    return {
        "schema_version": str(schema_version),
        "mode": mode or "unknown",
        "source": source or "unknown",
        "profile": profile,
        "timeframe": timeframe,
        "symbol": symbol,
    }


def extract_timestamp(record: Dict[str, Any]) -> Optional[str]:
    """
    record からタイムスタンプを抽出する（優先順位付き）。

    Parameters
    ----------
    record : Dict[str, Any]
        decision record

    Returns
    -------
    Optional[str]
        タイムスタンプ文字列（ISO形式）
    """
    # 優先順位: ts_jst > runtime.ts > timestamp
    ts = record.get("ts_jst")
    if ts:
        return str(ts)
    
    runtime = record.get("runtime")
    if isinstance(runtime, dict):
        ts = runtime.get("ts")
        if ts:
            return str(ts)
    
    ts = record.get("timestamp")
    if ts:
        return str(ts)
    
    return None


def calculate_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    records から集計指標を計算する。

    Parameters
    ----------
    records : List[Dict[str, Any]]
        decision records

    Returns
    -------
    Dict[str, Any]
        集計指標
    """
    n = len(records)
    if n == 0:
        return {
            "n": 0,
            "filter_pass_rate": 0.0,
            "entry_rate": "unknown",
            "skip_rate": "unknown",
            "blocked_rate": "unknown",
            "side_buy_rate": "unknown",
            "top_blocked_reasons": [],
            "min_ts": None,
            "max_ts": None,
        }
    
    # タイムスタンプの範囲
    timestamps: List[str] = []
    for record in records:
        ts = extract_timestamp(record)
        if ts:
            timestamps.append(ts)
    min_ts = min(timestamps) if timestamps else None
    max_ts = max(timestamps) if timestamps else None
    
    # filter_pass_rate
    filter_pass_count = sum(1 for r in records if r.get("filter_pass") is True)
    filter_pass_rate = filter_pass_count / n if n > 0 else 0.0
    
    # decision_detail から action を取得
    entry_count = 0
    skip_count = 0
    blocked_count = 0
    side_buy_count = 0
    blocked_reasons: List[str] = []
    
    for record in records:
        decision_detail = record.get("decision_detail", {})
        action = decision_detail.get("action")
        
        if action == "ENTRY":
            entry_count += 1
        elif action == "SKIP":
            skip_count += 1
        elif action == "BLOCKED":
            blocked_count += 1
        
        # decision フィールドも確認（後方互換性）
        if action is None:
            decision = record.get("decision")
            if decision == "ENTRY":
                entry_count += 1
            elif decision == "SKIP":
                skip_count += 1
            elif decision == "BLOCKED":
                blocked_count += 1
        
        # side
        side = decision_detail.get("side") or record.get("side")
        if side == "BUY":
            side_buy_count += 1
        
        # blocked_reason
        blocked_reason = decision_detail.get("blocked_reason") or decision_detail.get("reason")
        if blocked_reason:
            blocked_reasons.append(blocked_reason)
        
        # filter_reasons も確認
        filter_reasons = record.get("filter_reasons", [])
        if filter_reasons:
            blocked_reasons.extend(filter_reasons)
    
    # rates
    entry_rate = entry_count / n if n > 0 else 0.0
    skip_rate = skip_count / n if n > 0 else 0.0
    blocked_rate = blocked_count / n if n > 0 else 0.0
    side_buy_rate = side_buy_count / n if n > 0 else 0.0
    
    # top_blocked_reasons (Top3 + 率)
    top_blocked_reasons: List[Dict[str, Any]] = []
    if blocked_reasons and blocked_count > 0:
        counter = Counter(blocked_reasons)
        total_blocked = len(blocked_reasons)  # 理由の総数（1レコードに複数理由がある場合）
        for reason, count in counter.most_common(3):
            rate = (count / total_blocked) * 100.0 if total_blocked > 0 else 0.0
            top_blocked_reasons.append({
                "reason": reason,
                "count": count,
                "rate": round(rate, 1),
            })
    
    return {
        "n": n,
        "filter_pass_rate": round(filter_pass_rate, 4),
        "entry_rate": round(entry_rate, 4) if entry_count > 0 or skip_count > 0 or blocked_count > 0 else "unknown",
        "skip_rate": round(skip_rate, 4) if entry_count > 0 or skip_count > 0 or blocked_count > 0 else "unknown",
        "blocked_rate": round(blocked_rate, 4) if entry_count > 0 or skip_count > 0 or blocked_count > 0 else "unknown",
        "side_buy_rate": round(side_buy_rate, 4) if side_buy_count > 0 else "unknown",
        "top_blocked_reasons": top_blocked_reasons,
        "min_ts": min_ts,
        "max_ts": max_ts,
    }


def aggregate_by_key(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    records を集計キー（mode, source, profile, timeframe, symbol）でグループ化する。

    Parameters
    ----------
    records : List[Dict[str, Any]]
        decision records

    Returns
    -------
    Dict[str, List[Dict[str, Any]]]
        キー: "{mode}|{source}|{profile}|{timeframe}|{symbol}"
        値: 該当する records のリスト
    """
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    
    for record in records:
        runtime_info = extract_runtime_info(record)
        if runtime_info is None:
            # runtime が無い/古い場合は "unknown" グループに
            key = "unknown|unknown|unknown|unknown|unknown"
            grouped[key].append(record)
            continue
        
        key = "|".join([
            runtime_info["mode"],
            runtime_info["source"],
            runtime_info["profile"],
            runtime_info["timeframe"],
            runtime_info["symbol"],
        ])
        grouped[key].append(record)
    
    return dict(grouped)


def format_blocked_reasons(top_blocked_reasons: List[Dict[str, Any]]) -> str:
    """
    top_blocked_reasons を文字列にフォーマットする。

    Parameters
    ----------
    top_blocked_reasons : List[Dict[str, Any]]
        [{"reason": "...", "count": ..., "rate": ...}, ...]

    Returns
    -------
    str
        フォーマット済み文字列（例: "volatility(41.0%) / adx_low(33.0%) / spread(12.0%)"）
    """
    if not top_blocked_reasons:
        return "-"
    
    parts = [f"{item['reason']}({item['rate']}%)" for item in top_blocked_reasons]
    return " / ".join(parts)


def format_coverage_short(min_ts: Optional[str], max_ts: Optional[str]) -> str:
    """
    タイムスタンプを短縮形式でフォーマットする。

    Parameters
    ----------
    min_ts : Optional[str]
        最小タイムスタンプ
    max_ts : Optional[str]
        最大タイムスタンプ

    Returns
    -------
    str
        短縮形式（例: "2025-12-01..2025-12-02"）
    """
    if not min_ts or not max_ts:
        return "-"
    
    # ISO形式から日付部分だけ抽出（簡易版）
    try:
        min_date = min_ts.split("T")[0].split(" ")[0]
        max_date = max_ts.split("T")[0].split(" ")[0]
        return f"{min_date}..{max_date}"
    except Exception:
        return f"{min_ts}..{max_ts}"


def build_comparable_pairs(
    comparison_groups: Dict[str, Dict[str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    比較可能なペア（live と backtest の両方が存在し、unknown なし）を構築する。

    Parameters
    ----------
    comparison_groups : Dict[str, Dict[str, Dict[str, Any]]]
        比較グループ（comp_key -> {source -> data}）

    Returns
    -------
    List[Dict[str, Any]]
        比較可能なペアのリスト
    """
    pairs: List[Dict[str, Any]] = []
    
    for comp_key, group in comparison_groups.items():
        parts = comp_key.split("|")
        profile = parts[0]
        timeframe = parts[1]
        symbol = parts[2]
        
        # unknown が混ざる場合はスキップ
        if profile == "unknown" or timeframe == "unknown" or symbol == "unknown":
            continue
        
        live_data = group.get("mt5")
        backtest_data = group.get("backtest")
        
        # 両方が存在する場合のみ
        if not live_data or not backtest_data:
            continue
        
        live_metrics = live_data["metrics"]
        backtest_metrics = backtest_data["metrics"]
        
        # delta を計算
        delta: Dict[str, Any] = {}
        if isinstance(live_metrics["entry_rate"], (int, float)) and isinstance(backtest_metrics["entry_rate"], (int, float)):
            delta["entry_rate"] = backtest_metrics["entry_rate"] - live_metrics["entry_rate"]
        else:
            delta["entry_rate"] = None
        
        if isinstance(live_metrics["filter_pass_rate"], (int, float)) and isinstance(backtest_metrics["filter_pass_rate"], (int, float)):
            delta["filter_pass_rate"] = backtest_metrics["filter_pass_rate"] - live_metrics["filter_pass_rate"]
        else:
            delta["filter_pass_rate"] = None
        
        if isinstance(live_metrics["blocked_rate"], (int, float)) and isinstance(backtest_metrics["blocked_rate"], (int, float)):
            delta["blocked_rate"] = backtest_metrics["blocked_rate"] - live_metrics["blocked_rate"]
        else:
            delta["blocked_rate"] = None
        
        pairs.append({
            "join_key": {
                "symbol": symbol,
                "profile": profile,
                "timeframe": timeframe,
            },
            "live": {
                "entry_rate": live_metrics["entry_rate"],
                "filter_pass_rate": live_metrics["filter_pass_rate"],
                "blocked_rate": live_metrics["blocked_rate"],
                "top_blocked_reasons": live_metrics["top_blocked_reasons"],
                "min_ts": live_metrics["min_ts"],
                "max_ts": live_metrics["max_ts"],
            },
            "backtest": {
                "entry_rate": backtest_metrics["entry_rate"],
                "filter_pass_rate": backtest_metrics["filter_pass_rate"],
                "blocked_rate": backtest_metrics["blocked_rate"],
                "top_blocked_reasons": backtest_metrics["top_blocked_reasons"],
                "min_ts": backtest_metrics["min_ts"],
                "max_ts": backtest_metrics["max_ts"],
            },
            "delta": delta,
            "comparable": True,
        })
    
    return pairs


def build_delta_rankings(
    pairs: List[Dict[str, Any]],
    top_n: int,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    差分ランキングを構築する。

    Parameters
    ----------
    pairs : List[Dict[str, Any]]
        比較可能なペアのリスト
    top_n : int
        Top N（0 の場合は空のランキングを返す）

    Returns
    -------
    Dict[str, List[Dict[str, Any]]]
        {
            "entry_rate": [...],
            "filter_pass_rate": [...],
            "blocked_rate": [...],
        }
    """
    if top_n <= 0:
        return {
            "entry_rate": [],
            "filter_pass_rate": [],
            "blocked_rate": [],
        }
    
    rankings: Dict[str, List[Dict[str, Any]]] = {
        "entry_rate": [],
        "filter_pass_rate": [],
        "blocked_rate": [],
    }
    
    import math
    
    for metric in ["entry_rate", "filter_pass_rate", "blocked_rate"]:
        candidates = []
        for pair in pairs:
            if not pair["comparable"]:
                continue
            
            delta_val = pair["delta"].get(metric)
            if delta_val is None:
                continue
            
            # NaN/None チェック
            if not isinstance(delta_val, (int, float)) or math.isnan(delta_val):
                continue
            
            candidates.append((pair, abs(delta_val)))
        
        # abs で降順ソート
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        # Top N を取得
        for rank, (pair, abs_delta) in enumerate(candidates[:top_n], start=1):
            rankings[metric].append({
                "rank": rank,
                "pair": pair,
                "abs_delta": abs_delta,
            })
    
    return rankings


def evaluate_alerts(
    pairs: List[Dict[str, Any]],
    thresholds: Dict[str, float],
) -> tuple[bool, List[Dict[str, Any]]]:
    """
    閾値アラートを評価する。

    Parameters
    ----------
    pairs : List[Dict[str, Any]]
        比較可能なペアのリスト
    thresholds : Dict[str, float]
        {
            "entry_rate": 0.05,
            "filter_pass_rate": 0.10,
            "blocked_rate": 0.10,
        }

    Returns
    -------
    tuple[bool, List[Dict[str, Any]]]
        (ok: bool, violations: list)
        violations には {metric, threshold, row} を含む
    """
    violations: List[Dict[str, Any]] = []
    
    import math
    
    for pair in pairs:
        if not pair["comparable"]:
            continue
        
        delta = pair["delta"]
        
        # abs(Δentry_rate) チェック
        if "entry_rate" in thresholds:
            delta_entry = delta.get("entry_rate")
            if delta_entry is not None and isinstance(delta_entry, (int, float)) and not math.isnan(delta_entry):
                if abs(delta_entry) > thresholds["entry_rate"]:
                    violations.append({
                        "metric": "entry_rate",
                        "threshold": thresholds["entry_rate"],
                        "abs_delta": abs(delta_entry),
                        "delta": delta_entry,
                        "row": pair,
                    })
        
        # abs(Δfilter_pass_rate) チェック
        if "filter_pass_rate" in thresholds:
            delta_fp = delta.get("filter_pass_rate")
            if delta_fp is not None and isinstance(delta_fp, (int, float)) and not math.isnan(delta_fp):
                if abs(delta_fp) > thresholds["filter_pass_rate"]:
                    violations.append({
                        "metric": "filter_pass_rate",
                        "threshold": thresholds["filter_pass_rate"],
                        "abs_delta": abs(delta_fp),
                        "delta": delta_fp,
                        "row": pair,
                    })
        
        # abs(Δblocked_rate) チェック
        if "blocked_rate" in thresholds:
            delta_br = delta.get("blocked_rate")
            if delta_br is not None and isinstance(delta_br, (int, float)) and not math.isnan(delta_br):
                if abs(delta_br) > thresholds["blocked_rate"]:
                    violations.append({
                        "metric": "blocked_rate",
                        "threshold": thresholds["blocked_rate"],
                        "abs_delta": abs(delta_br),
                        "delta": delta_br,
                        "row": pair,
                    })
    
    ok = len(violations) == 0
    return (ok, violations)


def generate_comparison_table(
    aggregated: Dict[str, List[Dict[str, Any]]],
    top_n: int = 10,
    violations: Optional[List[Dict[str, Any]]] = None,
    comparable_pairs: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    集計結果から Markdown テーブルを生成する。

    Parameters
    ----------
    aggregated : Dict[str, List[Dict[str, Any]]]
        集計キー -> records のマッピング

    Returns
    -------
    str
        Markdown テーブル
    """
    lines: List[str] = []
    lines.append("# Decision Log Comparison Report")
    lines.append("")
    
    # 全体の期間を計算
    all_timestamps: List[str] = []
    for records in aggregated.values():
        for record in records:
            ts = extract_timestamp(record)
            if ts:
                all_timestamps.append(ts)
    
    if all_timestamps:
        min_ts = min(all_timestamps)
        max_ts = max(all_timestamps)
        lines.append(f"**Coverage:** {min_ts} ～ {max_ts}")
        lines.append("")
    
    lines.append("## Summary by Mode/Source/Profile/Timeframe/Symbol")
    lines.append("")
    
    # テーブルヘッダ
    lines.append("| Mode | Source | Profile | Timeframe | Symbol | n | filter_pass_rate | entry_rate | skip_rate | blocked_rate | side_buy_rate | top_blocked_reasons | min_ts | max_ts |")
    lines.append("|------|--------|---------|-----------|--------|---|-------------------|------------|-----------|--------------|---------------|-------------------|--------|--------|")
    
    # ソート: profile, timeframe, symbol, mode, source
    sorted_keys = sorted(aggregated.keys())
    
    for key in sorted_keys:
        records = aggregated[key]
        metrics = calculate_metrics(records)
        
        parts = key.split("|")
        mode = parts[0]
        source = parts[1]
        profile = parts[2]
        timeframe = parts[3]
        symbol = parts[4]
        
        blocked_reasons_str = format_blocked_reasons(metrics["top_blocked_reasons"])
        min_ts = metrics["min_ts"] or "-"
        max_ts = metrics["max_ts"] or "-"
        
        lines.append(
            f"| {mode} | {source} | {profile} | {timeframe} | {symbol} | "
            f"{metrics['n']} | {metrics['filter_pass_rate']:.4f} | "
            f"{metrics['entry_rate']} | {metrics['skip_rate']} | "
            f"{metrics['blocked_rate']} | {metrics['side_buy_rate']} | "
            f"{blocked_reasons_str} | {min_ts} | {max_ts} |"
        )
    
    lines.append("")
    
    # Live vs Backtest 比較セクション
    lines.append("## Live vs Backtest Comparison")
    lines.append("")
    lines.append("同一 (profile, timeframe, symbol) で live(mt5) と backtest(backtest) を比較")
    lines.append("")
    
    # profile, timeframe, symbol でグループ化
    comparison_groups: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    unmatched_groups: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    
    for key, records in aggregated.items():
        parts = key.split("|")
        mode = parts[0]
        source = parts[1]
        profile = parts[2]
        timeframe = parts[3]
        symbol = parts[4]
        
        # live(mt5) と backtest(backtest) のみ比較対象
        if source not in ("mt5", "backtest"):
            continue
        
        comp_key = f"{profile}|{timeframe}|{symbol}"
        metrics = calculate_metrics(records)
        
        # unknown が混ざる場合は unmatched に
        if profile == "unknown" or timeframe == "unknown" or symbol == "unknown":
            unmatched_groups[comp_key][source] = {
                "mode": mode,
                "source": source,
                "metrics": metrics,
                "key": key,
            }
        else:
            comparison_groups[comp_key][source] = {
                "mode": mode,
                "source": source,
                "metrics": metrics,
                "key": key,
            }
    
    # 比較可能なペアを構築（ランキング・アラート用）
    # generate_comparison_table() でも使用するため、先に構築
    
    # 比較テーブル生成（unknown なし）
    for comp_key in sorted(comparison_groups.keys()):
        parts = comp_key.split("|")
        profile = parts[0]
        timeframe = parts[1]
        symbol = parts[2]
        
        group = comparison_groups[comp_key]
        live_data = group.get("mt5")
        backtest_data = group.get("backtest")
        
        if not live_data and not backtest_data:
            continue
        
        lines.append(f"### {profile} / {timeframe} / {symbol}")
        lines.append("")
        lines.append(f"**JOIN KEY:** symbol={symbol} profile={profile} timeframe={timeframe}")
        lines.append("")
        lines.append("| Metric | Live (mt5) | Backtest | Δ (backtest - live) |")
        lines.append("|--------|------------|----------|---------------------|")
        
        if live_data and backtest_data:
            live_metrics = live_data["metrics"]
            backtest_metrics = backtest_data["metrics"]
            
            # n
            n_live = live_metrics["n"]
            n_backtest = backtest_metrics["n"]
            delta_n = n_backtest - n_live
            lines.append(f"| n | {n_live} | {n_backtest} | {delta_n:+d} |")
            
            # filter_pass_rate
            fp_live = live_metrics["filter_pass_rate"]
            fp_backtest = backtest_metrics["filter_pass_rate"]
            delta_fp = fp_backtest - fp_live
            lines.append(f"| filter_pass_rate | {fp_live:.4f} | {fp_backtest:.4f} | {delta_fp:+.4f} |")
            
            # entry_rate
            er_live = live_metrics["entry_rate"]
            er_backtest = backtest_metrics["entry_rate"]
            if isinstance(er_live, (int, float)) and isinstance(er_backtest, (int, float)):
                delta_er = er_backtest - er_live
                lines.append(f"| entry_rate | {er_live:.4f} | {er_backtest:.4f} | {delta_er:+.4f} |")
            else:
                lines.append(f"| entry_rate | {er_live} | {er_backtest} | - |")
            
            # skip_rate
            sr_live = live_metrics["skip_rate"]
            sr_backtest = backtest_metrics["skip_rate"]
            if isinstance(sr_live, (int, float)) and isinstance(sr_backtest, (int, float)):
                delta_sr = sr_backtest - sr_live
                lines.append(f"| skip_rate | {sr_live:.4f} | {sr_backtest:.4f} | {delta_sr:+.4f} |")
            else:
                lines.append(f"| skip_rate | {sr_live} | {sr_backtest} | - |")
            
            # blocked_rate
            br_live = live_metrics["blocked_rate"]
            br_backtest = backtest_metrics["blocked_rate"]
            if isinstance(br_live, (int, float)) and isinstance(br_backtest, (int, float)):
                delta_br = br_backtest - br_live
                lines.append(f"| blocked_rate | {br_live:.4f} | {br_backtest:.4f} | {delta_br:+.4f} |")
            else:
                lines.append(f"| blocked_rate | {br_live} | {br_backtest} | - |")
            
            # side_buy_rate
            sbr_live = live_metrics["side_buy_rate"]
            sbr_backtest = backtest_metrics["side_buy_rate"]
            if isinstance(sbr_live, (int, float)) and isinstance(sbr_backtest, (int, float)):
                delta_sbr = sbr_backtest - sbr_live
                lines.append(f"| side_buy_rate | {sbr_live:.4f} | {sbr_backtest:.4f} | {delta_sbr:+.4f} |")
            else:
                lines.append(f"| side_buy_rate | {sbr_live} | {sbr_backtest} | - |")
            
            # top_blocked_reasons
            tbr_live = format_blocked_reasons(live_metrics["top_blocked_reasons"])
            tbr_backtest = format_blocked_reasons(backtest_metrics["top_blocked_reasons"])
            lines.append(f"| top_blocked_reasons | {tbr_live} | {tbr_backtest} | - |")
        elif live_data:
            live_metrics = live_data["metrics"]
            lines.append(f"| n | {live_metrics['n']} | - | - |")
            lines.append(f"| filter_pass_rate | {live_metrics['filter_pass_rate']:.4f} | - | - |")
            lines.append(f"| entry_rate | {live_metrics['entry_rate']} | - | - |")
        elif backtest_data:
            backtest_metrics = backtest_data["metrics"]
            lines.append(f"| n | - | {backtest_metrics['n']} | - |")
            lines.append(f"| filter_pass_rate | - | {backtest_metrics['filter_pass_rate']:.4f} | - |")
            lines.append(f"| entry_rate | - | {backtest_metrics['entry_rate']} | - |")
        
        lines.append("")
    
    # 比較不能セクション（unknown が混ざる）
    if unmatched_groups:
        lines.append("## Unmatched or Unknown (Comparison Not Available)")
        lines.append("")
        lines.append("以下のグループは profile/timeframe/symbol に unknown が含まれるため、比較をスキップします。")
        lines.append("")
        
        for comp_key in sorted(unmatched_groups.keys()):
            parts = comp_key.split("|")
            profile = parts[0]
            timeframe = parts[1]
            symbol = parts[2]
            
            group = unmatched_groups[comp_key]
            live_data = group.get("mt5")
            backtest_data = group.get("backtest")
            
            lines.append(f"### {profile} / {timeframe} / {symbol} (比較不能)")
            lines.append("")
            lines.append(f"**JOIN KEY:** symbol={symbol} profile={profile} timeframe={timeframe}")
            lines.append("")
            
            if live_data:
                live_metrics = live_data["metrics"]
                lines.append(f"**Live (mt5):** n={live_metrics['n']}, filter_pass_rate={live_metrics['filter_pass_rate']:.4f}")
            if backtest_data:
                backtest_metrics = backtest_data["metrics"]
                lines.append(f"**Backtest:** n={backtest_metrics['n']}, filter_pass_rate={backtest_metrics['filter_pass_rate']:.4f}")
            
            lines.append("")
    
    # 差分ランキングセクション
    if top_n > 0:
        rankings = build_delta_rankings(comparable_pairs, top_n)
        
        lines.append("## Delta Ranking (Top N)")
        lines.append("")
        lines.append(f"比較可能なペア（JOIN KEY が揃って unknown なし）のみを対象とした差分ランキング（Top {top_n}）")
        lines.append("")
        
        if not comparable_pairs:
            lines.append("**No comparable pairs found.**")
            lines.append("")
        else:
            # abs(Δentry_rate)
            lines.append("### abs(Δentry_rate)")
            lines.append("")
            if rankings["entry_rate"]:
                lines.append("| Rank | JOIN KEY | Live | Backtest | Δ | Coverage | top_blocked_reasons (live/backtest) |")
                lines.append("|------|----------|------|----------|---|----------|-----------------------------------|")
                for item in rankings["entry_rate"]:
                    rank = item["rank"]
                    pair = item["pair"]
                    jk = pair["join_key"]
                    live = pair["live"]
                    backtest = pair["backtest"]
                    delta = pair["delta"]["entry_rate"]
                    
                    join_key_str = f"{jk['symbol']} / {jk['profile']} / {jk['timeframe']}"
                    live_val = live["entry_rate"]
                    backtest_val = backtest["entry_rate"]
                    delta_str = f"{delta:+.4f}" if isinstance(delta, (int, float)) else "-"
                    
                    coverage_live = format_coverage_short(live["min_ts"], live["max_ts"])
                    coverage_bt = format_coverage_short(backtest["min_ts"], backtest["max_ts"])
                    coverage_str = f"{coverage_live} / {coverage_bt}"
                    
                    live_reasons = format_blocked_reasons(live["top_blocked_reasons"])
                    bt_reasons = format_blocked_reasons(backtest["top_blocked_reasons"])
                    reasons_str = f"{live_reasons} / {bt_reasons}"
                    
                    lines.append(
                        f"| {rank} | {join_key_str} | "
                        f"{live_val:.4f if isinstance(live_val, (int, float)) else live_val} | "
                        f"{backtest_val:.4f if isinstance(backtest_val, (int, float)) else backtest_val} | "
                        f"{delta_str} | {coverage_str} | {reasons_str} |"
                    )
            else:
                lines.append("**No comparable pairs with valid entry_rate delta.**")
            lines.append("")
            
            # abs(Δfilter_pass_rate)
            lines.append("### abs(Δfilter_pass_rate)")
            lines.append("")
            if rankings["filter_pass_rate"]:
                lines.append("| Rank | JOIN KEY | Live | Backtest | Δ | Coverage | top_blocked_reasons (live/backtest) |")
                lines.append("|------|----------|------|----------|---|----------|-----------------------------------|")
                for item in rankings["filter_pass_rate"]:
                    rank = item["rank"]
                    pair = item["pair"]
                    jk = pair["join_key"]
                    live = pair["live"]
                    backtest = pair["backtest"]
                    delta = pair["delta"]["filter_pass_rate"]
                    
                    join_key_str = f"{jk['symbol']} / {jk['profile']} / {jk['timeframe']}"
                    live_val = live["filter_pass_rate"]
                    backtest_val = backtest["filter_pass_rate"]
                    delta_str = f"{delta:+.4f}" if isinstance(delta, (int, float)) else "-"
                    
                    coverage_live = format_coverage_short(live["min_ts"], live["max_ts"])
                    coverage_bt = format_coverage_short(backtest["min_ts"], backtest["max_ts"])
                    coverage_str = f"{coverage_live} / {coverage_bt}"
                    
                    live_reasons = format_blocked_reasons(live["top_blocked_reasons"])
                    bt_reasons = format_blocked_reasons(backtest["top_blocked_reasons"])
                    reasons_str = f"{live_reasons} / {bt_reasons}"
                    
                    lines.append(
                        f"| {rank} | {join_key_str} | "
                        f"{live_val:.4f} | {backtest_val:.4f} | "
                        f"{delta_str} | {coverage_str} | {reasons_str} |"
                    )
            else:
                lines.append("**No comparable pairs with valid filter_pass_rate delta.**")
            lines.append("")
            
            # abs(Δblocked_rate)
            lines.append("### abs(Δblocked_rate)")
            lines.append("")
            if rankings["blocked_rate"]:
                lines.append("| Rank | JOIN KEY | Live | Backtest | Δ | Coverage | top_blocked_reasons (live/backtest) |")
                lines.append("|------|----------|------|----------|---|----------|-----------------------------------|")
                for item in rankings["blocked_rate"]:
                    rank = item["rank"]
                    pair = item["pair"]
                    jk = pair["join_key"]
                    live = pair["live"]
                    backtest = pair["backtest"]
                    delta = pair["delta"]["blocked_rate"]
                    
                    join_key_str = f"{jk['symbol']} / {jk['profile']} / {jk['timeframe']}"
                    live_val = live["blocked_rate"]
                    backtest_val = backtest["blocked_rate"]
                    delta_str = f"{delta:+.4f}" if isinstance(delta, (int, float)) else "-"
                    
                    coverage_live = format_coverage_short(live["min_ts"], live["max_ts"])
                    coverage_bt = format_coverage_short(backtest["min_ts"], backtest["max_ts"])
                    coverage_str = f"{coverage_live} / {coverage_bt}"
                    
                    live_reasons = format_blocked_reasons(live["top_blocked_reasons"])
                    bt_reasons = format_blocked_reasons(backtest["top_blocked_reasons"])
                    reasons_str = f"{live_reasons} / {bt_reasons}"
                    
                    lines.append(
                        f"| {rank} | {join_key_str} | "
                        f"{live_val:.4f} | {backtest_val:.4f} | "
                        f"{delta_str} | {coverage_str} | {reasons_str} |"
                    )
            else:
                lines.append("**No comparable pairs with valid blocked_rate delta.**")
            lines.append("")
    
    # アラートセクション（violations がある場合）
    if violations:
        lines.append("## Alerts")
        lines.append("")
        lines.append("**FAIL:** 閾値を超えた差分が検出されました。")
        lines.append("")
        lines.append("| Rank | Metric | Threshold | abs(Δ) | JOIN KEY | Live | Backtest | Δ | Coverage | top_blocked_reasons (live/backtest) |")
        lines.append("|------|--------|-----------|--------|----------|------|----------|---|----------|-----------------------------------|")
        
        # 上位3件のみ表示
        for rank, violation in enumerate(violations[:3], start=1):
            metric = violation["metric"]
            threshold = violation["threshold"]
            abs_delta = violation["abs_delta"]
            delta = violation["delta"]
            row = violation["row"]
            
            jk = row["join_key"]
            live = row["live"]
            backtest = row["backtest"]
            
            join_key_str = f"{jk['symbol']} / {jk['profile']} / {jk['timeframe']}"
            
            # metric に応じた値を取得
            if metric == "entry_rate":
                live_val = live["entry_rate"]
                backtest_val = backtest["entry_rate"]
            elif metric == "filter_pass_rate":
                live_val = live["filter_pass_rate"]
                backtest_val = backtest["filter_pass_rate"]
            else:  # blocked_rate
                live_val = live["blocked_rate"]
                backtest_val = backtest["blocked_rate"]
            
            live_str = f"{live_val:.4f}" if isinstance(live_val, (int, float)) else str(live_val)
            backtest_str = f"{backtest_val:.4f}" if isinstance(backtest_val, (int, float)) else str(backtest_val)
            delta_str = f"{delta:+.4f}" if isinstance(delta, (int, float)) else "-"
            
            coverage_live = format_coverage_short(live["min_ts"], live["max_ts"])
            coverage_bt = format_coverage_short(backtest["min_ts"], backtest["max_ts"])
            coverage_str = f"{coverage_live} / {coverage_bt}"
            
            live_reasons = format_blocked_reasons(live["top_blocked_reasons"])
            bt_reasons = format_blocked_reasons(backtest["top_blocked_reasons"])
            reasons_str = f"{live_reasons} / {bt_reasons}"
            
            lines.append(
                f"| {rank} | {metric} | {threshold:.4f} | {abs_delta:.4f} | {join_key_str} | "
                f"{live_str} | {backtest_str} | {delta_str} | {coverage_str} | {reasons_str} |"
            )
        
        if len(violations) > 3:
            lines.append(f"| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ({len(violations) - 3} more violations)")
        
        lines.append("")
    elif violations is not None:
        # violations が空リストの場合（PASS）
        lines.append("## Alerts")
        lines.append("")
        lines.append("**PASS:** すべての閾値チェックを通過しました。")
        lines.append("")
    
    return "\n".join(lines)


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="Live vs Backtest Decision Log Comparison Tool",
        epilog="出力は JSON と Markdown の両方を生成します。",
    )
    parser.add_argument(
        "--decisions-glob",
        type=str,
        default="logs/decisions_*.jsonl",
        help="Live/dryrun decision ログの glob パターン",
    )
    parser.add_argument(
        "--backtest-glob",
        type=str,
        default="logs/backtest/**/decisions.jsonl",
        help="Backtest decision ログの glob パターン",
    )
    parser.add_argument(
        "--out-json",
        type=str,
        default="reports/decision_compare.json",
        help="JSON 出力先パス",
    )
    parser.add_argument(
        "--out-md",
        type=str,
        default="reports/decision_compare.md",
        help="Markdown 出力先パス",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="差分ランキングの Top N（0 の場合はランキング出力なし）",
    )
    parser.add_argument(
        "--fail-on-delta",
        action="store_true",
        help="閾値アラート判定を有効化（条件を満たしたら exit 2 で終了）",
    )
    parser.add_argument(
        "--delta-entry",
        type=float,
        default=0.05,
        help="abs(Δentry_rate) の閾値（デフォルト: 0.05）",
    )
    parser.add_argument(
        "--delta-filter-pass",
        type=float,
        default=0.10,
        help="abs(Δfilter_pass_rate) の閾値（デフォルト: 0.10）",
    )
    parser.add_argument(
        "--delta-blocked",
        type=float,
        default=0.10,
        help="abs(Δblocked_rate) の閾値（デフォルト: 0.10）",
    )
    parser.add_argument(
        "--min-comparable",
        type=int,
        default=0,
        help="最小比較可能ペア数（0 の場合は判定不能でも PASS、1以上なら判定不能時に FAIL）",
    )
    args = parser.parse_args()
    
    print("[decision_compare] Loading decision logs...")
    
    # ログ読み込み
    live_records = load_decision_logs(args.decisions_glob)
    backtest_records = load_decision_logs(args.backtest_glob)
    
    print(f"[decision_compare] Loaded {len(live_records)} live records")
    print(f"[decision_compare] Loaded {len(backtest_records)} backtest records")
    
    # 統合
    all_records = live_records + backtest_records
    
    if not all_records:
        print("[decision_compare] WARNING: No records found", file=sys.stderr)
        return 1
    
    # 集計
    print("[decision_compare] Aggregating...")
    aggregated = aggregate_by_key(all_records)
    
    # 集計結果を構造化
    result: Dict[str, Any] = {
        "summary": {
            "total_records": len(all_records),
            "live_records": len(live_records),
            "backtest_records": len(backtest_records),
            "groups": len(aggregated),
        },
        "groups": {},
    }
    
    for key, records in aggregated.items():
        parts = key.split("|")
        metrics = calculate_metrics(records)
        
        # top_blocked_reasons を JSON シリアライズ可能な形式に変換
        metrics_for_json = dict(metrics)
        metrics_for_json["top_blocked_reasons"] = [
            {"reason": item["reason"], "count": item["count"], "rate": item["rate"]}
            for item in metrics["top_blocked_reasons"]
        ]
        
        result["groups"][key] = {
            "mode": parts[0],
            "source": parts[1],
            "profile": parts[2],
            "timeframe": parts[3],
            "symbol": parts[4],
            "metrics": metrics_for_json,
        }
    
    # JSON 出力
    out_json_path = Path(args.out_json)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[decision_compare] JSON written to: {out_json_path}")
    
    # 比較可能なペアを構築（ランキング・アラート用）
    comparison_groups_for_alerts: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for key, records in aggregated.items():
        parts = key.split("|")
        mode = parts[0]
        source = parts[1]
        profile = parts[2]
        timeframe = parts[3]
        symbol = parts[4]
        
        if source not in ("mt5", "backtest"):
            continue
        
        comp_key = f"{profile}|{timeframe}|{symbol}"
        metrics = calculate_metrics(records)
        
        if profile == "unknown" or timeframe == "unknown" or symbol == "unknown":
            continue
        
        comparison_groups_for_alerts[comp_key][source] = {
            "mode": mode,
            "source": source,
            "metrics": metrics,
        }
    
    comparable_pairs_for_alerts = build_comparable_pairs(comparison_groups_for_alerts)
    
    # 閾値アラート判定
    violations: Optional[List[Dict[str, Any]]] = None
    if args.fail_on_delta:
        # 最小比較可能ペア数チェック
        if args.min_comparable > 0 and len(comparable_pairs_for_alerts) < args.min_comparable:
            print(
                f"[compare_alert] FAIL: Not enough comparable pairs "
                f"(found {len(comparable_pairs_for_alerts)}, required {args.min_comparable})",
                file=sys.stderr,
            )
            return 2
        
        thresholds = {
            "entry_rate": args.delta_entry,
            "filter_pass_rate": args.delta_filter_pass,
            "blocked_rate": args.delta_blocked,
        }
        
        ok, violations = evaluate_alerts(comparable_pairs_for_alerts, thresholds)
        
        if not ok:
            # FAIL: 標準出力にアラート情報を表示
            print("[compare_alert] FAIL: Threshold violations detected", file=sys.stderr)
            print(f"[compare_alert] Total violations: {len(violations)}", file=sys.stderr)
            print("", file=sys.stderr)
            
            # 上位3件を表示
            print("[compare_alert] Top 3 violations:", file=sys.stderr)
            for rank, violation in enumerate(violations[:3], start=1):
                metric = violation["metric"]
                threshold = violation["threshold"]
                abs_delta = violation["abs_delta"]
                delta = violation["delta"]
                row = violation["row"]
                
                jk = row["join_key"]
                live = row["live"]
                backtest = row["backtest"]
                
                join_key_str = f"{jk['symbol']} / {jk['profile']} / {jk['timeframe']}"
                
                if metric == "entry_rate":
                    live_val = live["entry_rate"]
                    backtest_val = backtest["entry_rate"]
                elif metric == "filter_pass_rate":
                    live_val = live["filter_pass_rate"]
                    backtest_val = backtest["filter_pass_rate"]
                else:  # blocked_rate
                    live_val = live["blocked_rate"]
                    backtest_val = backtest["blocked_rate"]
                
                live_str = f"{live_val:.4f}" if isinstance(live_val, (int, float)) else str(live_val)
                backtest_str = f"{backtest_val:.4f}" if isinstance(backtest_val, (int, float)) else str(backtest_val)
                delta_str = f"{delta:+.4f}" if isinstance(delta, (int, float)) else "-"
                
                coverage_live = format_coverage_short(live["min_ts"], live["max_ts"])
                coverage_bt = format_coverage_short(backtest["min_ts"], backtest["max_ts"])
                coverage_str = f"{coverage_live} / {coverage_bt}"
                
                live_reasons = format_blocked_reasons(live["top_blocked_reasons"])
                bt_reasons = format_blocked_reasons(backtest["top_blocked_reasons"])
                reasons_str = f"{live_reasons} / {bt_reasons}"
                
                print(
                    f"[compare_alert] #{rank} {metric}: abs(Δ)={abs_delta:.4f} > {threshold:.4f} "
                    f"(JOIN KEY: {join_key_str}, Live: {live_str}, Backtest: {backtest_str}, Δ: {delta_str}, "
                    f"Coverage: {coverage_str}, Blocked: {reasons_str})",
                    file=sys.stderr,
                )
            
            if len(violations) > 3:
                print(f"[compare_alert] ... and {len(violations) - 3} more violations", file=sys.stderr)
            
            return 2
        else:
            print("[compare_alert] PASS: All threshold checks passed", file=sys.stderr)
    
    # Markdown 生成（comparable_pairs を渡す）
    markdown = generate_comparison_table(
        aggregated,
        top_n=args.top_n,
        violations=violations,
        comparable_pairs=comparable_pairs_for_alerts,
    )
    
    # Markdown 出力
    out_md_path = Path(args.out_md)
    out_md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"[decision_compare] Markdown written to: {out_md_path}")
    
    # 標準出力にも Markdown を表示
    print("\n" + "=" * 80)
    print(markdown)
    print("=" * 80)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
