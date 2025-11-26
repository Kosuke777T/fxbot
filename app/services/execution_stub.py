from __future__ import annotations

import json
import os
import re
import statistics
from collections import deque, defaultdict
from datetime import datetime
from dataclasses import asdict, dataclass
from typing import Any, DefaultDict, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
from pathlib import Path

from loguru import logger

from app.core import market, mt5_client
from app.core.mt5_client import MT5Client
from app.core.strategy_profile import get_profile
from core.risk import LotSizingResult
from app.core.config_loader import load_config
from app.services import circuit_breaker, trade_service, trade_state
from app.services.orderbook_stub import orderbook
from app.services.trailing import AtrTrailer, TrailConfig, TrailState
from app.services.trailing_hook import apply_trailing_update
from app.services.trade_service import TradeService
from core import position_guard
from core.ai.service import AISvc, ProbOut
from core.metrics import METRICS
from core.utils.timeutil import now_jst_iso
from app.services.event_store import EVENT_STORE
from app.services.metrics import publish_metrics

# プロジェクトルート = app/services/ から 2 つ上
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

LOG_DIR = _PROJECT_ROOT / "logs" / "decisions"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_ATR_MED: deque[float] = deque(maxlen=128)
_ATR_LAST_PASS: bool = False
_ATR_LAST_REF: Optional[float] = None
_ATR_LAST_ENABLE: Optional[float] = None
_ATR_LAST_DISABLE: Optional[float] = None

# Trailing state store shared across dryrun/production per symbol
runtime_trail_states: DefaultDict[str, Dict[str, Any]] = defaultdict(dict)


def _load_runtime_threshold(default: float = 0.5) -> float:
    try:
        meta_path = _PROJECT_ROOT / "models" / "active_model.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            threshold = meta.get("best_threshold")
            if isinstance(threshold, (int, float)) and 0.0 < threshold < 1.0:
                print(f"[exec] using best_threshold from active_model.json: {threshold}")
                return float(threshold)
    except Exception as exc:
        print(f"[exec][warn] failed to load best_threshold: {exc}")
    print(f"[exec] using default threshold: {default}")
    return float(default)


BEST_THRESHOLD = _load_runtime_threshold(0.5)
print(f"[exec] active BEST_THRESHOLD={BEST_THRESHOLD}", flush=True)

def reset_atr_gate_state() -> None:
    """???/????????ATR????????????"""
    global _ATR_MED, _ATR_LAST_PASS
    _ATR_MED.clear()
    _ATR_LAST_PASS = False


def _atr_gate_ok(atr_pct_now: float, runtime_cfg: Dict[str, Any]) -> bool:
    """Hysteresis-enabled ATR gate to avoid rapid flip-flops around thresholds."""
    global _ATR_LAST_PASS, _ATR_LAST_REF, _ATR_LAST_ENABLE, _ATR_LAST_DISABLE

    filters_cfg: Dict[str, Any] = {}
    if isinstance(runtime_cfg, dict):
        filters_cfg = (runtime_cfg.get("filters") or {})

    if not filters_cfg:
        try:
            cfg = load_config()
            filters_cfg = cfg.get("filters", {})
        except Exception:
            filters_cfg = {}

    hy = (filters_cfg.get("atr_hysteresis") or {}) if isinstance(filters_cfg, dict) else {}

    default_min = 0.00055
    if isinstance(runtime_cfg, dict):
        default_min = float(runtime_cfg.get("min_atr_pct", default_min))

    en = float(hy.get("enable_min_pct", default_min))
    de = float(hy.get("disable_min_pct", min(en, 0.00045)))
    _ATR_LAST_ENABLE = en
    _ATR_LAST_DISABLE = de
    lb = int(hy.get("lookback", 12)) or 1
    if lb <= 0:
        lb = 1

    _ATR_MED.append(float(atr_pct_now))
    window = list(_ATR_MED)[-lb:] or [atr_pct_now]
    try:
        ref = statistics.median(window)
    except Exception:
        ref = float(window[-1])
    _ATR_LAST_REF = ref

    if _ATR_LAST_PASS:
        if ref < de:
            _ATR_LAST_PASS = False
        return _ATR_LAST_PASS or ref >= de
    else:
        if ref >= en:
            _ATR_LAST_PASS = True
        return _ATR_LAST_PASS or ref >= en


def _tick_to_dict(tick: Any) -> Optional[Dict[str, float]]:
    if tick is None:
        return None

    if isinstance(tick, dict):
        bid = tick.get("bid")
        ask = tick.get("ask")
    else:
        try:
            bid, ask = tick
        except (TypeError, ValueError):
            return None
    try:
        bid_f = float(bid) if bid is not None else 0.0
        ask_f = float(ask) if ask is not None else 0.0
    except (TypeError, ValueError):
        return None
    return {"bid": bid_f, "ask": ask_f, "mid": (bid_f + ask_f) / 2.0}

def _pip_size_for(symbol: str) -> float:
    return 0.01 if symbol.endswith("JPY") else 0.0001

