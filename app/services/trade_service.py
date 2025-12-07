from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
import logging

from app.core import mt5_client
from app.core.config_loader import load_config
from app.services import trade_state
from app.services.circuit_breaker import CircuitBreaker
from app.services.event_store import EVENT_STORE
from app.services.filter_service import evaluate_entry
from core.config import cfg
from core.indicators import atr as _atr
from core.position_guard import PositionGuard
from core.utils.clock import now_jst

from app.core.mt5_client import MT5Client, TickSpec
from app.core.strategy_profile import StrategyProfile, get_profile
from core.risk import LotSizingResult, compute_lot_scaler_from_backtest
#from app.core.risk import LotSizingResult


@dataclass
class LotRule:
    base_equity_per_0p01: int = 10_000
    min_lot: float = 0.01
    max_lot: float = 1.00
    step: float = 0.01


def round_to_step(x: float, step: float) -> float:
    return (int(x / step)) * step


def calc_lot(equity: float, rule: LotRule = LotRule()) -> float:
    raw = (equity / rule.base_equity_per_0p01) * 0.01
    lot = max(rule.min_lot, min(rule.max_lot, round_to_step(raw, rule.step)))
    return float(f"{lot:.2f}")


def snapshot_account() -> Optional[dict]:
    if not mt5_client.initialize():
        return None
    try:
        return mt5_client.get_account_info()
    finally:
        mt5_client.shutdown()


