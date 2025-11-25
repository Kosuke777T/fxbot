# scripts/discover_symbols.py

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import MetaTrader5 as mt5


OUTPUT_PATH = Path("configs") / "symbols_mt5.json"


def _guess_pair(name: str) -> Optional[str]:
    """
    シンボル名から通貨ペア6文字 (例: 'USDJPY') を推定する。
    先頭6文字が [A-Z]{6} ならそれを採用。
    USDJPY.r / USDJPYmicro などを想定。
    """
    m = re.match(r"^([A-Z]{6})", name)
    if not m:
        return None
    return m.group(1)


def main() -> None:
    print("=== MT5 initialize ===")
    if not mt5.initialize():
        print("MT5 init failed:", mt5.last_error())
        return

    try:
        account = mt5.account_info()
        print("account:", account)

        print("=== symbols_get() ===")
        symbols = mt5.symbols_get()
        print("total symbols:", len(symbols))

        rows: List[Dict[str, Any]] = []
        # pair ごとに「どのシンボルを優先するか」を決めるための情報
        # pair -> {"symbol": str, "score": int}
        preferred_raw: Dict[str, Dict[str, Any]] = {}

        for s in symbols:
            name = s.name

            # trade_mode == 0 は取引不可のことが多いので除外
            trade_mode = int(getattr(s, "trade_mode", 0))
            if trade_mode == 0:
                continue

            pair = _guess_pair(name)
            if not pair:
                continue

            visible = bool(getattr(s, "visible", False))
            selected = bool(getattr(s, "select", False))
            digits = int(getattr(s, "digits", 0))
            point = float(getattr(s, "point", 0.0))
            description = str(getattr(s, "description", ""))

            row = {
                "name": name,
                "pair": pair,
                "visible": visible,
                "select": selected,
                "trade_mode": trade_mode,
                "digits": digits,
                "point": point,
                "description": description,
            }
            rows.append(row)

            # 優先度スコア: visible + select が高いほど優先
            score = (1 if visible else 0) + (1 if selected else 0)

            prev = preferred_raw.get(pair)
            if prev is None or score > prev["score"]:
                preferred_raw[pair] = {"symbol": name, "score": score}

        preferred = {pair: v["symbol"] for pair, v in preferred_raw.items()}

        data = {
            "broker_server": getattr(account, "server", ""),
            "login": int(getattr(account, "login", 0)),
            "symbols": rows,
            "preferred": preferred,
        }

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        print()
        print(f"written: {OUTPUT_PATH}")
        print(f"pairs discovered: {len(preferred)}")

    finally:
        mt5.shutdown()
        print("=== MT5 shutdown ===")


if __name__ == "__main__":
    main()
