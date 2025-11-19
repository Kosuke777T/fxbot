import argparse

from app.services.trailing import AtrTrailer, TrailConfig, TrailState


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--side", choices=["BUY", "SELL"], default="BUY")
    parser.add_argument("--entry", type=float, required=True)
    parser.add_argument(
        "--atr",
        type=float,
        required=True,
        help="ATR in price units, e.g., 0.12 for 12 pips on USDJPY",
    )
    parser.add_argument("--pip", type=float, default=0.01, help="pip size, USDJPY=0.01")
    parser.add_argument("--point", type=float, default=0.001, help="point size, USDJPY=0.001")
    parser.add_argument("--activate", type=float, default=0.5)
    parser.add_argument("--step", type=float, default=0.25)
    parser.add_argument("--lockbe", type=float, default=0.3)
    parser.add_argument("--floor", type=float, default=5.0)
    args = parser.parse_args()

    cfg = TrailConfig(
        pip_size=args.pip,
        point=args.point,
        atr=args.atr,
        activate_mult=args.activate,
        step_mult=args.step,
        lock_be_mult=args.lockbe,
        hard_floor_pips=args.floor,
        only_in_profit=True,
        max_layers=20,
    )
    state = TrailState(side=args.side, entry=args.entry)
    trailer = AtrTrailer(cfg, state)

    steps = []
    for i in range(0, 31):
        delta = cfg.atr * 0.1 * i
        if args.side == "BUY":
            steps.append(args.entry + delta)
        else:
            steps.append(args.entry - delta)

    print(f"# side={args.side} entry={args.entry} atr={args.atr}")
    print("# price, activated, be_locked, layers, current_sl, new_sl")
    for px in steps:
        new_sl = trailer.suggest_sl(px)
        print(f"{px:.5f}, {state.activated}, {state.be_locked}, {state.layers}, {state.current_sl}, {new_sl}")


if __name__ == "__main__":
    main()