class TradeService:
    """Facade that coordinates guards, circuit breaker, and decision helpers."""

    def __init__(
        self,
        mt5_client: MT5Client | None = None,
        profile: StrategyProfile | None = None,
    ) -> None:
        self._mt5 = mt5_client
        self._profile = profile or get_profile()
        self._last_lot_result: LotSizingResult | None = None
        # 直近の LotSizingResult を公開用に保持
        self.last_lot_result: LotSizingResult | None = None
        self._logger = logging.getLogger(__name__)
        self.pos_guard = PositionGuard()
        self.cb = CircuitBreaker()
        self._reconcile_interval = 15
        self._desync_fix = True
        self._last_reconcile = 0.0

        # --- バックテスト由来ロットスケーラー --------------------------
        # 月次リターン / DD の安定度に応じてロットを倍率調整する係数。
        # compute_lot_scaler_from_backtest(...) で計算し、ここでキャッシュする。
        self._lot_scaler: float = 1.0
        self._lot_scaler_last_updated: float = 0.0
        # 何秒ごとに CSV を再読込するか（ここでは 1時間に1回）
        self._lot_scaler_ttl_sec: float = 3600.0

        self.state = trade_state.get_runtime()
        self.reload()

    def _get_lot_scaler(self) -> float:
        """
        バックテスト（月次リターン / 最大DD）から計算したロット補正係数を返す。

        - monthly_returns.csv がなければ 1.0 にフォールバック
        - 計算結果がおかしければ 1.0 にフォールバック
        - 高頻度で CSV を読まないよう、一定時間キャッシュする
        """
        now = time.time()
        # キャッシュが有効ならそのまま返す
        if (
            self._lot_scaler_last_updated > 0.0
            and (now - self._lot_scaler_last_updated) < self._lot_scaler_ttl_sec
        ):
            return self._lot_scaler

        path = self._profile.monthly_returns_path
        scaler = 1.0

        try:
            # ここでは core.risk.compute_lot_scaler_from_backtest のインターフェースを
            #   compute_lot_scaler_from_backtest(
            #       monthly_returns_csv: str,
            #       target_monthly_return: float,
            #       max_monthly_dd: float,
            #   ) -> float
            # という前提で呼び出しています。
            scaler = float(
                compute_lot_scaler_from_backtest(
                    monthly_returns_csv=str(path),
                    target_monthly_return=self._profile.target_monthly_return,
                    max_monthly_dd=self._profile.max_monthly_dd,
                )
            )
            # NaN や 0 以下は無効扱い
            if not (scaler > 0.0):
                raise ValueError(f"invalid scaler <= 0: {scaler}")
        except Exception as e:
            self._logger.warning(
                "compute_lot_scaler_from_backtest 失敗のため scaler=1.0 で継続: %s",
                e,
            )
            scaler = 1.0

        self._lot_scaler = scaler
        self._lot_scaler_last_updated = now

        self._logger.info(
            "Backtest lot scaler 更新: path=%s target_ret=%.3f max_dd=%.3f -> scaler=%.3f",
            path,
            self._profile.target_monthly_return,
            self._profile.max_monthly_dd,
            scaler,
        )
        return scaler

    def _compute_lot_for_entry(self, symbol: str, atr: float) -> LotSizingResult:
        """
        1トレードあたりのロット数を、現在の equity / ATR / tick 情報から計算する。

        atr: エントリー直前の足で計算した ATR 値（価格単位）
        """
        # --- ATR が変なときは default_lot にフォールバック ----------------
        if atr is None or atr <= 0:
            default_lot = getattr(self._profile, "default_lot", None)
            if default_lot is None:
                default_lot = float(self._config.trade.default_lot)

            self._logger.warning(
                "ATR が無効 (atr=%s) のため、default_lot=%.2f を使用します。",
                atr,
                default_lot,
            )
            res = LotSizingResult(
                lot=default_lot,
                capped_by_max_risk=False,
                effective_risk_pct=None,
                note="fallback_default_lot_due_to_invalid_atr",
            )
            self._last_lot_result = res
            self.last_lot_result = res
            return res

        # --- ベースロットを StrategyProfile から計算 -----------------------
        equity = self._mt5.get_equity()
        tick_spec: TickSpec = self._mt5.get_tick_spec(symbol)

        result = self._profile.compute_lot_size_from_atr(
            equity=equity,
            atr=atr,
            tick_size=tick_spec.tick_size,
            tick_value=tick_spec.tick_value,
        )

        base_lot = float(result.lot)

        self._logger.info(
            "ロット計算(base): equity=%.2f atr=%.5f tick_size=%.5f tick_value=%.5f -> lot=%.2f (capped=%s risk=%.3f)",
            equity,
            atr,
            tick_spec.tick_size,
            tick_spec.tick_value,
            base_lot,
            getattr(result, "capped_by_max_risk", None),
            float(getattr(result, "effective_risk_pct", 0.0) or 0.0),
        )

        # --- バックテスト由来のロット補正係数を適用 -------------------------
        scaler = self._get_lot_scaler()
        if scaler > 0.0 and scaler != 1.0 and base_lot > 0.0:
            # スケーラー適用前の lot を使って倍率を求める
            scaled_lot = base_lot * scaler

            # 安全のため物理的な最小/最大ロットでクランプ
            min_lot = 0.01
            max_lot = 1.00
            scaled_lot = max(min_lot, min(max_lot, scaled_lot))

            # 小数第2位で丸め
            scaled_lot = float(f"{scaled_lot:.2f}")

            if scaled_lot != base_lot:
                factor = scaled_lot / base_lot

                # LotSizingResult の関連フィールドも線形にスケーリングする
                # （定義されていないフィールドは無視）
                for field in (
                    "lot",
                    "per_trade_risk_pct",
                    "est_monthly_volatility_pct",
                    "est_max_monthly_dd_pct",
                    "effective_risk_pct",
                ):
                    if hasattr(result, field):
                        val = getattr(result, field)
                        if val is not None:
                            setattr(result, field, float(val) * factor)

                self._logger.info(
                    "Backtest lot scaler 適用: base_lot=%.3f scaler=%.3f -> adjusted_lot=%.3f (factor=%.3f)",
                    base_lot,
                    scaler,
                    scaled_lot,
                    factor,
                )
        else:
            self._logger.info("Backtest lot scaler=%.3f (ロット変更なし)", scaler)

        self._last_lot_result = result
        self.last_lot_result = result
        return result


    # ------------------------------------------------------------------ #
    # Configuration & helpers
    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        conf = cfg
        g = conf.get("guard", {}) or {}
        cb_cfg = conf.get("circuit_breaker", {}) or {}

        max_positions = int(g.get("max_positions", conf.get("runtime", {}).get("max_positions", 1)))
        inflight_timeout = int(g.get("inflight_timeout_sec", 20))
        self.pos_guard = PositionGuard(max_positions=max_positions, inflight_timeout_sec=inflight_timeout)

        self.cb = CircuitBreaker(
            max_consecutive_losses=int(cb_cfg.get("max_consecutive_losses", conf.get("risk", {}).get("max_consecutive_losses", 5))),
            daily_loss_limit_jpy=float(cb_cfg.get("daily_loss_limit_jpy", 0.0)),
            cooldown_min=int(cb_cfg.get("cooldown_min", 30)),
        )
        self._reconcile_interval = int(g.get("reconcile_interval_sec", 15))
        self._desync_fix = bool(g.get("desync_fix", True))
        self._last_reconcile = 0.0
        self.state = trade_state.get_runtime()

    def _periodic_reconcile(self, symbol: str) -> None:
        now = time.time()
        if now - self._last_reconcile >= self._reconcile_interval:
            self._last_reconcile = now
            self.pos_guard.reconcile_with_broker(symbol=symbol, desync_fix=self._desync_fix)

    # ------------------------------------------------------------------ #
    # Decisions & guards
    # ------------------------------------------------------------------ #
    def can_open(self, symbol: Optional[str]) -> bool:
        if symbol:
            self._periodic_reconcile(symbol)
        return self.pos_guard.can_open()

    def decide_entry_from_probs(self, p_buy: float, p_sell: float) -> Dict:
        conf = load_config()
        entry_cfg = conf.get("entry", {}) if isinstance(conf, dict) else {}
        th = float(entry_cfg.get("prob_threshold", entry_cfg.get("threshold_buy", 0.60)))
        edge = float(entry_cfg.get("entry_min_edge", entry_cfg.get("min_edge", 0.0)))
        bias = (entry_cfg.get("side_bias") or "auto").lower()

        pmax = p_buy if p_buy >= p_sell else p_sell
        p2nd = p_sell if p_buy >= p_sell else p_buy
        side = "BUY" if p_buy >= p_sell else "SELL"

        if pmax < th:
            return {"decision": "SKIP", "meta": "SKIP", "side": None, "reason": "ai_skip", "threshold": th}

        if (pmax - p2nd) < edge:
            return {
                "decision": "SKIP",
                "meta": "SKIP",
                "side": None,
                "reason": "ai_low_edge",
                "threshold": th,
                "edge": edge,
            }

        if p_buy == p_sell:
            if bias == "buy":
                side = "BUY"
            elif bias == "sell":
                side = "SELL"

        return {"decision": "ENTRY", "meta": side, "side": side, "threshold": th, "edge": edge}

    def decide_entry(self, p_buy: float, p_sell: float) -> Optional[str]:
        result = self.decide_entry_from_probs(p_buy, p_sell)
        return result["side"] if result.get("decision") == "ENTRY" else None

    def can_trade(self) -> bool:
        return self.cb.can_trade()


    def open_position(
        self,
        symbol: str,
        side: str,
        lot: float | None = None,
        atr: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        comment: str = "",
        features: Dict[str, Any] | None = None,
    ) -> None:
        """
        MT5 への発注。ATR を元に lot 計算を優先し、なければ ATR なしのフォールバック lot で送信。
        """
        if self._mt5 is None:
            raise RuntimeError("MT5 client is not configured on TradeService.")
        if self._profile is None:
            raise RuntimeError("Strategy profile is not configured on TradeService.")

        side_up = side.upper()
        if side_up not in {"BUY", "SELL"}:
            raise ValueError('side must be "buy" or "sell"')

        # --- フィルタエンジン呼び出しを追加 ---
        entry_context = {
            "timestamp": datetime.now(),
            "atr": atr,
            "volatility": features.get("volatility") if isinstance(features, dict) else None,
            "trend_strength": features.get("trend_strength") if isinstance(features, dict) else None,
            "consecutive_losses": self.cb.state.consecutive_losses if hasattr(self, "cb") and hasattr(self.cb, "state") else 0,
            "profile_stats": {
                "profile_name": self._profile.name if hasattr(self._profile, "name") else None,
            } if self._profile else {},
        }

        ok, reasons = evaluate_entry(entry_context)

        if not ok:
            # ここではまだ decisions.jsonl には書かず、ログだけ軽く出しておく
            self._logger.info(f"[Filter] entry blocked. reasons={reasons}")
            return

        equity = float(self._mt5.get_equity())
        tick_spec: TickSpec = self._mt5.get_tick_spec(symbol)

        lot_result: LotSizingResult | None = None
        lot_val = lot

        # ATR が指定されていて lot が決まっていない場合は ATR ベースで計算
        if (lot_val is None or lot_val == 0) and atr is not None and atr > 0:
            lot_result = self._profile.compute_lot_size_from_atr(
                equity=equity,
                atr=atr,
                tick_size=tick_spec.tick_size,
                tick_value=tick_spec.tick_value,
            )
            raw_volume = getattr(lot_result, "lot", None)
            if raw_volume is None:
                raw_volume = getattr(lot_result, "volume", None)
            if raw_volume is not None:
                lot_val = float(raw_volume)

        # フォールバック: ATR が無い/0 のときはデフォルト lot を使う
        if lot_val is None or lot_val <= 0:
            default_lot = getattr(self._profile, "default_lot", None)
            if default_lot is None:
                default_lot = float((cfg.get("trade", {}) or {}).get("default_lot", 0.01))
            lot_val = float(default_lot)

        self._last_lot_result = lot_result
        self.last_lot_result = lot_result

        self._mt5.order_send(
            symbol=symbol,
            order_type=side_up,
            lot=float(lot_val),
            sl=sl,
            tp=tp,
            comment=comment,
        )

    def mark_order_inflight(self, order_id: str) -> None:
        self.pos_guard.mark_inflight(order_id)

    def on_order_result(self, *, order_id: str, ok: bool, symbol: str) -> None:
        self.pos_guard.clear_inflight(order_id)
        if ok:
            self.pos_guard.reconcile_with_broker(symbol=symbol, desync_fix=True)

    def on_order_success(self, *, ticket: Optional[int], side: str, symbol: str, price: Optional[float] = None) -> None:
        self.pos_guard.reconcile_with_broker(symbol=symbol, desync_fix=True)
        runtime = self.state
        runtime.last_ticket = ticket
        runtime.last_side = side
        runtime.last_symbol = symbol
        EVENT_STORE.add(kind="ENTRY", symbol=symbol, side=side, price=price, sl=None, notes=f"ticket={ticket}")

    def on_broker_sync(self, symbol: Optional[str], fix: bool = True) -> None:
        self.pos_guard.reconcile_with_broker(symbol, desync_fix=fix)

    def record_trade_result(
        self,
        *,
        symbol: str,
        side: str,
        profit_jpy: float,
        info: Optional[dict[str, Any]] = None,
    ) -> None:
        resolved_symbol = symbol or self.state.last_symbol or "-"
        resolved_side = side or self.state.last_side
        notes = "settled"
        if info:
            if "notes" in info:
                notes = str(info["notes"])
            else:
                notes = str(info)
        EVENT_STORE.add(
            kind="CLOSE",
            symbol=resolved_symbol,
            side=resolved_side,
            profit_jpy=float(profit_jpy),
            notes=notes,
        )
        self.cb.on_trade_result(profit_jpy)


