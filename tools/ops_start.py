# tools/ops_start.py
"""
運用起動判定ツール

mt5_smoke の出力JSONを読み取って退出コードを統一する。

退出コード:
  0 : 起動OK（ok=True かつ step=="done"）
  10: 市場待ち（error.code=="MARKET_CLOSED"）
  20: 設定不備/取引不可（error.code=="TRADE_DISABLED"）
  30: 異常（上記以外の失敗、JSON破損、例外など）

Usage:
    python -m tools.ops_start [--symbol SYMBOL] [--dry 0|1] [--close-now 0|1]

Examples:
    # 通常起動
    python -m tools.ops_start --symbol USDJPY- --dry 0 --close-now 1

    # ドライラン
    python -m tools.ops_start --symbol USDJPY- --dry 1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="運用起動判定ツール（mt5_smoke の出力JSONを読み取って退出コードを統一）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="USDJPY-",
        help="テスト対象のシンボル（デフォルト: USDJPY-）",
    )
    parser.add_argument(
        "--dry",
        type=int,
        default=0,
        choices=[0, 1],
        help="ドライラン（実際の発注を行わない、デフォルト: 0）",
    )
    parser.add_argument(
        "--close-now",
        type=int,
        default=1,
        choices=[0, 1],
        help="発注後に即座にクローズするか（0=しない, 1=する、デフォルト: 1）",
    )

    args = parser.parse_args()

    # mt5_smoke を実行
    cmd = [
        sys.executable,
        "-m",
        "tools.mt5_smoke",
        "--symbol",
        args.symbol,
        "--dry",
        str(args.dry),
        "--close-now",
        str(args.close_now),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,  # 退出コードは判定に使うので check=False
        )

        # stdout を JSON として解析
        try:
            smoke_output = json.loads(result.stdout.strip())
        except (json.JSONDecodeError, ValueError) as e:
            # JSON破損の場合は rc=30
            output = {
                "status": "ERROR",
                "rc": 30,
                "smoke": None,
                "error": {
                    "code": "JSON_PARSE_FAILED",
                    "message": f"mt5_smoke の出力JSONを解析できませんでした: {e}",
                    "detail": {
                        "stdout": result.stdout[:200] if result.stdout else "(空)",
                        "stderr": result.stderr[:200] if result.stderr else "(空)",
                    },
                },
            }
            print(json.dumps(output, ensure_ascii=True, separators=(",", ":"), default=str))
            return 30

        # 判定ロジック
        ok = smoke_output.get("ok", False)
        step = smoke_output.get("step", "")
        error = smoke_output.get("error")
        error_code = error.get("code") if error else None

        # 退出コードを決定
        if ok and step == "done":
            rc = 0
            status = "OK"
        elif error_code == "MARKET_CLOSED":
            rc = 10
            status = "MARKET_CLOSED"
        elif error_code == "TRADE_DISABLED":
            rc = 20
            status = "TRADE_DISABLED"
        else:
            rc = 30
            status = "ERROR"

        # 出力JSON
        output: Dict[str, Any] = {
            "status": status,
            "rc": rc,
            "smoke": smoke_output,
        }

        print(json.dumps(output, ensure_ascii=True, separators=(",", ":"), default=str))
        return rc

    except Exception as e:
        # 予期しない例外の場合は rc=30
        output = {
            "status": "ERROR",
            "rc": 30,
            "smoke": None,
            "error": {
                "code": "UNEXPECTED_EXCEPTION",
                "message": str(e),
                "detail": {},
            },
        }
        print(json.dumps(output, ensure_ascii=True, separators=(",", ":"), default=str))
        return 30


if __name__ == "__main__":
    raise SystemExit(main())

