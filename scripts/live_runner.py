# scripts/live_runner.py
from __future__ import annotations

from pathlib import Path
import sys
import time
import argparse
import json
from typing import Any, Dict

# --- プロジェクトルート(fxbot/)を sys.path に追加 ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ai_service import AISvc
from app.services.trade_service import execute_decision
from app.services.mt5_selftest import mt5_smoke
from app.core.config_loader import load_config

def main() -> None:
    parser = argparse.ArgumentParser(
        description="運用起動コマンド（Live runner）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="起動前に MT5 接続・テスト発注のスモークテストを実行",
    )
    parser.add_argument(
        "--smoke-symbol",
        type=str,
        default="USDJPY-",
        help="スモークテストのシンボル（デフォルト: USDJPY-）",
    )
    parser.add_argument(
        "--smoke-lot",
        type=float,
        default=0.01,
        help="スモークテストのロット（デフォルト: 0.01）",
    )
    parser.add_argument(
        "--smoke-dry",
        action="store_true",
        help="スモークテストをドライランで実行（実際の発注なし）",
    )

    args = parser.parse_args()

    # スモークテスト実行（オプション）
    if args.smoke_test:
        print("=== MT5 スモークテスト実行 ===")
        smoke_result = mt5_smoke(
            symbol=args.smoke_symbol,
            lot=args.smoke_lot,
            close_now=True,
            dry=args.smoke_dry,
        )
        print(json.dumps(smoke_result, ensure_ascii=False, indent=2))
        if not smoke_result.get("ok", False):
            print("ERROR: スモークテストが失敗しました。運用を開始できません。")
            sys.exit(1)
        print("=== スモークテスト成功、運用を開始します ===\n")

    cfg = load_config()
    symbol = cfg.get("runtime", {}).get("symbol", "USDJPY")

    ai = AISvc()

    print("=== Live runner started ===")
    while True:
        # → AISvc が自動で MT5 チャートから特徴量を取る前提（既存の get_live_features があるなら置き換える）
        probs = ai.get_live_probs(symbol)
        print("tick → probs:", probs)
        decision = ai.build_decision_from_probs(probs, symbol=symbol)
        print("decision →", decision)
        # ATR は execution_stub 互換の decision の中に入っている
        execute_decision(decision, symbol=symbol)

        time.sleep(1.0)  # 1秒間隔でループ（適宜調整）

if __name__ == "__main__":
    main()