# ------------------------------------------------------------------ #
# Module-level helpers (backwards compatibility)
# ------------------------------------------------------------------ #
SERVICE = TradeService()


def execute_decision(
    decision: Dict[str, Any],
    *,
    symbol: Optional[str] = None,
    service: Optional[TradeService] = None,
) -> None:
    """
    Live 用のヘルパ:
    decision dict から TradeService.open_position(...) を呼び出す。

    期待する decision 形式の例::
        {
            "action": "ENTRY",
            "reason": "entry_ok",
            "signal": {
                "side": "BUY",
                "atr_for_lot": 0.0042,
                ...
            },
            "dec": {...},
        }

    - action != "ENTRY" の場合は何もしない
    - side や symbol が足りなければ何もしない
    - atr_for_lot はそのまま open_position(atr=...) に渡す
      （lot=None として渡し、TradeService 側で ATR ベースのロット計算を使う）
    """
    if not isinstance(decision, dict):
        # "SKIP" などの str が来た場合は黙って終了
        return

    action = decision.get("action")
    if action != "ENTRY":
        # エントリー以外（SKIP/BLOCKED/TRAIL_UPDATE）はここでは何もしない
        return

    signal = decision.get("signal") or {}
    if not isinstance(signal, dict):
        return

    side = signal.get("side")
    if not side:
        # どっちに建てるか不明なら何もしない
        return

    atr_for_lot = signal.get("atr_for_lot")

    svc = service or SERVICE

    # symbol が指定されていなければ設定ファイルから拾う（なければ何もしない）
    sym = symbol
    if not sym:
        try:
            from app.core.config_loader import load_config  # 遅延 import
            cfg = load_config()
            runtime_cfg = cfg.get("runtime", {}) if isinstance(cfg, dict) else {}
            sym = runtime_cfg.get("symbol")
        except Exception:
            sym = None

    if not sym:
        # シンボルが決まらない場合はエントリーしない
        return

    # lot=None + atr=atr_for_lot で呼び出し
    # open_position 側で StrategyProfile.compute_lot_size_from_atr を使って
    # ATR ベースの自動ロット計算が走る（既に実装済み）
    
    # features があれば取得（フィルタエンジン用）
    features = signal.get("features") or decision.get("features")
    
    svc.open_position(
        symbol=str(sym),
        side=str(side),
        lot=None,
        atr=float(atr_for_lot) if atr_for_lot is not None else None,
        features=features if isinstance(features, dict) else None,
    )


