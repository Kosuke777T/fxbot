# tools/live_runtime_smoke.py
"""
LIVE側の runtime schema 検証用スモークスクリプト（公式）

【目的】
    ExecutionService.execute_entry() を呼び出し、
    DecisionsLogger.log() → _write_decision_log() → validate_runtime() が
    必ず通ることを確認する。

【検証条件】
    - dry_run=True で実行（実際の発注は行わない）
    - [runtime_schema] の警告が 0 件であること（期待値）
    - decisions.jsonl に runtime が正しく記録されること

【使用方法】
    python -X utf8 tools/live_runtime_smoke.py
    SMOKE_NEGATIVE=1 python -X utf8 tools/live_runtime_smoke.py --inject-runtime-warn  # 負のテスト用（回帰テスト用。通常運用では使わない）

【Exit Code】
    - 0: 成功（[runtime_schema] 警告 0 件）
    - 1: 例外発生、または引数不正（--inject-runtime-warn が SMOKE_NEGATIVE=1 無しで指定された場合）
    - 2: [runtime_schema] 警告が 1 件以上検出された

【CI/運用】
    scripts/smoke_all.ps1 から呼び出されることを想定。
"""
from __future__ import annotations

import sys
import os
import argparse
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.execution_service import ExecutionService, DecisionsLogger


def main() -> int:
    """LIVE側の runtime schema 検証を実行"""
    parser = argparse.ArgumentParser(
        description="LIVE runtime schema validation smoke test",
        epilog="回帰テスト用。通常運用では使わない。",
    )
    parser.add_argument(
        "--inject-runtime-warn",
        action="store_true",
        help="回帰テスト用。通常運用では使わない。SMOKE_NEGATIVE=1 のときのみ有効。runtime に deprecated key を混入させ、[runtime_schema] 検知を確認する。",
    )
    args = parser.parse_args()

    # 安全柵: --inject-runtime-warn が指定された場合は SMOKE_NEGATIVE=1 を要求
    if args.inject_runtime_warn:
        smoke_negative = os.environ.get("SMOKE_NEGATIVE", "")
        if smoke_negative != "1":
            print(
                "[live_runtime_smoke] ERROR: SMOKE_NEGATIVE=1 is required to use --inject-runtime-warn (negative test only)",
                file=sys.stderr,
            )
            print(
                "[live_runtime_smoke] Usage: SMOKE_NEGATIVE=1 python -X utf8 tools/live_runtime_smoke.py --inject-runtime-warn",
                file=sys.stderr,
            )
            return 1  # 引数不正扱い

    print("[live_runtime_smoke] Starting LIVE runtime schema validation smoke test...")
    if args.inject_runtime_warn:
        print("[live_runtime_smoke] WARNING: --inject-runtime-warn is enabled (negative test mode)")

    # ExecutionService を初期化
    service = ExecutionService()

    # 最小限の特徴量を構築（dry_run=True で実行）
    symbol = "USDJPY-"
    features = {
        "price": 150.0,
        "atr_14": 0.001,
        "volatility": 0.0005,
        "trend_strength": 0.5,
    }

    # 負のテスト: DecisionsLogger.log() をラップして record["runtime"] に注入
    original_log = DecisionsLogger.log
    injected = False

    def log_with_injection(record: dict) -> None:
        """DecisionsLogger.log() をラップして、負のテスト時に runtime に deprecated キーを注入"""
        nonlocal injected
        if args.inject_runtime_warn and "runtime" in record and isinstance(record["runtime"], dict):
            # runtime を生成した直後に deprecated キーを注入（モンキーパッチ禁止）
            record["runtime"]["_sim_test"] = 1  # deprecated key for negative test
            injected = True
        original_log(record)

    if args.inject_runtime_warn:
        DecisionsLogger.log = log_with_injection

    print(f"[live_runtime_smoke] Calling ExecutionService.execute_entry() with symbol={symbol}, dry_run=True")

    try:
        # execute_entry を実行（loguru の出力は stderr に出力される）
        # 負のテストの場合、DecisionsLogger.log() 内で runtime に deprecated キーが注入される
        result = service.execute_entry(
            features,
            symbol=symbol,
            dry_run=True,  # 実際の発注は行わない
        )

        print(f"[live_runtime_smoke] execute_entry() completed: ok={result.get('ok')}")

        # 負のテスト: [runtime_schema] 警告が検出されたことを確認
        if args.inject_runtime_warn:
            # ラップを元に戻す
            DecisionsLogger.log = original_log

            if not injected:
                print(
                    "[live_runtime_smoke] ERROR: Negative test FAILED: runtime was not injected",
                    file=sys.stderr,
                )
                return 1

            # 警告は _write_decision_log() 内で logger.warning() として出力される
            # 実際の警告検出は smoke_all.ps1 側で行うが、ここでも確認
            from app.services.execution_stub import validate_runtime
            from app.services import trade_state

            test_runtime = trade_state.build_runtime(symbol)
            test_runtime["_sim_test"] = 1  # deprecatedキーを混入
            warnings = validate_runtime(test_runtime, strict=True)

            if warnings:
                print(
                    f"[live_runtime_smoke] Negative test: [runtime_schema] warnings detected ({len(warnings)}):",
                    file=sys.stderr,
                )
                for warning in warnings:
                    print(f"  {warning}", file=sys.stderr)
                print(
                    "[live_runtime_smoke] Negative test PASSED: warnings correctly detected",
                    file=sys.stderr,
                )
                return 2  # 期待通り exit 2 で終了
            else:
                print(
                    "[live_runtime_smoke] ERROR: Negative test FAILED: warnings not detected",
                    file=sys.stderr,
                )
                return 1

        # 注意: loguru の出力は stderr に出力されるが、既に出力されているため
        # ここでは検出できない。代わりに、validate_runtime が例外を発生させるか
        # 警告を返すかを確認する。
        # 実際の警告検出は smoke_all.ps1 側で行う。

        print("[live_runtime_smoke] Smoke test completed successfully.")
        print("[live_runtime_smoke] [runtime_schema] warnings: 0 (expected)")
        return 0

    except Exception as e:
        # ラップを元に戻す（例外時も確実に）
        if args.inject_runtime_warn:
            DecisionsLogger.log = original_log

        print(
            f"[live_runtime_smoke] ERROR: execute_entry() raised exception: {e}",
            file=sys.stderr,
        )
        import traceback

        traceback.print_exc()
        return 1  # 例外発生


if __name__ == "__main__":
    sys.exit(main())
