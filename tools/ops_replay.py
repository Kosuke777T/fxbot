"""
Ops履歴から条件を復元して再実行するCLIツール

Usage:
    python -m tools.ops_replay [--log PATH] [--index N] [--run]

    --log: 対象ログパス（既定：logs/ops 配下の最新 ops_result*.jsonl を探索）
    --index: 末尾から何件目を使うか（既定：1=最新）
    --run: 指定時のみ実際に再実行。未指定ならコマンドを表示するだけ
"""
from __future__ import annotations

import sys
from pathlib import Path

# tools/*.py を直接実行した場合でも "app" を import できるようにする
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse
import json
import subprocess
from typing import Optional, Any

from app.services.wfo_stability_service import evaluate_wfo_stability, load_saved_stability


def _load_saved_wfo_stability(run_id: str) -> dict | None:
    """logs/retrain/stability_{run_id}.json を最優先で読む。壊れてたら None。"""
    # wfo_stability_service.load_saved_stability に統一（後方互換探索も含む）
    return load_saved_stability(run_id)


# プロジェクトルートを推定（tools/ops_replay.py → tools/ → プロジェクトルート）
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _normalize_profiles(profiles_raw) -> list[str]:
    """
    profiles を list[str] に正規化する。

    Args:
        profiles_raw: プロファイル名（list[str], list[str]（カンマ区切り文字列含む）, str, None など）

    Returns:
        正規化されたプロファイル名のリスト
    """
    if profiles_raw is None:
        return []

    # 文字列の場合はカンマで分割
    if isinstance(profiles_raw, str):
        profiles_raw = [profiles_raw]

    # リストの場合、各要素を処理
    result = []
    for item in profiles_raw:
        if not item:
            continue
        item_str = str(item).strip()
        if not item_str:
            continue

        # カンマ区切りの場合は分割
        if "," in item_str:
            parts = item_str.split(",")
            for part in parts:
                part = part.strip()
                if part:
                    result.append(part)
        else:
            result.append(item_str)

    # 重複除外（順序保持）
    seen = set()
    normalized = []
    for p in result:
        if p not in seen:
            seen.add(p)
            normalized.append(p)

    return normalized


def find_latest_ops_result_jsonl() -> Optional[Path]:
    """
    logs/ops 配下の最新 ops_result*.jsonl を探索する。

    Returns:
        見つかったファイルパス（見つからなければ None）
    """
    logs_dir = PROJECT_ROOT / "logs" / "ops"
    if not logs_dir.exists():
        return None

    # ops_result*.jsonl を探索
    candidates = list(logs_dir.glob("ops_result*.jsonl"))
    if not candidates:
        return None

    # 更新日時順にソートして最新を返す
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest


def load_record_from_jsonl(log_path: Path, index: int = 1) -> Optional[dict]:
    """
    JSONLファイルから末尾から index 番目のレコードを読み込む。

    Args:
        log_path: JSONLファイルパス
        index: 末尾から何件目（1=最新）

    Returns:
        レコードdict（見つからなければ None）
    """
    if not log_path.exists():
        return None

    records = []
    try:
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    records.append(rec)
                except json.JSONDecodeError:
                    # 壊れ行はスキップ
                    continue
    except Exception as e:
        print(f"Error reading {log_path}: {e}", file=sys.stderr)
        return None

    if len(records) < index:
        print(f"Not enough records in {log_path} (found {len(records)}, requested index {index})", file=sys.stderr)
        return None

    # 末尾から index 番目（records は時系列順と仮定）
    target_rec = records[-index]

    # dictの中に last があればそれを採用（なければ行自体）
    if isinstance(target_rec, dict) and "last" in target_rec:
        return target_rec["last"]

    return target_rec


def extract_params(rec: dict) -> dict:
    """
    レコードから実行パラメータを抽出する。

    Args:
        rec: レコードdict

    Returns:
        パラメータdict: {symbol, profiles, dry, close_now}
    """
    symbol = rec.get("symbol")
    if not symbol:
        raise ValueError("symbol is required but not found in record")

    # profiles を正規化（カンマ区切り文字列を分割・平坦化）
    profiles_raw = rec.get("profiles", [])
    profiles = _normalize_profiles(profiles_raw)

    # dry と close_now は履歴に保存されていない可能性があるため、デフォルト値を使用
    # 必要に応じて rec から取得を試みる
    dry = rec.get("dry", 0)
    if not isinstance(dry, (int, bool)):
        dry = 0

    close_now = rec.get("close_now", 1)
    if not isinstance(close_now, (int, bool)):
        close_now = 1

    return {
        "symbol": str(symbol),
        "profiles": profiles,
        "dry": int(dry),
        "close_now": int(close_now),
    }