def can_open_new_position(symbol: Optional[str] = None) -> bool:
    settings = trade_state.get_settings()
    if not settings.trading_enabled:
        return False
    sym = symbol or load_config().get("runtime", {}).get("symbol")
    return SERVICE.can_open(sym)


def decide_entry(p_buy: float, p_sell: float) -> Optional[str]:
    return SERVICE.decide_entry(p_buy, p_sell)


def decide_entry_from_probs(p_buy: float, p_sell: float) -> dict:
    return SERVICE.decide_entry_from_probs(p_buy, p_sell)


def get_account_summary() -> dict[str, Any] | None:
    return mt5_client.get_account_info()


def build_exit_plan(symbol: str, ohlc_tail: Optional[Iterable[dict[str, Any]]]) -> dict[str, Any]:
    conf = load_config()
    ex_cfg = conf.get("exits", {}) if isinstance(conf, dict) else {}
    mode = (ex_cfg.get("mode") or "fixed").lower()

    if mode == "none":
        return {"mode": "none"}

    if mode == "fixed":
        fx = ex_cfg.get("fixed", {}) or {}
        return {
            "mode": "fixed",
            "tp_pips": float(fx.get("tp_pips", 10)),
            "sl_pips": float(fx.get("sl_pips", 10)),
        }

    if mode == "atr":
        ax = ex_cfg.get("atr", {}) or {}
        period = int(ax.get("period", 14))
        tp_mult = float(ax.get("tp_mult", 1.2))
        sl_mult = float(ax.get("sl_mult", 1.0))
        trailing = ax.get("trailing", {}) or {}

        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        for row in ohlc_tail or []:
            h = row.get("h") or row.get("high")
            l = row.get("l") or row.get("low")
            c = row.get("c") or row.get("close")
            if h is not None:
                highs.append(float(h))
            if l is not None:
                lows.append(float(l))
            if c is not None:
                closes.append(float(c))

        atr_value = _atr(highs, lows, closes, period)
        return {
            "mode": "atr",
            "atr": atr_value,
            "tp_mult": tp_mult,
            "sl_mult": sl_mult,
            "trailing": {
                "enabled": bool(trailing.get("enabled", True)),
                "activate_atr_mult": float(trailing.get("activate_atr_mult", 0.5)),
                "step_atr_mult": float(trailing.get("step_atr_mult", 0.25)),
                "lock_be_atr_mult": float(trailing.get("lock_be_atr_mult", 0.3)),
                "hard_floor_pips": float(trailing.get("hard_floor_pips", 5)),
                "only_in_profit": bool(trailing.get("only_in_profit", True)),
                "max_layers": int(trailing.get("max_layers", 20)),
                "price_source": (trailing.get("price_source") or "mid").lower(),
            },
        }

    return {"mode": "fixed", "tp_pips": 10, "sl_pips": 10}