def _point_for(symbol: str) -> float:
    return 0.001 if symbol.endswith("JPY") else 0.0001

def _mid_price(tick_dict: Optional[Dict[str, float]]) -> Optional[float]:
    if tick_dict is None:
        return None
    return tick_dict.get("mid")

def _current_price_for_side(tick_dict: Optional[Dict[str, float]], side: str, price_source: str) -> Optional[float]:
    if tick_dict is None:
        return None
    ps = (price_source or "mid").lower()
    if ps == "bid":
        return tick_dict.get("bid") if side == "BUY" else tick_dict.get("ask")
    if ps == "ask":
        return tick_dict.get("ask") if side == "BUY" else tick_dict.get("bid")
    return tick_dict.get("mid")

def _register_trailing_state(symbol: str, signal: Dict[str, Any], tick_dict: Optional[Dict[str, float]]) -> None:
    xp = signal.get("exit_plan") or {}
    if xp.get("mode") != "atr":
        runtime_trail_states.pop(symbol, None)
        return

    trailing = xp.get("trailing") or {}
    if not trailing.get("enabled", True):
        runtime_trail_states.pop(symbol, None)
        return

    atr_val = float(xp.get("atr") or 0.0)
    if atr_val <= 0.0:
        return

    side = signal.get("side")
    if not side:
        return

    pip_size = float(_pip_size_for(symbol))
    point = float(_point_for(symbol))
    price_source = (trailing.get("price_source") or "mid").lower()

    entry_price = signal.get("entry_price")
    if entry_price is None and tick_dict is not None:
        entry_price = _current_price_for_side(tick_dict, side, price_source)
    if entry_price is None:
        entry_price = _mid_price(tick_dict) if tick_dict else None
    if entry_price is None:
        return

    state = {
        "mode": "atr",
        "side": side,
        "symbol": symbol,
        "entry": float(entry_price),
        "atr": atr_val,
        "pip_size": pip_size,
        "point": point,
        "activate_atr_mult": float(trailing.get("activate_atr_mult", 0.5)),
        "step_atr_mult": float(trailing.get("step_atr_mult", 0.25)),
        "lock_be_atr_mult": float(trailing.get("lock_be_atr_mult", 0.3)),
        "hard_floor_pips": float(trailing.get("hard_floor_pips", 5.0)),
        "only_in_profit": bool(trailing.get("only_in_profit", True)),
        "max_layers": int(trailing.get("max_layers", 20)),
        "price_source": price_source,
        "activated": False,
        "be_locked": False,
        "layers": 0,
        "current_sl": None,
    }

    trail = runtime_trail_states.setdefault(symbol, {})
    trail.clear()
    trail.update(state)
    signal["trail_state"] = {
        "activated": False,
        "be_locked": False,
        "layers": 0,
        "current_sl": None,
        "atr": atr_val,
        "activate_atr_mult": state["activate_atr_mult"],
        "step_atr_mult": state["step_atr_mult"],
        "lock_be_atr_mult": state["lock_be_atr_mult"],
        "hard_floor_pips": state["hard_floor_pips"],
        "price_source": price_source,
        "max_layers": state["max_layers"],
        "only_in_profit": state["only_in_profit"],
        "side": side,
        "symbol": symbol,
        "entry": float(entry_price),
    }
    publish_metrics({
        "trail_activated": False,
        "trail_be_locked": False,
        "trail_layers":    0,
        "trail_current_sl": None,
    })
    signal["entry_price"] = float(entry_price)

