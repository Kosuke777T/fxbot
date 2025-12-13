# tools/mt5_smoke.py
"""
MT5 接続・テスト発注のスモークテスト（CLI版）

Usage:
    python -m tools.mt5_smoke [--symbol SYMBOL] [--lot LOT] [--close-now 0|1] [--dry 0|1]

Examples:
    # デフォルト設定で実行（USDJPY-, 0.01 lot, 即クローズ）
    python -m tools.mt5_smoke

    # ドライラン（実際の発注なし）
    python -m tools.mt5_smoke --dry 1

    # 発注のみ（クローズしない）
    python -m tools.mt5_smoke --close-now 0

    # カスタムシンボルとロット
    python -m tools.mt5_smoke --symbol EURUSD- --lot 0.02
"""
from __future__ import annotations

import sys
import logging

# 標準loggingもstderrへ（app.services を import する前に設定）
logging.basicConfig(stream=sys.stderr)

# loguruもstderrへ（stdout禁止、app.services を import する前に設定）
try:
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="INFO")
except Exception:
    pass

import argparse
import json
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.mt5_selftest import mt5_smoke


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MT5 接続・テスト発注のスモークテスト",
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
        "--lot",
        type=float,
        default=0.01,
        help="テスト発注のロット（デフォルト: 0.01）",
    )
    parser.add_argument(
        "--close-now",
        type=int,
        default=1,
        choices=[0, 1],
        help="発注後に即座にクローズするか（0=しない, 1=する、デフォルト: 1）",
    )
    parser.add_argument(
        "--dry",
        type=int,
        default=0,
        choices=[0, 1],
        help="ドライラン（実際の発注を行わない、デフォルト: 0）",
    )

    args = parser.parse_args()

    # 結果と exit code を保持する変数
    result = None
    exit_code = 1

    try:
        # services層の mt5_smoke を呼び出す
        result = mt5_smoke(
            symbol=args.symbol,
            lot=args.lot,
            close_now=bool(args.close_now),
            dry=bool(args.dry),
        )
        # 成功時は 0、失敗時は 1 を返す
        exit_code = 0 if result.get("ok", False) else 1
    except Exception as e:
        # 例外時も安全なdictを返す
        result = {
            "ok": False,
            "step": "exception",
            "details": {},
            "error": {
                "code": "UNEXPECTED_ERROR",
                "message": str(e),
            },
        }
        exit_code = 1
    finally:
        # 必ず最後に1行で結果dictをJSON出力（stdout への出力はこの1回だけ）
        if result is not None:
            print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), default=str))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

