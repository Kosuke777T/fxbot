# scripts/live_runner.py
from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any, Dict

# --- プロジェクトルート(fxbot/)を sys.path に追加 ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ai_service import AISvc
from app.services.trade_service import execute_decision
from app.core.config_loader import load_config

def main() -> None:
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