_trade_last_fill_ts: Optional[datetime] = None


def mark_filled_now() -> None:
    """Record the timestamp of the latest successful fill."""
    global _trade_last_fill_ts
    _trade_last_fill_ts = now_jst()


def post_fill_grace_active() -> bool:
    """Return True when the post-fill grace window is active."""
    if _trade_last_fill_ts is None:
        return False

    conf = load_config()
    runtime_cfg = conf.get("runtime", {}) if isinstance(conf, dict) else {}
    grace_sec = int((runtime_cfg or {}).get("post_fill_grace_sec", 0) or 0)
    if grace_sec <= 0:
        return False

    return (now_jst() - _trade_last_fill_ts) <= timedelta(seconds=grace_sec)


def mark_order_inflight(order_id: str) -> None:
    SERVICE.mark_order_inflight(order_id)


def on_order_result(order_id: str, ok: bool, symbol: str) -> None:
    SERVICE.on_order_result(order_id=order_id, ok=ok, symbol=symbol)


def reconcile_positions(symbol: Optional[str] = None, desync_fix: bool = True) -> None:
    SERVICE.on_broker_sync(symbol, fix=desync_fix)


def on_order_success(ticket: Optional[int], side: str, symbol: str, price: Optional[float] = None) -> None:
    SERVICE.on_order_success(ticket=ticket, side=side, symbol=symbol, price=price)


def record_trade_result(
    *,
    symbol: str,
    side: str,
    profit_jpy: float,
    info: Optional[dict[str, Any]] = None,
) -> None:
    SERVICE.record_trade_result(symbol=symbol, side=side, profit_jpy=profit_jpy, info=info)


def circuit_breaker_can_trade() -> bool:
    return SERVICE.can_trade()
