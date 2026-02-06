from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional
import logging

from loguru import logger as loguru_logger

from app.core import mt5_client
from app.core.config_loader import load_config
from app.services import trade_state
from app.services.circuit_breaker import CircuitBreaker
from app.services.event_store import EVENT_STORE
from app.services.filter_service import evaluate_entry
from app.services.inflight_service import make_key as inflight_make_key, mark as inflight_mark, finish as inflight_finish
from app.services.loss_streak_service import update_on_trade_result, get_consecutive_losses
from app.core.config import cfg
from core.indicators import atr as _atr
from core.position_guard import PositionGuard
from core.utils.clock import now_jst

from app.core.mt5_client import MT5Client, TickSpec
from app.core.strategy_profile import StrategyProfile, get_profile
from core.risk import LotSizingResult, compute_lot_scaler_from_backtest
#from app.core.risk import LotSizingResult

def mt5_diag_snapshot(symbol: str) -> dict[str, Any]:
    """
    T-4: MT5接続・取引可能状態の診断スナップショット（観測のみ）。

    制約:
    - initialize/login/symbol_select 等の副作用操作は呼ばない（読み取りのみ）。
    - 取得できない場合は None/unknown を返す（raise しない）。
    """
    out: dict[str, Any] = {
        "symbol": symbol,
        "resolved_symbol": None,
        "connected": None,
        "terminal_trade_allowed": None,
        "account_trade_allowed": None,
        "symbol_visible": None,
        "symbol_trade_mode": None,
        "mt5_last_error": None,
    }

    # symbol解決（読み取りのみ）
    try:
        from app.core.symbol_map import resolve_symbol

        out["resolved_symbol"] = resolve_symbol(symbol)
    except Exception:
        out["resolved_symbol"] = symbol

    # MetaTrader5 API（読み取りのみ）
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception as e:
        out["mt5_last_error"] = f"import_failed:{type(e).__name__}:{e}"
        return out

    try:
        ti = mt5.terminal_info()
        out["connected"] = bool(ti)
        out["terminal_trade_allowed"] = getattr(ti, "trade_allowed", None) if ti is not None else None
    except Exception as e:
        out["mt5_last_error"] = f"terminal_info_failed:{type(e).__name__}:{e}"

    try:
        ai = mt5.account_info()
        out["account_trade_allowed"] = getattr(ai, "trade_allowed", None) if ai is not None else None
    except Exception as e:
        out["mt5_last_error"] = f"account_info_failed:{type(e).__name__}:{e}"

    try:
        rs = out.get("resolved_symbol") or symbol
        si = mt5.symbol_info(rs)
        out["symbol_visible"] = getattr(si, "visible", None) if si is not None else None
        out["symbol_trade_mode"] = getattr(si, "trade_mode", None) if si is not None else None
    except Exception as e:
        out["mt5_last_error"] = f"symbol_info_failed:{type(e).__name__}:{e}"

    try:
        le = mt5.last_error()
        out["mt5_last_error"] = str(le)
    except Exception:
        pass

    return out


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


def get_profile_lot_limits() -> tuple[Optional[float], Optional[float]]:
    """
    現在の戦略プロファイル（config の lot セクション）から min_lot / max_lot を返す。
    GUI の口座帯表示用。取得失敗時は (None, None) を返し、ログに WARNING を出す。
    """
    try:
        conf = load_config()
        lot_cfg = (conf.get("lot", {}) if isinstance(conf, dict) else {}) or {}
        if not isinstance(lot_cfg, dict):
            loguru_logger.warning("[lot_limits] config 'lot' is not a dict: {}", type(lot_cfg).__name__)
            return (None, None)
        min_lot = lot_cfg.get("min_lot")
        max_lot = lot_cfg.get("max_lot")
        if min_lot is None and max_lot is None:
            loguru_logger.warning("[lot_limits] config 'lot' has no min_lot/max_lot")
            return (None, None)
        min_f = float(min_lot) if min_lot is not None else None
        max_f = float(max_lot) if max_lot is not None else None
        return (min_f, max_f)
    except Exception as e:
        loguru_logger.warning("[lot_limits] get_profile_lot_limits failed: {}", e)
        return (None, None)