def build_ops_start_command(params: dict, project_root: Path) -> list[str]:
    """
    ops_start.ps1 実行コマンドを構築する。

    Args:
        params: パラメータdict
        project_root: プロジェクトルートパス

    Returns:
        コマンドリスト
    """
    ops_start_script = project_root / "tools" / "ops_start.ps1"

    cmd = [
        "pwsh",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ops_start_script),
        "-Symbol",
        params["symbol"],
        "-Dry",
        str(params["dry"]),
        "-CloseNow",
        str(params["close_now"]),
    ]

    # profiles があれば追加
    if params["profiles"]:
        if len(params["profiles"]) == 1:
            cmd.extend(["-Profiles", params["profiles"][0]])
        else:
            cmd.extend(["-Profiles", ",".join(params["profiles"])])

    return cmd


def _load_latest_wfo_inputs() -> Optional[dict[str, Any]]:
    """
    WFO安定性評価に必要な入力を最新の成果物から集める。
    - metrics_wfo.json（train/test統計）
    - logs/retrain/report_*.json（wfo指標入り）
    """
    roots = [Path("backtests"), Path("logs") / "backtest"]
    metrics_candidates: list[Path] = []
    for r in roots:
        if r.exists():
            metrics_candidates.extend(r.rglob("metrics_wfo.json"))
    if not metrics_candidates:
        return None
    metrics_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    metrics_path = metrics_candidates[0]

    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None

    report_dir = Path("logs") / "retrain"
    report_candidates = list(report_dir.glob("report_*.json")) if report_dir.exists() else []
    report_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    report_json = None
    if report_candidates:
        try:
            report_json = json.loads(report_candidates[0].read_text(encoding="utf-8", errors="replace"))
        except Exception:
            report_json = None

    return {
        "metrics_wfo": metrics,
        "report": report_json,
        "paths": {
            "metrics_wfo": str(metrics_path),
            "report": str(report_candidates[0]) if report_candidates else None,
        },
    }


def main() -> int:
    """メイン処理。"""
    parser = argparse.ArgumentParser(description="Ops履歴から条件を復元して再実行")
    parser.add_argument(
        "--log",
        type=str,
        help="対象ログパス（既定：logs/ops 配下の最新 ops_result*.jsonl を探索）",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=1,
        help="末尾から何件目を使うか（既定：1=最新）",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="指定時のみ実際に再実行。未指定ならコマンドを表示するだけ",
    )

    args = parser.parse_args()

    # ログファイルを決定
    if args.log:
        log_path = Path(args.log)
        if not log_path.is_absolute():
            log_path = PROJECT_ROOT / log_path
    else:
        log_path = find_latest_ops_result_jsonl()
        if log_path is None:
            print("Error: No ops_result*.jsonl found in logs/ops", file=sys.stderr)
            return 1

    if not log_path.exists():
        print(f"Error: Log file not found: {log_path}", file=sys.stderr)
        return 1

    # レコードを読み込む
    rec = load_record_from_jsonl(log_path, args.index)
    if rec is None:
        print(f"Error: Failed to load record from {log_path} (index={args.index})", file=sys.stderr)
        return 1

    # パラメータを抽出
    try:
        params = extract_params(rec)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # コマンドを構築
    cmd = build_ops_start_command(params, PROJECT_ROOT)
    cmd_str = " ".join(cmd)

    if not args.run:
        # コマンドを表示するだけ（副作用ゼロ）
        print(cmd_str)
        return 0

    # --run 指定時のみ profiles を保存
    if params["profiles"]:
        try:
            from app.services.profiles_store import save_profiles

            save_profiles(params["profiles"], symbol=params["symbol"])
            print(f"Saved profiles: {params['profiles']}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Failed to save profiles: {e}", file=sys.stderr)
            # プロファイル保存失敗でも続行

    # PROMOTE実行前のWFO安定性チェック
    wfo_inputs = _load_latest_wfo_inputs()
    if not wfo_inputs:
        print("[ops_replay] PROMOTE blocked: wfo_result_missing", flush=True, file=sys.stderr)
        return 2

    # まず保存済み stability_{run_id}.json を最優先で採用
    out = None
    run_id = None
    try:
        m = wfo_inputs.get("metrics_wfo") or {}
        rid = m.get("run_id")
        if rid is not None:
            run_id = str(rid)
    except Exception:
        run_id = None

    if run_id:
        out = _load_saved_wfo_stability(run_id)
        if out is not None:
            print(f"[ops_replay] loaded saved stability run_id={run_id}", flush=True, file=sys.stderr)

    # 保存済みが無い場合のみ従来の再計算にフォールバック
    if out is None:
        out = evaluate_wfo_stability(
            wfo_inputs.get("metrics_wfo"),
            metrics_path=wfo_inputs.get("paths", {}).get("metrics_wfo"),
        )
    if not bool(out.get("stable")):
        print("[ops_replay] PROMOTE blocked: wfo_unstable", flush=True, file=sys.stderr)
        print(f"[ops_replay] details: {out}", flush=True, file=sys.stderr)
        return 2

    # stable True なら、ここから既存の実行を続行

    # 実際に実行
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            check=False,
        )
        return result.returncode
    except Exception as e:
        print(f"Error: Failed to execute ops_start.ps1: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

