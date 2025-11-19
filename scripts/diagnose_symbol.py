# scripts/diagnose_symbol.py
import MetaTrader5 as mt5


def main() -> None:
    if not mt5.initialize():
        print("MT5 init failed:", mt5.last_error())
        return
    try:
        # USDJPYで始まる全候補を列挙
        cands = mt5.symbols_get("USDJPY*")
        print("Candidates:", len(cands))
        for s in cands:
            print(f"- {s.name}  (select={s.select}, bid={s.bid}, ask={s.ask}, point={s.point})")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
