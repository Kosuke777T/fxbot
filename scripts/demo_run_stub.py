# scripts/demo_run_stub.py
"""
ExecutionStub を使ったデモモードランナー

decisions.jsonl を数件だけ生成して挙動を確認するためのスクリプト。
MT5 のリアル発注は絶対に行いません。

【使用方法】
    # PowerShell 7 (プロジェクトルートから実行):
    # (.venv) PS D:\fxbot> python -m scripts.demo_run_stub

    または

    python scripts/demo_run_stub.py

【出力先】
    logs/decisions/decisions_USDJPY.jsonl にログが出力されます。
    (注: symbol "USDJPY-" は _symbol_to_filename() により "USDJPY" に変換される)

【動作確認コマンド】
    # 出力されたログを確認（最後の5件）
    # Get-Content logs\\decisions\\decisions_USDJPY.jsonl | Select-Object -Last 5

    # filter_reasons が含まれているか確認
    # Get-Content logs\\decisions\\decisions_USDJPY.jsonl | Select-String '"filter_reasons"'

    # filter_pass と filter_reasons の両方が含まれているか確認
    # Get-Content logs\\decisions\\decisions_USDJPY.jsonl | Select-String '"filter_pass"|"filter_reasons"'

    # 最新の1件を整形して表示
    # Get-Content logs\\decisions\\decisions_USDJPY.jsonl | Select-Object -Last 1 | ConvertFrom-Json | ConvertTo-Json -Depth 10
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import logger as app_logger
from app.core.config_loader import load_config
from app.services import circuit_breaker, trade_state
from app.services.execution_stub import ExecutionStub, reset_atr_gate_state
from app.services.ai_service import get_ai_service


def _build_dummy_features(tick_idx: int, expected_features: list[str] | None) -> Dict[str, float]:
    """
    expected_features に基づいてダミーの特徴量を生成する。
    モデルが期待する特徴量の数と順序に完全に合わせる。

    Parameters
    ----------
    tick_idx : int
        ティックのインデックス（変動を付けるために使用）
    expected_features : list[str] | None
        AISvc から取得した期待特徴量のリスト。None の場合は空の dict を返す。

    Returns
    -------
    Dict[str, float]
        expected_features のキーのみを含む特徴量辞書
    """
    if not expected_features:
        return {}

    import math
    drift = math.sin(tick_idx / 6.0)

    values: Dict[str, float] = {}
    for name in expected_features:
        base = 0.0

        # 特徴量名に基づいて適切なダミー値を設定
        if "atr" in name.lower():
            base = 0.02 + abs(drift) * 0.005  # ATR は 0.02 前後
        elif "vol" in name.lower():
            base = 0.8 + drift * 0.1  # ボラティリティ
        elif "trend" in name.lower():
            base = 0.1 + drift * 0.05  # トレンド強度
        elif "rsi" in name.lower():
            base = 50.0 + drift * 20.0  # RSI は 0-100
            base = max(0.0, min(100.0, base))
        elif "ema" in name.lower():
            if "ratio" in name.lower():
                base = 1.0 + drift * 0.002  # EMA 比率
            else:
                base = 150.0 * (1 + drift * 0.001)  # EMA 価格
        elif "ret" in name.lower():
            base = drift * 0.001  # リターン
        elif "range" in name.lower():
            base = 0.02 + abs(drift) * 0.005  # レンジ
        elif "adx" in name.lower():
            base = 20.0 + abs(drift) * 10.0  # ADX
            base = max(5.0, base)
        else:
            # その他の特徴量は 0.5 前後で設定
            base = 0.5 + drift * 0.1

        values[name] = float(base)

    return values


def _create_runtime_cfg(cfg: Dict[str, Any], symbol: str, prob_threshold: float, stub: ExecutionStub) -> Dict[str, Any]:
    """runtime_cfg を作成する"""
    runtime_cfg = cfg.get("runtime", {})
    entry_cfg = cfg.get("entry", {})
    filters_cfg = cfg.get("filters", {})

    spread_limit_pips = float(runtime_cfg.get("spread_limit_pips", runtime_cfg.get("spread_limit", 1.5)))
    max_positions = int(runtime_cfg.get("max_positions", 1))
    min_adx = float(filters_cfg.get("adx_min", 15.0))
    disable_adx_gate = bool(filters_cfg.get("adx_disable", False))
    min_atr_pct = float(filters_cfg.get("min_atr_pct", 0.0003))

    # ダミーのティックデータ（bid, ask）
    base_price = 150.0
    spread = 0.0005
    tick = (base_price - spread / 2, base_price + spread / 2)

    sell_threshold = max(min(1.0 - prob_threshold, 1.0), 0.0)
    settings = trade_state.get_settings()

    return {
        "threshold_buy": prob_threshold,
        "threshold_sell": sell_threshold,
        "prob_threshold": prob_threshold,
        "spread_limit_pips": spread_limit_pips,
        "max_positions": max_positions,
        "spread_pips": spread * 100,  # pips に変換（簡易）
        "open_positions": 0,
        "ai_threshold": stub.ai.threshold,
        "min_adx": min_adx,
        "disable_adx_gate": disable_adx_gate,
        "min_atr_pct": min_atr_pct,
        "tick": tick,
        "side_bias": settings.side_bias,
    }


def main() -> None:
    """デモモードランナーのメイン関数"""
    # ロガー設定
    app_logger.setup()

    print("=== Demo Run Stub Started ===")
    print("MT5 リアル発注は行われません。decisions.jsonl のみ出力されます。")
    print()

    # 設定読み込み
    cfg = load_config()
    runtime_cfg = cfg.get("runtime", {})
    entry_cfg = cfg.get("entry", {})

    # シンボルを 'USDJPY-' に統一
    symbol = "USDJPY-"

    # 確率閾値の取得
    prob_threshold = float(entry_cfg.get("prob_threshold", entry_cfg.get("threshold_buy", 0.60)))

    # trade_state の更新
    trade_state.update(
        trading_enabled=True,
        threshold_buy=prob_threshold,
        threshold_sell=max(min(1.0 - prob_threshold, 1.0), 0.0),
        prob_threshold=prob_threshold,
        side_bias=str(entry_cfg.get("side_bias", "auto") or "auto"),
    )

    # CircuitBreaker の初期化
    cb_cfg = cfg.get("circuit_breaker", {}) if isinstance(cfg, dict) else {}
    cb = circuit_breaker.CircuitBreaker(
        max_consecutive_losses=int(cb_cfg.get("max_consecutive_losses", 5)),
        daily_loss_limit_jpy=float(cb_cfg.get("daily_loss_limit_jpy", 0.0)),
        cooldown_min=int(cb_cfg.get("cooldown_min", 30)),
    )

    # AISvc の初期化と expected_features の取得（get_ai_service() を使用）
    expected_features: list[str] | None = None
    try:
        ai = get_ai_service()
        expected_features = ai.expected_features
        print(f"[Demo] AISvc model: {getattr(ai, 'model_name', 'unknown')}")
        if expected_features:
            print(f"[Demo] expected_features: {expected_features}")
            print(f"[Demo] expected_features len: {len(expected_features)}")
        else:
            print("[Demo] WARNING: expected_features is None. Using empty features dict.")
    except Exception as exc:
        print(f"[Demo] AISvc model loading failed → using dummy model: {exc}")

        # ダミー AISvc を作成（execution_stub.py の実装を参考）
        class DummyProbOut:
            def __init__(self, p_buy: float, p_sell: float, p_skip: float, meta: str = "dummy") -> None:
                self.p_buy = float(p_buy)
                self.p_sell = float(p_sell)
                self.p_skip = float(p_skip)
                self.meta = meta
                self.model_name = "dummy"
                self.calibrator_name = "dummy"
                self.features_hash = "dummy"

            def model_dump(self) -> dict:
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
                self.expected_features: list[str] | None = None  # ダミーでも None にする

            def predict(self, feats: dict) -> DummyProbOut:
                # ダミー確率を返す（テスト用）
                # 特徴量に基づいて少し変動させる
                import math
                atr = feats.get("atr_14", 0.02)
                rsi = feats.get("rsi_14", 50.0)

                # RSI に基づいて BUY/SELL 確率を変動
                buy_factor = (rsi - 30.0) / 40.0  # RSI 30-70 を 0-1 に変換
                buy_factor = max(0.0, min(1.0, buy_factor))

                p_buy = 0.35 + buy_factor * 0.25  # 0.35 〜 0.60
                p_sell = 0.25 + (1.0 - buy_factor) * 0.25  # 0.25 〜 0.50
                p_skip = 1.0 - p_buy - p_sell
                if p_skip < 0:
                    p_skip = 0.0
                    total = p_buy + p_sell
                    if total > 0:
                        p_buy /= total
                        p_sell /= total
                    else:
                        p_buy = 0.33
                        p_sell = 0.33
                        p_skip = 0.34
                return DummyProbOut(p_buy=p_buy, p_sell=p_sell, p_skip=p_skip, meta="demo")

        ai = DummyAISvc(threshold=prob_threshold)
        # ダミーの場合も expected_features は None のまま

    # ExecutionStub の初期化
    try:
        reset_atr_gate_state()
    except Exception:
        pass

    stub = ExecutionStub(cb=cb, ai=ai, no_metrics=True)  # demo モードでは metrics を書き込まない

    print(f"[Demo] Symbol: {symbol}")
    print(f"[Demo] Running {30} ticks...")
    print()

    # 作業2: 仮ポジション状態の管理
    sim_open_position = False
    sim_pos_until_tick = -1

    # 環境変数のチェック（sim_pos_guard バイパス用）
    import os
    debug_relax_pos_guard = os.getenv("FXBOT_DEBUG_RELAX_POS_GUARD", "").strip() in ("1", "true", "True", "on", "ON")

    # 擬似ポジションの保持tick数を環境変数で制御
    default_hold_ticks = 10
    sim_pos_hold_ticks = default_hold_ticks
    env_hold_ticks = os.getenv("FXBOT_SIM_POS_TICKS", "").strip()
    if env_hold_ticks:
        try:
            hold_val = int(env_hold_ticks)
            if hold_val >= 0:
                sim_pos_hold_ticks = hold_val
            else:
                print(f"[Demo] WARNING: FXBOT_SIM_POS_TICKS={env_hold_ticks} is negative, using default={default_hold_ticks}")
        except (ValueError, TypeError):
            print(f"[Demo] WARNING: FXBOT_SIM_POS_TICKS={env_hold_ticks} is invalid, using default={default_hold_ticks}")
    print(f"[Demo] Simulated position hold ticks: {sim_pos_hold_ticks}")

    # メインループ: 30 ticks をシミュレート
    for tick_idx in range(30):
        # 仮ポジションのタイムアウト処理
        if sim_open_position and tick_idx >= sim_pos_until_tick:
            sim_open_position = False
            print(f"[Tick {tick_idx:3d}] Simulated position closed (timeout)")

        # runtime_cfg の作成（各 tick ごとに再作成）
        runtime_payload = _create_runtime_cfg(cfg, symbol, prob_threshold, stub)
        # 仮ポジション状態を runtime_cfg の open_positions に設定（demo/live 共通）
        runtime_payload["open_positions"] = 1 if sim_open_position else 0
        # 保持tick数を runtime_cfg に追加（demo/live 共通、decisions.jsonl に記録される）
        runtime_payload["pos_hold_ticks"] = sim_pos_hold_ticks

        # expected_features に基づいてダミーの特徴量を生成
        features = _build_dummy_features(tick_idx, expected_features)

        # on_tick() を呼び出し（例外処理で1tickのエラーでもループ全体が止まらないようにする）
        try:
            result = stub.on_tick(symbol, features, runtime_payload)

            # 結果をログ出力
            decision = result.get("decision")
            if decision:
                action = decision.get("action", "UNKNOWN")
                # blocked は action から判定（action=BLOCKED の場合は blocked=True）
                blocked = action == "BLOCKED"
                reason = decision.get("reason", "")
                filter_pass = decision.get("filter_pass")
                filter_reasons = decision.get("filter_reasons", [])

                # シミュレーション用の単一ポジション保護: sim_pos=True の間は ENTRY を防ぐ
                # ただし、debug_relax_pos_guard が有効な場合はバイパスする（ENTRY を許可）
                if action == "ENTRY" and sim_open_position and not debug_relax_pos_guard:
                    # ENTRY 決定を BLOCKED に上書き（relax_pos_guard=0 の場合のみ防ぐ）
                    action = "BLOCKED"
                    reason = "sim_pos_guard"
                    decision["action"] = action
                    decision["reason"] = reason
                    # filters に sim_pos_guard 情報を追加
                    if "filters" not in decision:
                        decision["filters"] = {}
                    decision["filters"]["sim_pos_guard_hit"] = True
                    decision["filters"]["sim_pos_guard_reason"] = "already_in_simulated_position"
                    print(f"[Tick {tick_idx:3d}] ENTRY blocked by sim_pos_guard (sim_pos=True) -> action={action} reason={reason}")
                elif action == "ENTRY":
                    # ENTRY が出たら仮ポジションを開始
                    sim_open_position = True
                    sim_pos_until_tick = tick_idx + sim_pos_hold_ticks
                    print(f"[Tick {tick_idx:3d}] action={action:10s} reason={reason:20s} "
                          f"blocked={blocked} filter_pass={filter_pass} "
                          f"filter_reasons={filter_reasons} -> SIM POS OPEN (until tick {sim_pos_until_tick}, hold_ticks={sim_pos_hold_ticks})")
                else:
                    print(f"[Tick {tick_idx:3d}] action={action:10s} reason={reason:20s} "
                          f"blocked={blocked} filter_pass={filter_pass} "
                          f"filter_reasons={filter_reasons} "
                          f"sim_pos={sim_open_position}")
            else:
                # decision=None の場合は blocked は False
                blocked = False
                print(f"[Tick {tick_idx:3d}] decision=None, blocked={blocked} sim_pos={sim_open_position}")
        except Exception as exc:
            print(f"[Tick {tick_idx:3d}] ERROR: {exc}")
            import traceback
            traceback.print_exc()
            break  # エラーが発生したらループを終了（挙動を安定させる）

        # 短い待機時間（オプション）
        time.sleep(0.2)

    print()
    print("=== Demo Run Completed ===")
    print(f"decisions.jsonl の出力先: logs/decisions/decisions_USDJPY.jsonl")
    print(f"  (注: symbol '{symbol}' は _symbol_to_filename() により 'USDJPY' に変換される)")


if __name__ == "__main__":
    main()