def _update_trailing_state(symbol: str, tick_dict: Optional[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    if tick_dict is None:
        return None

    state = runtime_trail_states.setdefault(symbol, {})
    if not state or state.get("mode") != "atr":
        return None

    side = state.get("side")
    entry = state.get("entry")
    atr_val = float(state.get("atr") or 0.0)
    if not side or entry is None or atr_val <= 0.0:
        return None

    price_source = (state.get("price_source") or "mid").lower()
    current_price = _current_price_for_side(tick_dict, side, price_source)
    if current_price is None:
        return None

    cfg = TrailConfig(
        pip_size=float(state.get("pip_size", _pip_size_for(symbol))),
        point=float(state.get("point", _point_for(symbol))),
        atr=atr_val,
        activate_mult=float(state.get("activate_atr_mult", 0.5)),
        step_mult=float(state.get("step_atr_mult", 0.25)),
        lock_be_mult=float(state.get("lock_be_atr_mult", 0.3)),
        hard_floor_pips=float(state.get("hard_floor_pips", 5.0)),
        only_in_profit=bool(state.get("only_in_profit", True)),
        max_layers=int(state.get("max_layers", 20)),
    )
    trail_state = TrailState(
        side=side,
        entry=float(entry),
        activated=bool(state.get("activated", False)),
        be_locked=bool(state.get("be_locked", False)),
        layers=int(state.get("layers", 0)),
        current_sl=state.get("current_sl"),
    )

    trailer = AtrTrailer(cfg, trail_state)
    new_sl = trailer.suggest_sl(float(current_price))

    state.update(
        {
            "activated": trail_state.activated,
            "be_locked": trail_state.be_locked,
            "layers": trail_state.layers,
            "current_sl": trail_state.current_sl,
        }
    )
    runtime_trail_states[symbol] = state

    if new_sl is None:
        return None

    return {
        "new_sl": new_sl,
        "price": current_price,
        "state": {
            "activated": trail_state.activated,
            "be_locked": trail_state.be_locked,
            "layers": trail_state.layers,
            "current_sl": trail_state.current_sl,
            "price_source": price_source,
            "atr": atr_val,
            "max_layers": int(state.get("max_layers", 20)),
            "only_in_profit": bool(state.get("only_in_profit", True)),
            "side": side,
            "symbol": state.get("symbol", symbol),
        },
    }

def _session_hour_allowed() -> bool:
    """
    config.session.allow_hours_jst ??????????????????????
    ????????/???/???????????
    """
    try:
        from core.config import cfg as _cfg
    except Exception:
        _cfg = {}

    session_cfg = {}
    if isinstance(_cfg, dict):
        raw = _cfg.get("session")
        session_cfg = raw if isinstance(raw, dict) else {}

    allow = session_cfg.get("allow_hours_jst", [])
    if not isinstance(allow, (list, tuple, set)) or len(allow) == 0:
        return True

    try:
        import pytz
        from datetime import datetime
        jst = pytz.timezone("Asia/Tokyo")
        hour = datetime.now(jst).hour
    except Exception:
        return True

    return hour in set(allow)

def _symbol_to_filename(symbol: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", symbol)
    return safe.strip("_") or "UNKNOWN"


def _write_decision_log(symbol: str, record: Dict[str, Any]) -> None:
    fname = LOG_DIR / f"decisions_{_symbol_to_filename(symbol)}.jsonl"
    with open(fname, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_decision_trace(
    *,
    ts_jst: str,
    symbol: str,
    ai_out: "ProbOut",
    cb_status: Dict[str, Any],
    filters_ctx: Dict[str, Any],
    decision: Dict[str, Any],
    prob_threshold: float,
    calibrator_name: str,
) -> Dict[str, Any]:
    """Assemble a structured trace record for downstream analysis."""
    if isinstance(decision, dict):
        action = str(decision.get("action") or "").upper()
        if action in {"BUY", "SELL", "LONG", "SHORT"}:
            decision_label = "ENTRY"
        else:
            decision_label = str(decision.get("action") or "")
    else:
        decision_label = str(decision)

    trace = {
        "ts_jst": ts_jst,
        "type": "decision",
        "symbol": symbol,
        "filters": filters_ctx,
        "probs": {
            "buy": round(ai_out.p_buy, 6),
            "sell": round(ai_out.p_sell, 6),
            "skip": round(ai_out.p_skip, 6),
        },
        "calibrator": calibrator_name,
        "meta": ai_out.meta,
        "threshold": float(prob_threshold),
        "decision": decision_label,
        "ai": ai_out.model_dump(),
        "cb": cb_status,
        "features_hash": ai_out.features_hash,
        "model": ai_out.model_name,
    }
    if isinstance(decision, dict):
        trace["decision_detail"] = decision

        # --- ロット情報があればトップレベルにもコピー -----------------
        if "lot" in decision:
            trace["lot"] = decision.get("lot")
        if "lot_info" in decision:
            trace["lot_info"] = decision.get("lot_info")

        exit_plan = decision.get("signal", {}).get("exit_plan")
    else:
        exit_plan = None

    trace["exit_plan"] = exit_plan or {"mode": "none"}
    return trace

##
def _collect_features(
    symbol: str,
    base_features: Tuple[str, ...],
    tick: Optional[Tuple[float, float]],
    spread_pips: Optional[float],
    open_positions: int,
) -> Dict[str, float]:
    """
    Live 用の軽量なフィーチャ生成。
    - 学習時の 9 列（ret_1, ret_5, ema_5, ema_20, ema_ratio, rsi_14, atr_14, range, vol_chg）
      を中心に、設定された base_features だけ埋める。
    - 本来は OHLCV の履歴から計算するべきだが、ここでは tick/spread からの簡易版。
    """
    bid, ask = tick if tick else (None, None)
    mid = (float(bid) + float(ask)) / 2 if bid is not None and ask is not None else 0.0
    spr = float(spread_pips) if spread_pips is not None else 0.0

    features: Dict[str, float] = {}
    if not base_features:
        features["bias"] = 1.0
        return features

    # 簡易な値（将来的に core.ai.features と揃えるならここを書き換える）
    ret_1_val = 0.0
    ret_5_val = 0.0
    ema_5_val = mid
    ema_20_val = mid
    if ema_20_val != 0.0:
        ema_ratio_val = ema_5_val / ema_20_val
    else:
        ema_ratio_val = 0.0
    rsi_14_val = 50.0
    atr_14_val = spr
    range_val = spr
    vol_chg_val = float(open_positions)

    for name in base_features:
        # --- モデルの 9 列 ---
        if name == "ret_1":
            features[name] = ret_1_val
        elif name == "ret_5":
            features[name] = ret_5_val
        elif name == "ema_5":
            features[name] = ema_5_val
        elif name == "ema_20":
            features[name] = ema_20_val
        elif name == "ema_ratio":
            features[name] = ema_ratio_val
        elif name == "rsi_14":
            features[name] = rsi_14_val
        elif name == "atr_14":
            features[name] = atr_14_val
        elif name == "range":
            features[name] = range_val
        elif name == "vol_chg":
            features[name] = vol_chg_val

        # --- 旧仕様の互換用（config 側で消してもいいが、残しても無害） ---
        elif name == "adx_14":
            features[name] = 20.0 + min(20.0, spr * 5.0)
        elif name == "bbp":
            features[name] = 0.5 if spr == 0 else max(0.0, min(1.0, spr / 5.0))
        elif name == "wick_ratio":
            features[name] = 0.5

        else:
            features[name] = 0.0

    return features

##

@dataclass
class ExecutionStub:
    """
    ドライラン用の実行スタブ：
    - AI確率（AISvc.predict）を呼び出して意思決定だけ行い、約定はしない
    - サーキットブレーカー（self.cb）発動中は BLOCKED を記録
    """
    cb: circuit_breaker.CircuitBreaker
    ai: AISvc

    def __post_init__(self) -> None:
        try:
            self.ai.threshold = float(BEST_THRESHOLD)
        except Exception:
            pass
        try:
            sell_threshold = max(min(1.0 - BEST_THRESHOLD, 1.0), 0.0)
            trade_state.update(
                prob_threshold=float(BEST_THRESHOLD),
                threshold_buy=float(BEST_THRESHOLD),
                threshold_sell=float(sell_threshold),
            )
        except Exception:
            pass


    def on_tick(
        self,
        symbol: str,
        features: Dict[str, float],
        runtime_cfg: Dict[str, Any],
    ) -> Dict[str, Any]:
        ts = now_jst_iso()

        cb_status = self.cb.status()
        ai_out = self.ai.predict(features)

        tick_dict = _tick_to_dict(runtime_cfg.get("tick"))
        trade_svc = getattr(trade_service, "SERVICE", None)

        spread_limit = float(runtime_cfg.get("spread_limit_pips", 1.5))
        min_adx = float(runtime_cfg.get("min_adx", 15.0))
        min_atr_pct = float(runtime_cfg.get("min_atr_pct", 0.0003))
        disable_adx_gate = bool(runtime_cfg.get("disable_adx_gate", False))
        prob_threshold = float(BEST_THRESHOLD)
        runtime_cfg["prob_threshold"] = prob_threshold
        side_bias = runtime_cfg.get("side_bias")

        raw_spread = runtime_cfg.get("spread_pips", 0.0)
        try:
            cur_spread = float(raw_spread)
        except (TypeError, ValueError):
            cur_spread = 0.0
        cur_spread = round(cur_spread, 5)

        cur_adx = round(float(features.get("adx_14", 0.0)), 5)
        cur_atr_pct = round(float(features.get("atr_14", 0.0)), 8)
        # ロット計算用：価格単位の ATR（特徴量 atr_14 と同じもの）
        atr_for_lot = float(features.get("atr_14", 0.0))

        base_filters: Dict[str, Any] = {
            "spread": cur_spread,
            "spread_limit": spread_limit,
            "adx": cur_adx,
            "min_adx": min_adx,
            "adx_disabled": disable_adx_gate,
            "atr_pct": cur_atr_pct,
            "min_atr_pct": min_atr_pct,
            "prob_threshold": prob_threshold,
            # ログ用に、ロット計算で使う ATR も入れておく
            "atr_for_lot": atr_for_lot,
        }
        if side_bias is not None:
            base_filters["side_bias"] = side_bias

        atr_gate_ok = _atr_gate_ok(cur_atr_pct, runtime_cfg)
        if _ATR_LAST_REF is not None:
            base_filters["atr_ref"] = round(float(_ATR_LAST_REF), 8)
        base_filters["atr_gate_state"] = "open" if _ATR_LAST_PASS else "closed"
        if _ATR_LAST_ENABLE is not None:
            base_filters["atr_enable_min"] = float(_ATR_LAST_ENABLE)
        if _ATR_LAST_DISABLE is not None:
            base_filters["atr_disable_min"] = float(_ATR_LAST_DISABLE)

        grace_active = trade_service.post_fill_grace_active()
        base_filters["post_fill_grace"] = grace_active

        def _emit(decision: Any, filters_ctx: Dict[str, Any], level: str = "info") -> None:
            # decision が str ("SKIP" など) の場合は dict として扱わずに抜ける
            if not isinstance(decision, dict):
                print("decision は dict ではありません:", decision)
                return

            action = decision.get("action")
            reason = decision.get("reason")

            gate_state = filters_ctx.get("atr_gate_state")
            atr_ref = float(filters_ctx.get("atr_ref", filters_ctx.get("atr_pct", 0.0)) or 0.0)
            post_grace = bool(filters_ctx.get("post_fill_grace", False))

            # --- カウンタは KVS から安全に読み出して加算 ---
            cur = METRICS.get()  # dictコピーが返る想定
            ce = int(cur.get("count_entry", 0))
            cs = int(cur.get("count_skip", 0))
            cb = int(cur.get("count_blocked", 0))
            if action == "ENTRY":
                ce += 1
            elif action == "SKIP":
                cs += 1
            elif action == "BLOCKED":
                cb += 1

            # --- まとめて publish（KVS更新＋runtime/metrics.json原子的書き換え） ---
            publish_metrics({
                "last_decision": action,
                "last_reason":   reason,
                "atr_ref":       float(atr_ref),
                "atr_gate_state": gate_state,
                "post_fill_grace": bool(post_grace),
                "spread":          filters_ctx.get("spread"),
                "adx":             filters_ctx.get("adx"),
                "min_adx":         filters_ctx.get("min_adx"),
                "prob_threshold":  filters_ctx.get("prob_threshold"),
                "min_atr_pct":     filters_ctx.get("min_atr_pct"),
                "count_entry":     ce,
                "count_skip":      cs,
                "count_blocked":   cb,
                # ts は publish_metrics 側でも自動付与するが、ここで入れても良い
            })


            trail_signal = decision.get("signal") if isinstance(decision, dict) else None
            if isinstance(trail_signal, dict) and "trail_state" in trail_signal:
                trail_state = trail_signal.get("trail_state") or {}
                new_sl_val = trail_state.get("current_sl")
                trail_side = trail_state.get("side") or trail_signal.get("side") or decision.get("side")
                trail_symbol = trail_state.get("symbol") or trail_signal.get("symbol") or symbol
                ticket = trail_state.get("ticket") if isinstance(trail_state, dict) else None
                if new_sl_val is not None and trail_side and trail_symbol:
                    try:
                        apply_trailing_update(
                            ticket=ticket if isinstance(ticket, int) else None,
                            side=str(trail_side),
                            symbol=str(trail_symbol),
                            new_sl=float(new_sl_val),
                            reason=str(action or "trail"),
                        )
                    except Exception as exc:
                        logger.debug(f"[TRAIL][HOOK][ERR] {exc}")

            trace = _build_decision_trace(
                ts_jst=ts,
                symbol=symbol,
                ai_out=ai_out,
                cb_status=cb_status,
                filters_ctx=filters_ctx,
                decision=decision,
                prob_threshold=prob_threshold,
                calibrator_name=self.ai.calibrator_name,
            )
            trace["runtime"] = runtime_cfg
            _write_decision_log(symbol, trace)

            ai_payload = ai_out.model_dump()
            ai_payload["best_threshold"] = BEST_THRESHOLD
            ai_payload.setdefault("threshold", getattr(self.ai, "threshold", prob_threshold))
            payload = {
                "mode": "dryrun",
                "symbol": symbol,
                "decision": decision.get("action"),
                "reason": decision.get("reason"),
                "ai": ai_payload,
                "filters": filters_ctx,
                "cb": cb_status,
            }
            log = logger.bind(event="dryrun", ts=ts)
            if level == "warning":
                log.warning(payload)
            elif level == "error":
                log.error(payload)
            else:
                log.info(payload)

        trail_info = _update_trailing_state(symbol, tick_dict)
        if trail_info:
            filters_ctx = dict(base_filters)
            filters_ctx["trail_state"] = trail_info["state"]
            filters_ctx["trail_new_sl"] = trail_info["new_sl"]
            filters_ctx["trail_price"] = trail_info["price"]
            publish_metrics({
                "trail_activated": bool(trail_info["state"].get("activated")),
                "trail_be_locked": bool(trail_info["state"].get("be_locked")),
                "trail_layers":    int(trail_info["state"].get("layers") or 0),
                "trail_current_sl": trail_info["state"].get("current_sl"),
            })

            decision_payload = {
                "action": "TRAIL_UPDATE",
                "reason": None,
                "signal": {
                    "trail_state": trail_info["state"],
                    "trail_new_sl": trail_info["new_sl"],
                    "trail_price": trail_info["price"],
                    "side": trail_info["state"].get("side"),
                    "symbol": trail_info["state"].get("symbol", symbol),
                },
            }
            _emit(decision_payload, filters_ctx, level="info")

        if not _session_hour_allowed():
            filters_ctx = dict(base_filters)
            filters_ctx["session"] = "closed"
            decision_payload = {"action": "SKIP", "reason": "session_closed"}
            _emit(decision_payload, filters_ctx, level="info")
            return {"blocked": False, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": decision_payload}

        if not grace_active and cur_spread and cur_spread > spread_limit:
            filters_ctx = dict(base_filters)
            filters_ctx["blocked"] = "spread"
            decision_payload = {"action": "BLOCKED", "reason": "spread"}
            _emit(decision_payload, filters_ctx, level="warning")
            return {"blocked": True, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": None}

        if not grace_active and not disable_adx_gate and cur_adx < min_adx:
            filters_ctx = dict(base_filters)
            filters_ctx["blocked"] = "adx_low"
            decision_payload = {"action": "BLOCKED", "reason": "adx_low"}
            _emit(decision_payload, filters_ctx, level="warning")
            return {"blocked": True, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": None}

        if not grace_active and not atr_gate_ok:
            filters_ctx = dict(base_filters)
            filters_ctx["blocked"] = "atr_low"
            decision_payload = {"action": "BLOCKED", "reason": "atr_low"}
            _emit(decision_payload, filters_ctx, level="warning")
            return {"blocked": True, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": None}

        if not self.cb.can_trade():
            cb_status = self.cb.status()
            filters_ctx = dict(base_filters)
            filters_ctx["blocked"] = "circuit_breaker"
            decision_payload = {"action": "BLOCKED", "reason": cb_status.get("reason", "circuit_breaker")}
            _emit(decision_payload, filters_ctx, level="warning")
            return {"blocked": True, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": None}

        cb_status = self.cb.status()

        if cb_status.get("tripped"):
            filters_ctx = dict(base_filters)
            filters_ctx["blocked"] = "circuit_breaker"
            decision_payload = {"action": "BLOCKED", "reason": cb_status.get("reason", "circuit_breaker")}
            _emit(decision_payload, filters_ctx, level="warning")
            return {"blocked": True, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": None}

        config = load_config()
        entry_cfg = config.get("entry", {}) if isinstance(config, dict) else {}
        edge = float(entry_cfg.get("entry_min_edge", entry_cfg.get("min_edge", 0.0)))
        buy_threshold = prob_threshold
        sell_threshold = max(min(1.0 - prob_threshold, 1.0), 0.0)

        base_filters["threshold_buy"] = buy_threshold
        base_filters["threshold_sell"] = sell_threshold
        base_filters["edge"] = edge

        filters_ctx = dict(base_filters)
        filters_ctx["blocked"] = None

        if side_bias is None:
            side_bias = (entry_cfg.get("side_bias") or "auto").lower()
        else:
            side_bias = str(side_bias).lower()
        filters_ctx["side_bias"] = side_bias

        p_buy = float(ai_out.p_buy)
        p_sell = float(ai_out.p_sell)

        buy_ok = p_buy >= buy_threshold
        sell_ok = p_sell >= sell_threshold

        chosen_side = None
        chosen_prob = 0.0
        other_prob = 0.0

        if buy_ok and (not sell_ok or p_buy >= p_sell):
            chosen_side = "BUY"
            chosen_prob = p_buy
            other_prob = p_sell
        elif sell_ok:
            chosen_side = "SELL"
            chosen_prob = p_sell
            other_prob = p_buy

        if chosen_side is not None and p_buy == p_sell:
            if side_bias == "buy":
                chosen_side = "BUY"
                chosen_prob = p_buy
                other_prob = p_sell
            elif side_bias == "sell":
                chosen_side = "SELL"
                chosen_prob = p_sell
                other_prob = p_buy

        decision_info: Dict[str, Any] = {
            "threshold_buy": buy_threshold,
            "threshold_sell": sell_threshold,
            "edge": edge,
            "prob_buy": p_buy,
            "prob_sell": p_sell,
        }

        if chosen_side is None:
            decision_info.update({"decision": "SKIP", "reason": "ai_threshold"})
            decision_payload = {
                "action": "SKIP",
                "reason": "ai_threshold",
                "ai_meta": ai_out.meta,
                "dec": decision_info,
            }
            _emit(decision_payload, filters_ctx, level="info")
            return {"blocked": False, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": decision_payload}

        if (chosen_prob - other_prob) < edge:
            decision_info.update({"decision": "SKIP", "reason": "ai_low_edge"})
            decision_payload = {
                "action": "SKIP",
                "reason": "ai_low_edge",
                "ai_meta": ai_out.meta,
                "dec": decision_info,
            }
            _emit(decision_payload, filters_ctx, level="info")
            return {"blocked": False, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": decision_payload}

        decision_info.update(
            {
                "decision": "ENTRY",
                "side": chosen_side,
                "prob": chosen_prob,
                "edge_delta": chosen_prob - other_prob,
            }
        )

        if not trade_service.can_open_new_position(symbol):
            blocked_filters = dict(base_filters)
            blocked_filters["blocked"] = "pos_guard"
            decision_payload = {
                "action": "BLOCKED",
                "reason": "pos_guard",
                "ai_meta": ai_out.meta,
                "dec": decision_info,
            }
            _emit(decision_payload, blocked_filters, level="warning")
            position_guard.on_order_rejected_or_canceled(symbol)
            return {"blocked": True, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": None}

        signal = {
            "side": chosen_side,
            "prob": chosen_prob,
            "meta": chosen_side,
            "best_threshold": buy_threshold,
        }

        recent_ohlc = globals().get("get_recent_ohlc")
        ohlc_tail = None
        if callable(recent_ohlc):
            try:
                ohlc_tail = recent_ohlc(symbol, bars=64)
            except Exception:
                ohlc_tail = None

        exit_plan = None
        try:
            exit_plan = trade_service.build_exit_plan(symbol, ohlc_tail)
        except Exception:
            exit_plan = None

        if not exit_plan:
            exit_builder = globals().get("_build_exit_plan")
            decision_exit_builder = globals().get("_build_decision_exit_plan")
            if callable(exit_builder) and ohlc_tail is not None:
                if callable(decision_exit_builder):
                    exit_plan = decision_exit_builder(symbol, ohlc_tail)
                else:
                    exit_plan = exit_builder(symbol, ohlc_tail)

        signal["exit_plan"] = exit_plan or {"mode": "none"}
        # ロット計算用 ATR をシグナルにも載せて Live 側で参照できるようにする
        if atr_for_lot is not None:
            signal["atr_for_lot"] = float(atr_for_lot)

        _register_trailing_state(symbol, signal, tick_dict)

        trade_service.mark_filled_now()
        filters_ctx = dict(base_filters)
        filters_ctx["blocked"] = None
        # --- ロット計算挿入ブロック ----------------------
        profile = get_profile("michibiki_std")

        # ATR は signal 内に格納済み
        atr_val = float(signal.get("atr_for_lot", 0.0))

        try:
            client = MT5Client()
            client.initialize()
            equity = client.get_equity()
            tspec = client.get_tick_spec(symbol)
            tick_size = tspec.tick_size
            tick_value = tspec.tick_value
        except Exception:
            equity = 1_000_000.0
            tick_size = 0.01
            tick_value = 100.0

        lot_result = profile.compute_lot_size_from_atr(
            equity=float(equity),
            atr=float(atr_val),
            tick_size=float(tick_size),
            tick_value=float(tick_value),
        )
        # TradeService 側にもロット計算結果を保持しておく
        if isinstance(trade_svc, TradeService):
            try:
                trade_svc.last_lot_result = lot_result
                if hasattr(trade_svc, "_last_lot_result"):
                    trade_svc._last_lot_result = lot_result  # type: ignore[attr-defined]
            except Exception:
                pass
        # ---------------------------------------------------
        # --- ���b�g���iLotSizingResult�j������� dict ������ -----------------
        lot_info = None
        lot_info_source = None
        if isinstance(trade_svc, TradeService):
            lot_info_source = getattr(trade_svc, "last_lot_result", None)
        if lot_info_source is None:
            lot_info_source = lot_result

        if lot_info_source is not None:
            try:
                if isinstance(lot_info_source, LotSizingResult) and hasattr(lot_info_source, "to_dict"):
                    lot_info = lot_info_source.to_dict()  # type: ignore[attr-defined]
                else:
                    lot_info = asdict(lot_info_source)  # type: ignore[arg-type]
            except Exception as exc:
                print(f"[warn] failed to serialize lot_result: {exc!r}")
                lot_info = None
        decision_payload = {
            "action": "ENTRY",
            "reason": decision_info.get("reason","entry_ok"),
            "ai_meta": ai_out.meta,
            "signal": signal,
            "dec": decision_info,
            "lot": (lot_info.get("lot") if isinstance(lot_info, dict) else None),
            "lot_info": lot_info,
        }
        _emit(decision_payload, filters_ctx, level="info")
        return {"blocked": False, "ai": ai_out, "cb": cb_status, "ts": ts, "decision": decision_payload}


def evaluate_and_log_once() -> None:
    """Dry-run evaluation that mirrors the live decision path."""
    cfg = load_config()
    runtime_cfg = cfg.get("runtime", {})
    ai_cfg = cfg.get("ai", {})
    entry_cfg = cfg.get("entry", {})
    filters_cfg = cfg.get("filters", {})

    best_threshold = float(BEST_THRESHOLD)
    sell_threshold = max(min(1.0 - best_threshold, 1.0), 0.0)

    trade_state.update(
        threshold_buy=best_threshold,
        threshold_sell=sell_threshold,
        prob_threshold=best_threshold,
        side_bias=str(entry_cfg.get("side_bias", "auto") or "auto"),
        trading_enabled=True,  # ★ dryrun 評価では常にトレードONにする
    )
    settings = trade_state.get_settings()

    symbol = runtime_cfg.get("symbol", "USDJPY")
    spread_limit_pips = float(runtime_cfg.get("spread_limit_pips", runtime_cfg.get("spread_limit", 1.5)))
    max_pos = int(runtime_cfg.get("max_positions", 1))
    min_adx = float(filters_cfg.get("adx_min", 15.0))
    disable_adx_gate = bool(filters_cfg.get("adx_disable", False))
    min_atr_pct = float(filters_cfg.get("min_atr_pct", 0.0003))

    if not settings.trading_enabled:
        logger.bind(event="dryrun", ts=now_jst_iso()).info(
            {"mode": "dryrun", "enabled": False, "reason": "trading_disabled"}
        )
        return

    '''
    try:
        from app.core.mt5_client import MT5Client
        client = MT5Client()
        client.initialize()
        client.login_account()
    except Exception:
        # ドライランなのでMT5につながらなくてもOK
        logger.bind(event="dryrun", ts=now_jst_iso()).warning(
            {"mode": "dryrun", "enabled": True, "error": "mt5_init_skipped"}
        )
    '''

    try:
        spr_callable = getattr(market, "spread", None)
        spr = spr_callable(symbol) if callable(spr_callable) else 0.0

        ob_obj = orderbook() if callable(orderbook) else orderbook
        get_maybe = getattr(ob_obj, "get", None)
        ob = get_maybe(symbol) if callable(get_maybe) else None

        open_cnt = 0
        if ob is not None:
            updater = getattr(ob, "update_with_market_and_close_if_hit", None)
            if callable(updater):
                updater(symbol)
            cnt_getter = getattr(ob, "count_open", None)
            if callable(cnt_getter):
                try:
                    open_cnt = int(cnt_getter(symbol))
                except Exception:
                    open_cnt = 0

        tick = market.tick(symbol)
        tick_dict = None
        if tick:
            try:
                bid, ask = tick
                tick_dict = {"bid": float(bid), "ask": float(ask)}
            except (TypeError, ValueError):
                tick_dict = None

        base_features = tuple(ai_cfg.get("features", {}).get("base", []))
        features = _collect_features(symbol, base_features, tick, spr, open_cnt)
        # ★ dryrun 用：ATR を固定値に強制
        features["atr_14"] = 0.02   # 例: 2銭相当

        cb_cfg = cfg.get("circuit_breaker", {}) if isinstance(cfg, dict) else {}
        cb = circuit_breaker.CircuitBreaker(
            max_consecutive_losses=int(cb_cfg.get("max_consecutive_losses", 5)),
            daily_loss_limit_jpy=float(cb_cfg.get("daily_loss_limit_jpy", 0.0)),
            cooldown_min=int(cb_cfg.get("cooldown_min", 30)),
        )
        #
        # --- DryRun 用 AISvc：モデル読み込み失敗を回避する ---
        try:
            ai = AISvc(threshold=best_threshold)
        except Exception:
            print("[exec] AISvc model loading failed → using dummy model for dryrun")

            class DummyProbOut:
                def __init__(self, p_buy: float, p_sell: float, p_skip: float, meta: str = "dummy") -> None:
                    # ExecutionStub や _build_decision_trace から参照される属性だけ持っておけばOK
                    self.p_buy = float(p_buy)
                    self.p_sell = float(p_sell)
                    self.p_skip = float(p_skip)
                    self.meta = meta
                    self.model_name = "dummy"
                    self.calibrator_name = "dummy"
                    self.features_hash = "dummy"

                def model_dump(self) -> dict:
                    # 本物の ProbOut.model_dump() っぽい辞書を返す
                    return {
                        "p_buy": self.p_buy,
                        "p_sell": self.p_sell,
                        "p_skip": self.p_skip,
                        "meta": self.meta,
                        "model_name": self.model_name,
                        "calibrator_name": self.calibrator_name,
                        "features_hash": self.features_hash,
                    }

            class DummyAISvc:
                def __init__(self, threshold: float) -> None:
                    self.threshold = float(threshold)
                    self.calibrator_name = "dummy"
                    self.model_name = "dummy"

                def predict(self, feats: dict) -> "DummyProbOut":
                    # 適当な確率を返すダミーモデル
                    return DummyProbOut(
                        p_buy=0.33,
                        p_sell=0.33,
                        p_skip=0.34,
                        meta="dummy",
                    )

            ai = DummyAISvc(threshold=best_threshold)
        #
        print(f"[exec] AISvc model: {getattr(ai, 'model_name', 'unknown')} (threshold={best_threshold})")
        stub = ExecutionStub(cb=cb, ai=ai)

        runtime_payload = {
            "threshold_buy": best_threshold,
            "threshold_sell": sell_threshold,
            "prob_threshold": best_threshold,
            "spread_limit_pips": spread_limit_pips,
            "max_positions": max_pos,
            "spread_pips": spr,
            "open_positions": open_cnt,
            "ai_threshold": stub.ai.threshold,
            # dryrun 用に ADX ゲートを無効化
            "min_adx": 0.0,
            "disable_adx_gate": True,
            # ★ dryrun 用：ATR が 0 にならないよう最低値を入れる
            "min_atr_pct": 0.0003,   # 任意。0.0002〜0.001 の範囲なら何でも良い。
            #"min_atr_pct": min_atr_pct,
            "tick": tick,
            "side_bias": settings.side_bias,
        }

        result = stub.on_tick(symbol, features, runtime_payload)
        _ = result
    finally:
        try:
            shutdown = getattr(mt5_client, "shutdown", None)
            if callable(shutdown):
                shutdown()
        except Exception:
            pass
