# scripts/dryrun_smoke.py
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger

from app.core import logger as app_logger
from app.core.config_loader import load_config
from app.services import circuit_breaker, trade_state
from app.services.execution_stub import ExecutionStub, reset_atr_gate_state
from core.ai.service import AISvc
from core.utils.hashing import hash_features
from core.utils.timeutil import now_jst_iso


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run smoke simulator")
    parser.add_argument("--sim", action="store_true", help="Use synthetic tick stream")
    parser.add_argument("--atr-open", action="store_true", help="Force ATR gate open")
    parser.add_argument("--n", type=int, default=200, help="Number of ticks to simulate")
    parser.add_argument("--dt", type=int, default=50, help="Tick interval in milliseconds")
    parser.add_argument("--base", type=float, default=150.20, help="Base mid price")
    parser.add_argument("--spread", type=float, default=0.5, help="Spread in pips")
    parser.add_argument("--atrpct", type=float, default=0.0005, help="ATR percentage baseline")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--symbol", type=str, default=None, help="Override runtime symbol")
    return parser.parse_args(argv)


def _build_features(base_price: float, spread_pips: float, tick_idx: int, rng: random.Random) -> Dict[str, float]:
    drift = math.sin(tick_idx / 6.0)
    noise = rng.uniform(-0.02, 0.02)
    ema_5 = base_price * (1 + drift * 0.002) + noise
    ema_20 = base_price * (1 - drift * 0.001) - noise
    rsi_14 = 60.0 + drift * 25.0 + rng.uniform(-3, 3)
    atr_14 = abs(drift) * 0.002 + spread_pips * 0.0001
    adx_14 = 22.0 + abs(drift) * 15.0 + rng.uniform(-1.5, 1.5)
    bbp = 0.5 + drift * 0.35 + rng.uniform(-0.05, 0.05)
    vol_chg = drift * 0.08 + rng.uniform(-0.02, 0.02)
    wick_ratio = 0.5 + drift * 0.3 + rng.uniform(-0.05, 0.05)
    return {
        "ema_5": float(ema_5),
        "ema_20": float(ema_20),
        "rsi_14": float(max(0.0, min(100.0, rsi_14))),
        "atr_14": float(abs(atr_14)),
        "adx_14": float(max(5.0, adx_14)),
        "bbp": float(max(0.0, min(1.0, bbp))),
        "vol_chg": float(vol_chg),
        "wick_ratio": float(max(0.0, min(1.0, wick_ratio))),
    }


def _ensure_config_overrides(cfg: dict, args: argparse.Namespace) -> None:
    filters_cfg = cfg.setdefault("filters", {})
    hy = filters_cfg.setdefault("atr_hysteresis", {})
    if args.atr_open:
        filters_cfg["min_atr_pct"] = 0.0
        hy["enable_min_pct"] = 0.0
        hy["disable_min_pct"] = 0.0


def prepare_state(cfg: dict, args: argparse.Namespace) -> ExecutionStub:
    runtime_cfg = cfg.get("runtime", {})
    entry_cfg = cfg.get("entry", {})
    prob_threshold = float(entry_cfg.get("prob_threshold", entry_cfg.get("threshold_buy", 0.60)))
    trade_state.update(
        trading_enabled=True,
        threshold_buy=float(entry_cfg.get("threshold_buy", prob_threshold)),
        threshold_sell=float(entry_cfg.get("threshold_sell", prob_threshold)),
        prob_threshold=prob_threshold,
        side_bias=str(entry_cfg.get("side_bias", "auto") or "auto"),
    )

    cb_cfg = cfg.get("circuit_breaker", {}) if isinstance(cfg, dict) else {}
    cb = circuit_breaker.CircuitBreaker(
        max_consecutive_losses=int(cb_cfg.get("max_consecutive_losses", cfg.get("risk", {}).get("max_consecutive_losses", 5))),
        daily_loss_limit_jpy=float(cb_cfg.get("daily_loss_limit_jpy", 0.0)),
        cooldown_min=int(cb_cfg.get("cooldown_min", 30)),
    )
    ai = AISvc(threshold=prob_threshold)
    try:
        reset_atr_gate_state()
    except Exception:
        pass
    return ExecutionStub(cb=cb, ai=ai)


def main(argv: list[str]) -> None:
    app_logger.setup()
    args = parse_args(argv)
    if not args.sim:
        args.sim = True  # default to simulation for smoke test

    rng = random.Random(args.seed)
    cfg = load_config()
    _ensure_config_overrides(cfg, args)

    import core.config as core_config

    core_config.cfg = cfg
    stub = prepare_state(cfg, args)

    runtime_cfg = cfg.get("runtime", {})
    symbol = args.symbol or runtime_cfg.get("symbol", "USDJPY")

    spread_limit = float(runtime_cfg.get("spread_limit_pips", runtime_cfg.get("spread_limit", 1.5)))
    max_positions = int(runtime_cfg.get("max_positions", 1))

    trail_logged = False

    for idx in range(args.n):
        features = _build_features(args.base, args.spread, idx, rng)
        runtime_payload = {
            "spread_pips": float(args.spread),
            "spread_limit_pips": spread_limit,
            "max_positions": max_positions,
            "open_positions": 0,
            "ai_threshold": stub.ai.threshold,
            "min_atr_pct": cfg.get("filters", {}).get("min_atr_pct", 0.0),
            "filters": cfg.get("filters", {}),
        }
        result = stub.on_tick(symbol, features, runtime_payload)
        if not trail_logged:
            logger.info("[TRAIL][DRYRUN] smoke trail ping features_hash={}", hash_features(features))
            trail_logged = True
        if args.dt > 0:
            time.sleep(min(args.dt / 1000.0, 0.1))

    logger.info(
        "[SMOKE] completed n={} dt_ms={} symbol={} time={}",
        args.n,
        args.dt,
        symbol,
        now_jst_iso(),
    )


if __name__ == "__main__":
    main(sys.argv[1:])