def run_start_diagnosis(symbol: str) -> bool:
    """
    T-65: 自動売買ループ開始直後の事前診断（1回だけ）。
    T-65.1: ログイン前押下時も例外を出さず NG 即 BLOCK（env 未設定等は BLOCK として回収）。

    - services 層から mt5_client.initialize() を実行
    - initialize() が RuntimeError（env 未設定等）を投げた場合は例外を握り、ok_init=False 相当で BLOCK
    - connected / trade_allowed / account(REAL|DEMO) を取得
    - 1行ログ [mt5] connected=... trade_allowed=... account=... を出力
    - NG 時: next_action=BLOCKED を確定し、ui_events と ops_history に理由を記録して False を返す
    - OK 時: True を返し、既存フローに合流（MT5 は接続のまま）

    Returns:
        True: 取引可能。False: 取引不可のためループを開始しない。
    """
    from loguru import logger
    from app.services.ops_history_service import append_ops_result
    from datetime import datetime, timezone

    connected = False
    trade_allowed = False
    account_label = "UNKNOWN"
    ok_init = False
    exception_caught = False
    try:
        ok_init = mt5_client.initialize()
    except Exception as e:
        loguru_logger.warning(
            "[trade_loop] start diagnosis exception (treated as BLOCK): {}",
            e,
        )
        ok_init = False
        exception_caught = True

    if not ok_init:
        connected = False
        trade_allowed = False
        account_label = "UNKNOWN"
    else:
        try:
            diag = mt5_diag_snapshot(symbol)
            connected = bool(diag.get("connected"))
            term_allowed = diag.get("terminal_trade_allowed")
            account_allowed = diag.get("account_trade_allowed")
            trade_allowed = (term_allowed is True and account_allowed is True)
            info = mt5_client.get_account_info()
            account_label = "REAL" if (info and getattr(info, "trade_mode", 0) != 0) else "DEMO"
        except Exception:
            connected = False
            trade_allowed = False
            account_label = "UNKNOWN"

    loguru_logger.info(
        "[mt5] connected={} trade_allowed={} account={}",
        connected,
        trade_allowed,
        account_label,
    )

    if connected and trade_allowed:
        return True

    reason = "env_missing" if exception_caught else ("mt5_not_connected" if not connected else "trade_not_allowed")
    loguru_logger.warning(
        "[trade_loop] start blocked reason={} next_action=BLOCKED",
        reason,
    )
    EVENT_STORE.add(
        kind="INFO",
        symbol=symbol,
        reason=reason,
        notes="next_action=BLOCKED trade_loop_start_blocked",
    )
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    append_ops_result({
        "symbol": symbol,
        "profiles": [],
        "started_at": started_at,
        "ok": False,
        "step": "blocked",
        "model_path": None,
        "reason": reason,
        "next_action": "BLOCKED",
    })
    return False


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
        self._entry_gate_lock = threading.Lock()
        self._entry_inflight = False

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

    def _log_gate_denied(self, symbol: str, reason: str, run_id: Optional[int] = None) -> None:
        """ガード拒否を decisions.jsonl に GATE_DENIED として追記（検証用）。失敗時は握り潰す。"""
        try:
            from app.services.execution_stub import _write_decision_log
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            _write_decision_log(symbol, {"ts": ts, "action": "GATE_DENIED", "reason": reason, "run_id": run_id})
        except Exception:
            pass

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
        dry_run: bool = False,
        run_id: Optional[int] = None,
    ) -> None:
        """
        MT5 への発注。ATR を元に lot 計算を優先し、なければ ATR なしのフォールバック lot で送信。
        """
        if self._profile is None:
            raise RuntimeError("Strategy profile is not configured on TradeService.")

        side_up = side.upper()
        if side_up not in {"BUY", "SELL"}:
            raise ValueError('side must be "buy" or "sell"')

        # --- T-62-2 MT5に触る前ガード: run_id 指定時はここで止め、login/order_send に進ませない ---
        if run_id is not None:
            rt_early = trade_state.get_runtime()
            if not trade_state.get_settings().trading_enabled:
                loguru_logger.info(
                    "[ORDER] cancelled reason=trading_disabled run_id={} (before MT5)",
                    run_id,
                )
                self._log_gate_denied(symbol, "trading_disabled", run_id)
                return
            if not getattr(rt_early, "trade_loop_running", False):
                loguru_logger.info(
                    "[ORDER] cancelled reason=trade_loop_not_running run_id={} rt_running={} rt_run_id={} (before MT5)",
                    run_id,
                    getattr(rt_early, "trade_loop_running", False),
                    getattr(rt_early, "trade_run_id", None),
                )
                self._log_gate_denied(symbol, "trade_loop_not_running", run_id)
                return
            if getattr(rt_early, "trade_run_id", None) != run_id:
                loguru_logger.info(
                    "[ORDER] cancelled reason=run_id_mismatch expected={} got={} run_id={} (before MT5)",
                    getattr(rt_early, "trade_run_id", None),
                    run_id,
                    run_id,
                )
                self._log_gate_denied(symbol, "run_id_mismatch", run_id)
                return

        # --- T-45-4(A): ENTRYゲート（単一） ---
        # 目的:
        # - 多重ENTRY/EXIT競合（inflight/open）を抑止
        # - 連続損失が閾値以上なら ENTRY を deny（最も安全）
        # 方針:
        # - 例外で止めない（denyしてreturn）
        # - 監査用にINFOログを必ず残す
        evidence_keys: list[str] = []
        try:
            if isinstance(features, dict):
                evidence_keys = list(features.keys())
        except Exception:
            evidence_keys = []

        # --- MT5未設定の live 実行は縮退（例外停止禁止） ---
        # - dry_run は従来どおり通す（発注だけスキップ）
        # - live は ENTRY deny + 監査ログ（app.log / ui_events INFO）
        if (not dry_run) and (self._mt5 is None):
            msg = "[guard][entry] denied reason=mt5_unavailable symbol=%s side=%s evidence_keys=%s"
            self._logger.info(msg, symbol, side_up, evidence_keys)
            try:
                EVENT_STORE.add(
                    kind="INFO",
                    symbol=symbol,
                    side=side_up,
                    reason="mt5_unavailable",
                    notes=(msg % (symbol, side_up, evidence_keys)),
                )
            except Exception:
                pass
            self._log_gate_denied(symbol, "mt5_unavailable", run_id)
            return

        # --- フィルタエンジン呼び出しを追加 ---
        # 連敗数を取得（プロファイル名・シンボルは実際の変数名に合わせてください）
        profile_name = self._profile.name if hasattr(self._profile, "name") and self._profile else "michibiki_std"
        consecutive_losses = get_consecutive_losses(profile_name, symbol)

        # 0) circuit breaker（既存のservices状態を使用）
        try:
            if hasattr(self, "cb") and (not self.cb.can_trade()):
                msg = (
                    "[guard][entry] denied reason=circuit_breaker symbol=%s side=%s consec_losses=%s evidence_keys=%s"
                )
                self._logger.info(
                    "[guard][entry] denied reason=circuit_breaker symbol=%s side=%s consec_losses=%s evidence_keys=%s",
                    symbol,
                    side_up,
                    consecutive_losses,
                    evidence_keys,
                )
                try:
                    EVENT_STORE.add(kind="INFO", symbol=symbol, side=side_up, reason="guard_entry_denied", notes=(msg % (symbol, side_up, consecutive_losses, evidence_keys)))
                except Exception:
                    pass
                self._log_gate_denied(symbol, "circuit_breaker", run_id)
                return
        except Exception:
            # 観測不能なら gate は縮退（raiseで止めない）
            pass

        # 1) position/inflight（既存 PositionGuard を使用）
        try:
            # reconcile + open_count gate
            if not self.can_open(symbol):
                open_count = getattr(getattr(self.pos_guard, "state", None), "open_count", None)
                max_pos = getattr(self.pos_guard, "max_positions", None)
                inflight_n = None
                try:
                    inflight_n = len(getattr(getattr(self.pos_guard, "state", None), "inflight_orders", {}) or {})
                except Exception:
                    inflight_n = None
                msg = (
                    "[guard][entry] denied reason=max_positions_reached symbol=%s side=%s open_count=%s max_positions=%s inflight=%s evidence_keys=%s"
                )
                self._logger.info(
                    "[guard][entry] denied reason=max_positions_reached symbol=%s side=%s open_count=%s max_positions=%s inflight=%s evidence_keys=%s",
                    symbol,
                    side_up,
                    open_count,
                    max_pos,
                    inflight_n,
                    evidence_keys,
                )
                try:
                    EVENT_STORE.add(kind="INFO", symbol=symbol, side=side_up, reason="guard_entry_denied", notes=(msg % (symbol, side_up, open_count, max_pos, inflight_n, evidence_keys)))
                except Exception:
                    pass
                self._log_gate_denied(symbol, "max_positions_reached", run_id)
                return
            # inflight gate（ENTRY/EXIT競合の最小抑止）
            inflight_orders = getattr(getattr(self.pos_guard, "state", None), "inflight_orders", None)
            if isinstance(inflight_orders, dict) and len(inflight_orders) > 0:
                # NOTE: 観測強化（ロジック不変）
                # - inflight_orders は {key: ts(float)} 想定だが、型揺れがあり得るためログ生成は安全に行う
                inflight_timeout_sec = getattr(self.pos_guard, "inflight_timeout_sec", None)
                inflight_keys = []
                inflight_age_sec = []
                try:
                    _now_ts = time.time()
                    for _k, _ts in list(inflight_orders.items())[:5]:
                        inflight_keys.append(_k)
                        try:
                            inflight_age_sec.append(round(float(_now_ts - float(_ts)), 2))
                        except Exception:
                            inflight_age_sec.append(f"bad_ts:{type(_ts).__name__}")
                except Exception:
                    # ログ生成失敗でも guard 判定自体は変えない
                    inflight_keys = ["<log_failed>"]
                    inflight_age_sec = ["<log_failed>"]
                msg = (
                    "[guard][entry] denied reason=inflight_orders symbol=%s side=%s inflight=%s inflight_timeout_sec=%s inflight_keys=%s inflight_age_sec=%s evidence_keys=%s"
                )
                self._logger.info(
                    "[guard][entry] denied reason=inflight_orders symbol=%s side=%s inflight=%s inflight_timeout_sec=%s inflight_keys=%s inflight_age_sec=%s evidence_keys=%s",
                    symbol,
                    side_up,
                    len(inflight_orders),
                    inflight_timeout_sec,
                    inflight_keys,
                    inflight_age_sec,
                    evidence_keys,
                )
                try:
                    EVENT_STORE.add(
                        kind="INFO",
                        symbol=symbol,
                        side=side_up,
                        reason="guard_entry_denied",
                        notes=(
                            msg
                            % (
                                symbol,
                                side_up,
                                len(inflight_orders),
                                inflight_timeout_sec,
                                inflight_keys,
                                inflight_age_sec,
                                evidence_keys,
                            )
                        ),
                    )
                except Exception:
                    pass
                self._log_gate_denied(symbol, "inflight_orders", run_id)
                return
        except Exception:
            # 観測不能なら gate は縮退（raiseで止めない）
            pass

        # 2) loss streak threshold（既存設定と既存ログを使用、推測で作らない）
        try:
            # TradeService.reload() で cb.max_consecutive_losses が確定している（configs/config.yaml 由来）
            streak_th = int(getattr(self.cb, "max_consecutive_losses", 0) or 0)
            if streak_th > 0 and int(consecutive_losses) >= streak_th:
                msg = (
                    "[guard][streak] entry_blocked consec_losses=%d threshold=%d action=DENY reason=loss_streak_limit symbol=%s side=%s evidence_keys=%s"
                )
                self._logger.info(
                    "[guard][streak] entry_blocked consec_losses=%d threshold=%d action=DENY reason=loss_streak_limit symbol=%s side=%s evidence_keys=%s",
                    int(consecutive_losses),
                    int(streak_th),
                    symbol,
                    side_up,
                    evidence_keys,
                )
                try:
                    EVENT_STORE.add(kind="INFO", symbol=symbol, side=side_up, reason="guard_streak_denied", notes=(msg % (int(consecutive_losses), int(streak_th), symbol, side_up, evidence_keys)))
                except Exception:
                    pass
                self._log_gate_denied(symbol, "loss_streak_limit", run_id)
                return
        except Exception:
            pass

        # 3) entry_inflight（__init__ で初期化済み）
        entry_inflight_acquired = False
        try:
            with self._entry_gate_lock:
                if bool(self._entry_inflight):
                    msg = "[guard][entry] denied reason=entry_inflight symbol=%s side=%s evidence_keys=%s"
                    self._logger.info(
                        "[guard][entry] denied reason=entry_inflight symbol=%s side=%s evidence_keys=%s",
                        symbol,
                        side_up,
                        evidence_keys,
                    )
                    try:
                        EVENT_STORE.add(kind="INFO", symbol=symbol, side=side_up, reason="guard_entry_denied", notes=(msg % (symbol, side_up, evidence_keys)))
                    except Exception:
                        pass
                    self._log_gate_denied(symbol, "entry_inflight", run_id)
                    return
                self._entry_inflight = True
                entry_inflight_acquired = True
        except Exception:
            # フラグが壊れても売買フローは止めない（縮退して継続）
            pass
        # --- /T-45-4(A) ---
        inflight_key = None
        order_ok = False
        try:
            entry_context = {
                "timestamp": datetime.now(),
                "atr": atr,
                "volatility": features.get("volatility") if isinstance(features, dict) else None,
                "trend_strength": features.get("trend_strength") if isinstance(features, dict) else None,
                "consecutive_losses": consecutive_losses,
                "profile_stats": {
                    "profile_name": profile_name,
                } if self._profile else {},
            }

            ok, reasons = evaluate_entry(entry_context)

            if not ok:
                # ここではまだ decisions.jsonl には書かず、ログだけ軽く出しておく
                self._logger.info(f"[Filter] entry blocked. reasons={reasons}")
                self._log_gate_denied(symbol, "filter_blocked", run_id)
                return

            equity = None
            tick_spec = None
            try:
                if self._mt5 is not None:
                    equity = float(self._mt5.get_equity())
                    tick_spec = self._mt5.get_tick_spec(symbol)
            except Exception:
                equity = None
                tick_spec = None

            lot_result: LotSizingResult | None = None
            lot_val = lot

            # ATR が指定されていて lot が決まっていない場合は ATR ベースで計算
            if (
                (lot_val is None or lot_val == 0)
                and atr is not None
                and atr > 0
                and equity is not None
                and tick_spec is not None
            ):
                lot_result = self._profile.compute_lot_size_from_atr(
                    equity=float(equity),
                    atr=atr,
                    tick_size=float(getattr(tick_spec, "tick_size", 0.0) or 0.0),
                    tick_value=float(getattr(tick_spec, "tick_value", 0.0) or 0.0),
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

            # --- T-44-4: size_decision.multiplier を最終ロットに反映（services-only / add-only） ---
            # 目的:
            # - 最終ロット = base_lot × size_decision.multiplier
            # - multiplier=1.0 の場合、従来挙動と完全一致（実質 no-op）
            # 入力:
            # - features["size_decision"] = {"multiplier": 0.5|1.0|1.5, "reason": "..."} を優先
            # - 無ければ features["size_multiplier"] を参照（後方互換）
            size_mult = 1.0
            size_reason = None
            try:
                if isinstance(features, dict):
                    sd = features.get("size_decision")
                    if isinstance(sd, dict):
                        if sd.get("multiplier") is not None:
                            size_mult = float(sd.get("multiplier"))
                        if sd.get("reason") is not None:
                            size_reason = str(sd.get("reason"))
                    elif features.get("size_multiplier") is not None:
                        size_mult = float(features.get("size_multiplier"))
            except Exception:
                size_mult = 1.0
                size_reason = None

            try:
                if isinstance(lot_val, (int, float)) and size_mult != 1.0:
                    base_lot = float(lot_val)
                    lot_val = base_lot * float(size_mult)
                    self._logger.info(
                        "[lot] apply size_decision: base_lot=%.6f mult=%.3f -> lot=%.6f reason=%s",
                        base_lot,
                        float(size_mult),
                        float(lot_val),
                        size_reason,
                    )
            except Exception:
                # ここで例外を出すと発注自体が止まるため、縮退（倍率を無視）
                pass
            # --- /T-44-4 ---

            # --- T-45-4(B): ロット上限（min/max）clamp（multiplier適用後、最終確定直前） ---
            # 目的:
            # - size上限が必ず効く（config/defaultどちらでも観測可能）
            # - multiplier乗算ロジックは変更しない（その後で clamp する）
            try:
                lot_cfg = (cfg.get("lot", {}) if isinstance(cfg, dict) else {}) or {}
                src = "default"
                if isinstance(lot_cfg, dict) and (("min_lot" in lot_cfg) or ("max_lot" in lot_cfg)):
                    src = "config"
                min_lot = float(lot_cfg.get("min_lot", 0.01) if isinstance(lot_cfg, dict) else 0.01)
                max_lot = float(lot_cfg.get("max_lot", 1.0) if isinstance(lot_cfg, dict) else 1.0)

                before = float(lot_val) if isinstance(lot_val, (int, float)) else None
                if before is not None:
                    after = max(min_lot, min(max_lot, before))
                    clamped = bool(after != before)
                    # 毎回出す（clamp有無を観測で確定できるように）
                    self._logger.info(
                        "[lot][clamp] before=%.6f after=%.6f clamped=%s min=%.6f max=%.6f src=%s reason=size_cap",
                        before,
                        after,
                        clamped,
                        min_lot,
                        max_lot,
                        src,
                    )
                    try:
                        EVENT_STORE.add(
                            kind="INFO",
                            symbol=symbol,
                            side=side_up,
                            reason="lot_clamp",
                            notes=(
                                "[lot][clamp] before=%.6f after=%.6f clamped=%s min=%.6f max=%.6f src=%s reason=size_cap"
                                % (before, after, clamped, min_lot, max_lot, src)
                            ),
                        )
                    except Exception:
                        pass
                    if clamped:
                        lot_val = after
            except Exception:
                # clampが壊れても例外で止めない（縮退）
                pass
            # --- /T-45-4(B) ---

            self._last_lot_result = lot_result
            self.last_lot_result = lot_result

            # --- T-62-2 発注直前の最終防衛: trading_enabled → trade_loop_running → run_id（安い順） ---
            rt = trade_state.get_runtime()
            if run_id is not None:
                if not trade_state.get_settings().trading_enabled:
                    loguru_logger.info("[ORDER] cancelled reason=trading_disabled run_id={}", run_id)
                    self._log_gate_denied(symbol, "trading_disabled", run_id)
                    return
                if not getattr(rt, "trade_loop_running", False):
                    loguru_logger.info(
                        "[ORDER] cancelled reason=trade_loop_not_running run_id={} rt_running={} rt_run_id={}",
                        run_id,
                        getattr(rt, "trade_loop_running", False),
                        getattr(rt, "trade_run_id", None),
                    )
                    self._log_gate_denied(symbol, "trade_loop_not_running", run_id)
                    return
                current = getattr(rt, "trade_run_id", None)
                if current != run_id:
                    loguru_logger.info(
                        "[ORDER] cancelled reason=run_id_mismatch expected={} got={} run_id={}",
                        current,
                        run_id,
                        run_id,
                    )
                    self._log_gate_denied(symbol, "run_id_mismatch", run_id)
                    return

            # --- SL/TP 補完ロジック（最終防波堤）---
            # sl/tp が None の場合、build_exit_plan から取得して補完する
            # それでも補完できなければ発注をブロック
            if sl is None or tp is None:
                try:
                    import MetaTrader5 as mt5
                    tick = mt5.symbol_info_tick(symbol)
                    if tick is None:
                        loguru_logger.warning(
                            "[guard] denied reason=missing_sl_tp detail=tick_unavailable symbol={} side={} run_id={}",
                            symbol, side_up, run_id,
                        )
                        self._log_gate_denied(symbol, "missing_sl_tp_tick_unavailable", run_id)
                        return

                    # 現在価格: BUY は ask、SELL は bid
                    entry_price = tick.ask if side_up == "BUY" else tick.bid

                    # pip_size を symbol から算出（JPY系は 0.01、それ以外は 0.0001）
                    sym_upper = symbol.upper().replace("-", "").replace("_", "")
                    pip_size = 0.01 if "JPY" in sym_upper else 0.0001

                    # build_exit_plan を呼んで tp_pips / sl_pips を取得
                    exit_plan = build_exit_plan(symbol, None)
                    mode = exit_plan.get("mode", "fixed")

                    if mode == "none":
                        loguru_logger.warning(
                            "[guard] denied reason=missing_sl_tp detail=exit_mode_none symbol={} side={} run_id={}",
                            symbol, side_up, run_id,
                        )
                        self._log_gate_denied(symbol, "missing_sl_tp_exit_mode_none", run_id)
                        return

                    tp_pips = exit_plan.get("tp_pips")
                    sl_pips = exit_plan.get("sl_pips")

                    # ATR モードの場合は atr * mult で pips を計算
                    if mode == "atr":
                        atr_val = exit_plan.get("atr")
                        tp_mult = exit_plan.get("tp_mult", 1.2)
                        sl_mult = exit_plan.get("sl_mult", 1.0)
                        if atr_val is not None and atr_val > 0:
                            # ATR を pips に変換（価格単位 / pip_size）
                            tp_pips = (atr_val * tp_mult) / pip_size
                            sl_pips = (atr_val * sl_mult) / pip_size

                    if tp_pips is None or sl_pips is None or tp_pips <= 0 or sl_pips <= 0:
                        loguru_logger.warning(
                            "[guard] denied reason=missing_sl_tp detail=pips_invalid tp_pips={} sl_pips={} symbol={} side={} run_id={}",
                            tp_pips, sl_pips, symbol, side_up, run_id,
                        )
                        self._log_gate_denied(symbol, "missing_sl_tp_pips_invalid", run_id)
                        return

                    # 価格を計算: BUY は SL < entry < TP、SELL は TP < entry < SL
                    if side_up == "BUY":
                        if sl is None:
                            sl = entry_price - float(sl_pips) * pip_size
                        if tp is None:
                            tp = entry_price + float(tp_pips) * pip_size
                    else:  # SELL
                        if sl is None:
                            sl = entry_price + float(sl_pips) * pip_size
                        if tp is None:
                            tp = entry_price - float(tp_pips) * pip_size

                    loguru_logger.info(
                        "[ORDER] sl_tp_complemented entry_price={:.5f} sl={:.5f} tp={:.5f} sl_pips={:.1f} tp_pips={:.1f} mode={} symbol={} side={}",
                        entry_price, sl, tp, sl_pips, tp_pips, mode, symbol, side_up,
                    )
                except Exception as e:
                    loguru_logger.warning(
                        "[guard] denied reason=missing_sl_tp detail=complement_failed error={} symbol={} side={} run_id={}",
                        e, symbol, side_up, run_id,
                    )
                    self._log_gate_denied(symbol, "missing_sl_tp_complement_failed", run_id)
                    return

            # --- SL/TP 最終チェック（必ず数値が入っていること）---
            if sl is None or tp is None:
                loguru_logger.warning(
                    "[guard] denied reason=missing_sl_tp detail=final_check_failed sl={} tp={} symbol={} side={} run_id={}",
                    sl, tp, symbol, side_up, run_id,
                )
                self._log_gate_denied(symbol, "missing_sl_tp_final_check_failed", run_id)
                return

            # --- T-3 observe: order_send prepared (single point) ---
            # NOTE: trade logic is unchanged; this is observation-only logging.
            loguru_logger.info(
                "[ORDER] prepared symbol={} side={} lot={} sl={} tp={} dry_run={} run_id={}",
                symbol,
                side_up,
                float(lot_val),
                sl,
                tp,
                bool(dry_run),
                run_id,
            )

            if dry_run:
                # dry_run=True のときは発注せず、ENTRYイベントだけ残す（観測用）
                # - ガード/倍率/クランプは同一ロジックで通る
                self._logger.info(
                    "[order][dry_run] skip order_send symbol=%s side=%s lot=%.6f",
                    symbol,
                    side_up,
                    float(lot_val),
                )
                # --- T-3 observe: dry_run skip (must be visible in app.log) ---
                loguru_logger.info("[ORDER] skipped reason=dry_run")
                try:
                    EVENT_STORE.add(
                        kind="ENTRY",
                        symbol=symbol,
                        side=side_up,
                        price=None,
                        sl=sl,
                        tp=tp,
                        notes=(
                            "dryrun [order][dry_run] skip order_send symbol=%s side=%s lot=%.6f"
                            % (symbol, side_up, float(lot_val))
                        ),
                    )
                except Exception:
                    pass
            else:
                # live: MT5へ実際に送る（inflight は services 層で実施）
                rt_final = trade_state.get_runtime()
                current_run_id = getattr(rt_final, "trade_run_id", None)
                if run_id is not None and current_run_id != run_id:
                    loguru_logger.info(
                        "[ORDER] cancelled reason=run_id_stale_final run_id={} current={}",
                        run_id, current_run_id,
                    )
                    self._log_gate_denied(symbol, "run_id_stale_final", run_id)
                    return
                inflight_key = inflight_make_key(symbol)
                try:
                    inflight_mark(inflight_key)
                except Exception:
                    pass
                order_result = self._mt5.order_send(
                    symbol=symbol,
                    order_type=side_up,
                    lot=float(lot_val),
                    sl=sl,
                    tp=tp,
                    comment=comment,
                )
                ticket, retcode, comment_msg = (order_result or (None, None, None))[:3]
                order_ok = bool(ticket)
                if ticket:
                    self.on_order_success(ticket=ticket, side=side_up, symbol=symbol, price=None)
                # --- T-3 observe: order_send result (best-effort) ---
                loguru_logger.info(
                    "[ORDER] sent ok={} retcode={} order_id={} message={}",
                    bool(ticket),
                    retcode,
                    ticket,
                    comment_msg,
                )
        finally:
            if inflight_key is not None:
                try:
                    inflight_finish(key=inflight_key, ok=order_ok, symbol=symbol)
                except Exception:
                    pass
            # entry_inflight を解除（例外で止めない）
            try:
                if entry_inflight_acquired:
                    self._entry_inflight = False
            except Exception as e:
                loguru_logger.error("[CRITICAL] entry_inflight release failed: {}", e)
                try:
                    self._entry_inflight = False
                except Exception as e2:
                    loguru_logger.error("[CRITICAL] entry_inflight fallback release failed: {}", e2)

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
        """
        決済結果（確定損益）を ui_events.jsonl に記録する（表示/観測用）。

        T-44-3 仕様固定: record_trade_result(info) の入力契約
        - 目的: repo 内に caller が居ない（観測事実）ため、将来どこから呼ばれても
          “推測なし”で exit_reason/exit_type を渡せるよう、services 側で正規化ルールを固定する。

        入力（info の許容キー / 優先順位）:
        - 優先（推奨）:
          - info["exit_reason"]: str（例: "TP" / "SL" / それ以外の短いコード）
          - info["exit_type"]:   str（"DEFENSE" | "PROFIT" のみ受理）
        - 後方互換（限定的に受理）:
          - info["reason"] または info["close_reason"] が "TP"|"SL" のときだけ exit_reason として採用
          - "たぶんTP" 等の推測は禁止（= 上記の明示コード以外は採用しない）

        正規化ルール（推測ゼロ）:
        - 材料が無い場合:
            exit_reason="UNKNOWN", exit_type="DEFENSE"
        - exit_reason=="TP" のときだけ:
            exit_type="PROFIT"
        - exit_reason=="SL" のときだけ:
            exit_type="DEFENSE"
        - それ以外:
            exit_type は caller 明示が無い限り DEFENSE（推測で PROFIT にしない）

        互換性:
        - profit_jpy 等の既存ログキーは変更しない（exit_* は追加のみ）。
        """
        resolved_symbol = symbol or self.state.last_symbol or "-"
        resolved_side = side or self.state.last_side
        notes = "settled"
        # T-44-3: Exit as Decision (label-only / add-only)
        # Observation (logs/ui_events.jsonl): CLOSE events currently have no TP/SL discriminator.
        # Therefore:
        # - If caller provides explicit exit_reason/exit_type, we use it (add-only).
        # - Otherwise, we record exit_reason="UNKNOWN" and exit_type="DEFENSE".
        exit_reason = "UNKNOWN"
        exit_type = "DEFENSE"
        if info:
            if "notes" in info:
                notes = str(info["notes"])
            else:
                notes = str(info)

            # add-only: accept explicit keys only (do not infer)
            try:
                if isinstance(info.get("exit_reason"), str) and info.get("exit_reason"):
                    exit_reason = str(info.get("exit_reason"))
            except Exception:
                pass
            try:
                if isinstance(info.get("exit_type"), str) and info.get("exit_type"):
                    et = str(info.get("exit_type")).upper()
                    if et in ("DEFENSE", "PROFIT"):
                        exit_type = et
            except Exception:
                pass

            # Backwards-compatible: some callers may still pass only "reason".
            # We only accept explicit TP/SL codes (no guessing).
            if exit_reason == "UNKNOWN":
                try:
                    r = info.get("reason") or info.get("close_reason")
                    if isinstance(r, str) and r in ("TP", "SL"):
                        exit_reason = r
                except Exception:
                    pass

        # minimal classification using only explicit codes (no guessing)
        if exit_reason == "TP":
            exit_type = "PROFIT"
        elif exit_reason == "SL":
            exit_type = "DEFENSE"
        EVENT_STORE.add(
            kind="CLOSE",
            symbol=resolved_symbol,
            side=resolved_side,
            profit_jpy=float(profit_jpy),
            notes=notes,
            exit_type=exit_type,
            exit_reason=exit_reason,
        )
        self.cb.on_trade_result(profit_jpy)

        # 連敗カウンタ更新
        try:
            profile_name = self._profile.name if hasattr(self._profile, "name") and self._profile else "michibiki_std"
            new_streak = update_on_trade_result(profile_name, resolved_symbol, float(profit_jpy))
            self._logger.info(
                "[Execution] loss streak updated: profile=%s symbol=%s pl=%.2f consecutive_losses=%d",
                profile_name, resolved_symbol, profit_jpy, new_streak
            )
        except Exception:
            # ここで例外を握ることで、連敗カウンタの不具合で売買自体が止まらないようにする
            self._logger.exception(
                "[Execution] failed to update loss streak (profile=%s, symbol=%s)",
                getattr(self._profile, "name", "unknown") if self._profile else "unknown",
                resolved_symbol
            )


# ------------------------------------------------------------------ #
# Module-level helpers (backwards compatibility)
# ------------------------------------------------------------------ #
_SERVICE: Optional[TradeService] = None
_SERVICE_LOCK = threading.Lock()


def get_default_trade_service() -> TradeService:
    """
    TradeService の単一点シングルトン取得。

    - import 時に勝手に生成しない（lazy init）
    - MT5 が未設定/初期化失敗でも例外で止めない（mt5=None で縮退）
    """
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is not None:
            return _SERVICE

        logger = logging.getLogger(__name__)

        mt5: MT5Client | None = None
        try:
            # 既存APIを優先（観測で確定した mt5_client.py の initialize/login/_get_client を使用）
            ok_init = bool(mt5_client.initialize())
            ok_login = bool(mt5_client.login()) if ok_init else False
            if ok_init and ok_login:
                try:
                    # mt5_client._get_client() は同モジュール内の既存シングルトン（privateだが観測済み）
                    mt5 = getattr(mt5_client, "_get_client")()
                except Exception:
                    mt5 = None
        except Exception as e:
            # 環境変数未設定などで落ち得るので握る（live は open_position のゲートで deny する）
            logger.info("[mt5] unavailable at service init (%s): %s", type(e).__name__, e)
            mt5 = None

        _SERVICE = TradeService(mt5_client=mt5, profile=None)
        return _SERVICE


def execute_decision(
    decision: Dict[str, Any],
    *,
    symbol: Optional[str] = None,
    service: Optional[TradeService] = None,
    dry_run: bool = False,
    run_id: Optional[int] = None,
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

    # action は top-level を優先し、無ければ decision_detail.action を参照（後方互換）
    action = decision.get("action")
    if action is None:
        _dd0 = decision.get("decision_detail")
        if isinstance(_dd0, dict):
            action = _dd0.get("action")
    if str(action or "").upper() != "ENTRY":
        # エントリー以外（SKIP/BLOCKED/TRAIL_UPDATE）はここでは何もしない
        return

    # side は top-level -> decision_detail -> signal の順で解決
    side = decision.get("side")
    if side is None:
        _dd1 = decision.get("decision_detail")
        if isinstance(_dd1, dict):
            side = _dd1.get("side")
    signal = decision.get("signal") or {}
    if side is None and isinstance(signal, dict):
        side = signal.get("side")
    if not side:
        # どっちに建てるか不明なら何もしない
        return

    atr_for_lot = None
    if isinstance(signal, dict):
        atr_for_lot = signal.get("atr_for_lot")

    svc = service or get_default_trade_service()

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

    # features を dict に正規化（フィルタ/倍率用）
    features_raw = None
    if isinstance(signal, dict):
        features_raw = signal.get("features")
    if features_raw is None:
        features_raw = decision.get("features")
    features: Dict[str, Any] = features_raw if isinstance(features_raw, dict) else {}

    # size_decision を “現実の決定フォーマット” から後方互換で取り込む（add-only）
    # 優先: decision_detail.size_decision -> meta.size_decision
    try:
        dd = decision.get("decision_detail")
        if isinstance(dd, dict) and "size_decision" in dd and isinstance(dd.get("size_decision"), dict):
            features.setdefault("size_decision", dd.get("size_decision"))
    except Exception:
        pass
    try:
        meta = decision.get("meta")
        if isinstance(meta, dict) and "size_decision" in meta and isinstance(meta.get("size_decision"), dict):
            features.setdefault("size_decision", meta.get("size_decision"))
    except Exception:
        pass

    svc.open_position(
        symbol=str(sym),
        side=str(side),
        lot=None,
        atr=float(atr_for_lot) if atr_for_lot is not None else None,
        features=features,
        dry_run=bool(dry_run),
        run_id=run_id,
    )


def can_open_new_position(symbol: Optional[str] = None) -> bool:
    settings = trade_state.get_settings()
    if not settings.trading_enabled:
        return False
    sym = symbol or load_config().get("runtime", {}).get("symbol")
    return get_default_trade_service().can_open(sym)


def decide_entry(p_buy: float, p_sell: float) -> Optional[str]:
    return get_default_trade_service().decide_entry(p_buy, p_sell)


def decide_entry_from_probs(p_buy: float, p_sell: float) -> dict:
    return get_default_trade_service().decide_entry_from_probs(p_buy, p_sell)


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
    get_default_trade_service().mark_order_inflight(order_id)


def on_order_result(order_id: str, ok: bool, symbol: str) -> None:
    get_default_trade_service().on_order_result(order_id=order_id, ok=ok, symbol=symbol)


def reconcile_positions(symbol: Optional[str] = None, desync_fix: bool = True) -> None:
    get_default_trade_service().on_broker_sync(symbol, fix=desync_fix)


def on_order_success(ticket: Optional[int], side: str, symbol: str, price: Optional[float] = None) -> None:
    get_default_trade_service().on_order_success(ticket=ticket, side=side, symbol=symbol, price=price)


def record_trade_result(
    *,
    symbol: str,
    side: str,
    profit_jpy: float,
    info: Optional[dict[str, Any]] = None,
) -> None:
    get_default_trade_service().record_trade_result(symbol=symbol, side=side, profit_jpy=profit_jpy, info=info)


def circuit_breaker_can_trade() -> bool:
    return get_default_trade_service().can_trade()
