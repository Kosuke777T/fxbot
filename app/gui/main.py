import sys
import traceback
import threading
from typing import Optional, Dict, Any

from PyQt6.QtCore import QTimer, QObject
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QLabel,
)

from pathlib import Path

from app.core import logger as app_logger
from app.gui.control_tab import ControlTab
from app.gui.dashboard_tab_qt import DashboardTab
from app.gui.history_tab import HistoryTab
from app.services.execution_stub import evaluate_and_log_once
from app.gui.ai_tab import AITab
from app.gui.backtest_tab import BacktestTab
from app.gui.virtual_bt_tab import VirtualBTTab
from app.gui.kpi_tab import KPITab
from app.gui.settings_tab import SettingsTab
from app.gui.ops_tab import OpsTab
from app.gui.scheduler_tab import SchedulerTab
from app.gui.visualize_tab import VisualizeTab
from app.services.kpi_service import KPIService
from app.services.scheduler_facade import get_scheduler
from app.services.execution_service import ExecutionService
from app.services import trade_state, mt5_account_store, mt5_selftest
from app.core.config_loader import load_config
from app.core import market
from app.services.orderbook_stub import orderbook
from loguru import logger
from app.services.aisvc_loader import check_model_health_at_startup, set_last_model_health


class SchedulerTickRunner(QObject):
    """GUI起動中に JobScheduler.run_pending() を定期実行するランナー"""

    def __init__(self, parent=None, interval_ms: int = 10_000):
        super().__init__(parent)
        # scheduler_facade のシングルトンを使用（二重生成を防止）
        self._scheduler = get_scheduler()
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()
        logger.info("[GUI][scheduler] tick runner started interval_ms={} (using singleton scheduler)", interval_ms)

    def _on_tick(self):
        # 連続起動を防ぐ（run_pendingが重い可能性があるため）
        if self._lock.locked():
            return
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        with self._lock:
            try:
                self._scheduler.run_pending()
            except Exception as e:
                logger.exception("[GUI][scheduler] run_pending failed: {}", e)


class TradeLoopRunner(QObject):
    """
    自動売買ループを実行するランナー。
    起動点: start() が唯一。Controlタブ「取引ON」→ _trade_loop.start() のみから呼ばれる。
    """

    def __init__(self, parent=None, interval_ms: int = 3000):
        super().__init__(parent)
        self._exec_service = ExecutionService()
        self._lock = threading.Lock()
        self._is_running = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._cfg = None
        self._symbol = "USDJPY-"
        self._mt5_diag_logged = False
        self._test_entry_used = False

    def _emit_mt5_diag_once(self, *, dry_run: bool) -> None:
        """T-4: trade_loop開始直後のMT5診断ログ（重複防止）。"""
        if self._mt5_diag_logged:
            return
        self._mt5_diag_logged = True
        try:
            from app.services import trade_service as trade_service_mod

            diag = trade_service_mod.mt5_diag_snapshot(self._symbol)
        except Exception as e:
            diag = {"symbol": self._symbol, "mt5_last_error": f"diag_failed:{type(e).__name__}:{e}"}

        logger.info(
            "[MT5_DIAG] connected={} terminal_trade_allowed={} account_trade_allowed={} symbol={} resolved_symbol={} symbol_visible={} symbol_trade_mode={} dry_run={} mt5_last_error={}",
            diag.get("connected"),
            diag.get("terminal_trade_allowed"),
            diag.get("account_trade_allowed"),
            diag.get("symbol", self._symbol),
            diag.get("resolved_symbol"),
            diag.get("symbol_visible"),
            diag.get("symbol_trade_mode"),
            bool(dry_run),
            diag.get("mt5_last_error"),
        )

    def start(self) -> bool:
        """
        自動売買ループを開始する。既に実行中の場合は False を返す。
        【起動点】このメソッドが唯一。GUI・services・core で [trade_loop] started はここからのみ出力。
        """
        if self._is_running:
            logger.warning("[trade_loop] start denied reason=already_running")
            return False

        try:
            self._mt5_diag_logged = False
            self._test_entry_used = False
            # 設定を読み込む
            self._cfg = load_config()
            runtime_cfg = self._cfg.get("runtime", {})
            self._symbol = runtime_cfg.get("symbol", "USDJPY-")

            # モードとdry_runを取得
            mode = runtime_cfg.get("mode", "dryrun")
            settings = trade_state.get_settings()
            trading_enabled = bool(getattr(settings, "trading_enabled", False))
            effective_dry_run = (mode == "dryrun") or (not trading_enabled)

            # 開始ログを出力（T-61: 観測で即断できるよう 1 箇所のみ）
            logger.info(
                "[trade_loop] started mode={} dry_run={} symbol={}",
                mode,
                effective_dry_run,
                self._symbol,
            )
            # T-65: 開始直後の事前診断（1回だけ）。NG なら BLOCK して注文系に進まない
            from app.services import trade_service as trade_service_mod
            if not trade_service_mod.run_start_diagnosis(self._symbol):
                return False
            # T-4: started直後に1回だけ診断ログ（dry_run/liveどちらでも出す）
            self._emit_mt5_diag_once(dry_run=effective_dry_run)

            self._is_running = True
            self._timer.start()
            return True
        except Exception as e:
            logger.exception("[trade_loop] start failed: {}", e)
            self._is_running = False
            return False

    def stop(self, reason: str = "unknown") -> None:
        """
        自動売買ループを停止する。
        【停止処理の唯一の箇所】[trade_loop] stopped はここからのみ出力。GUI・補助・別スレッドでは出さない。
        """
        if not self._is_running:
            return

        logger.info("[trade_loop] stopping reason={} symbol={}", reason, self._symbol)
        self._timer.stop()
        self._is_running = False

        logger.info("[trade_loop] stopped reason={}", reason)

    def is_running(self) -> bool:
        """ループが実行中かどうかを返す。"""
        return self._is_running

    def test_entry_with_sl_tp(self) -> None:
        """
        1回だけのテストエントリー（SL/TP付き）。
        - dry_run/live は既存判定を尊重（runtime.mode != "live" は必ず dry_run）
        - trading_enabled=True のときのみ有効
        - MT5未接続の場合、live送信せず理由ログだけ
        """
        # 連打防止（1回だけ）
        if bool(getattr(self, "_test_entry_used", False)):
            logger.info("[TEST_ORDER] skipped reason=already_used")
            return

        # trading_enabled のみ許可
        settings = trade_state.get_settings()
        trading_enabled = bool(getattr(settings, "trading_enabled", False))
        if not trading_enabled:
            logger.info("[TEST_ORDER] skipped reason=trading_disabled")
            return

        # runtime.mode に従って dry_run を決定（既存判定）
        cfg = self._cfg if isinstance(self._cfg, dict) else load_config()
        runtime_cfg = cfg.get("runtime", {}) if isinstance(cfg, dict) else {}
        mode = runtime_cfg.get("mode", "dryrun")
        effective_dry_run = (mode != "live") or (not trading_enabled)

        symbol = self._symbol
        side = "BUY"
        lot = 0.01
        sl_pips = int(getattr(settings, "sl_pips", 10) or 10)
        tp_pips = int(getattr(settings, "tp_pips", 10) or 10)

        logger.info(
            "[TEST_ORDER] requested symbol={} side={} lot={} sl_pips={} tp_pips={} mode={} dry_run={}",
            symbol,
            side,
            float(lot),
            sl_pips,
            tp_pips,
            mode,
            bool(effective_dry_run),
        )

        # live の場合のみ、MT5接続/ティックを前提に SL/TP を価格へ変換（read-only）
        sl_price = None
        tp_price = None
        if not effective_dry_run:
            try:
                import MetaTrader5 as mt5  # type: ignore
                from app.core.symbol_map import resolve_symbol

                rs = resolve_symbol(symbol)

                # Settingsタブのテスト発注が動いているので、ここは svc._mt5 ではなく
                # MetaTrader5 側の initialize 可否で判定する（最小の副作用でIPC確立）。
                ok_init = bool(mt5.initialize())
                if not ok_init:
                    logger.info(
                        "[TEST_ORDER] skipped reason=mt5_init_failed last_error={}",
                        mt5.last_error(),
                    )
                    return

                # シンボルが未選択だと tick が取れない環境があるので best-effort で select
                try:
                    mt5.symbol_select(rs, True)
                except Exception:
                    pass

                t = mt5.symbol_info_tick(rs)
                if t is None:
                    logger.info(
                        "[TEST_ORDER] skipped reason=mt5_tick_unavailable last_error={}",
                        mt5.last_error(),
                    )
                    return
                price = float(getattr(t, "ask", 0.0) or 0.0)  # BUY は ask 基準
                sym_norm = str(rs).replace("-", "")
                pip_size = 0.01 if sym_norm.endswith("JPY") else 0.0001
                sl_price = price - float(sl_pips) * float(pip_size)
                tp_price = price + float(tp_pips) * float(pip_size)
            except Exception:
                try:
                    import MetaTrader5 as mt5  # type: ignore
                    err = mt5.last_error()
                except Exception:
                    err = None
                logger.info("[TEST_ORDER] skipped reason=mt5_tick_unavailable last_error={}", err)
                return
        else:
            # dry_run は必ずスキップ理由を出す（既存挙動は open_position 側が尊重）
            logger.info("[TEST_ORDER] skipped reason=dry_run")

        # TradeService の既存 open_position を使用（新規発注経路は作らない）
        try:
            from app.services import trade_service as trade_service_mod

            svc = trade_service_mod.get_default_trade_service()
            rt = trade_state.get_runtime()
            prev_ticket = getattr(rt, "last_ticket", None)

            svc.open_position(
                symbol=symbol,
                side=side,
                lot=float(lot),
                sl=sl_price,
                tp=tp_price,
                comment="intent=TEST source=test_button",
                features={"source": "test_button", "sl_pips": sl_pips, "tp_pips": tp_pips},
                dry_run=bool(effective_dry_run),
            )

            # live の場合のみ、結果を best-effort で観測ログ化
            if not effective_dry_run:
                rt2 = trade_state.get_runtime()
                new_ticket = getattr(rt2, "last_ticket", None)
                ok = bool(new_ticket) and (new_ticket != prev_ticket)
                logger.info("[TEST_ORDER] sent ok={} order_id={}", ok, new_ticket if ok else None)
                if ok:
                    self._test_entry_used = True
        except Exception as e:
            logger.info("[TEST_ORDER] skipped reason=exception error={}", e)
            return

    def _on_tick(self):
        """タイマーイベントハンドラ"""
        # 連続起動を防ぐ
        if self._lock.locked():
            return

        # trading_enabled をチェック
        settings = trade_state.get_settings()
        trading_enabled = bool(getattr(settings, "trading_enabled", False))
        if not trading_enabled:
            # trading_enabled が False になったら停止
            logger.info("[trade_loop] auto-stop trading_enabled={} symbol={}", trading_enabled, self._symbol)
            self.stop(reason="auto_stop_trading_disabled")
            return

        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        """実際のループ処理（別スレッドで実行）"""
        with self._lock:
            try:
                if not self._is_running:
                    return

                # 設定を再読み込み（必要に応じて）
                if self._cfg is None:
                    self._cfg = load_config()
                runtime_cfg = self._cfg.get("runtime", {})
                symbol = runtime_cfg.get("symbol", "USDJPY-")
                ai_cfg = self._cfg.get("ai", {}) if isinstance(self._cfg, dict) else {}

                # モードとdry_runを取得
                mode = runtime_cfg.get("mode", "dryrun")
                settings = trade_state.get_settings()
                trading_enabled = bool(getattr(settings, "trading_enabled", False))
                effective_dry_run = (mode == "dryrun") or (not trading_enabled)

                # 既存の特徴量生成ルートを使用（execution_stub._collect_features）
                try:
                    from app.services.execution_stub import _collect_features

                    # base_features を取得
                    base_features = tuple(ai_cfg.get("features", {}).get("base", []))

                    # spread_pips を取得（market から取得を試みるが、失敗しても続行）
                    spread_pips = 0.0
                    try:
                        spr_callable = getattr(market, "spread_pips", None)
                        if callable(spr_callable):
                            spread_pips = spr_callable(symbol)
                        elif hasattr(market, "spread"):
                            spr_callable2 = getattr(market, "spread", None)
                            if callable(spr_callable2):
                                spread_pips = spr_callable2(symbol)
                    except Exception:
                        spread_pips = 0.0

                    # オープンポジション数を取得
                    open_positions = 0
                    try:
                        ob_obj = orderbook() if callable(orderbook) else orderbook
                        get_maybe = getattr(ob_obj, "get", None)
                        ob = get_maybe(symbol) if callable(get_maybe) else None
                        if ob is not None:
                            cnt_getter = getattr(ob, "count_open", None)
                            if callable(cnt_getter):
                                open_positions = int(cnt_getter(symbol))
                    except Exception:
                        open_positions = 0

                    # tick は None で渡す（market.tick が存在しないため）
                    # _collect_features は tick=None でも動作する（デフォルト値で埋める）
                    tick = None

                    # 既存の正規ルートで特徴量を生成
                    features = _collect_features(
                        symbol=symbol,
                        base_features=base_features,
                        tick=tick,
                        spread_pips=spread_pips,
                        open_positions=open_positions,
                    )

                    # ExecutionService.execute_entry を呼び出す
                    logger.info(
                        "[trade_loop][tick] calling execute_entry symbol={} dry_run={}",
                        symbol,
                        effective_dry_run,
                    )
                    res = self._exec_service.execute_entry(
                        features=features,
                        symbol=symbol,
                        dry_run=effective_dry_run,
                    )
                    ok = bool(res.get("ok")) if isinstance(res, dict) else False
                    logger.info("[trade_loop][tick] execute_entry returned ok={}", ok)
                except Exception as e:
                    logger.exception("[trade_loop] tick failed: {}", e)
            except Exception as e:
                logger.exception("[trade_loop] run failed: {}", e)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FX AI Bot Control Panel")
        self.resize(980, 640)
        
        # 起動時にモデル健全性チェックを1回だけ実行（起動時のみ、tick処理中は呼ばない）
        health_result: Dict[str, Any] = {"stable": False, "score": 0.0, "reasons": ["startup_check_exception"], "meta": {}}
        try:
            health = check_model_health_at_startup()
            # 結果を正規化（stable/score/reasons/meta を保証）
            health_result = {
                "stable": health.get("stable", False),
                "score": health.get("score", 0.0),
                "reasons": health.get("reasons", []),
                "meta": health.get("meta", {}),
            }
            # services側の参照窓口に保存
            set_last_model_health(health_result)
        except Exception as e:
            # チェック自体が失敗してもアプリは落とさない（最重要の成功条件）
            # 失敗時は正規化された結果を保持
            health_result = {
                "stable": False,
                "score": 0.0,
                "reasons": [f"startup_check_exception: {type(e).__name__}"],
                "meta": {},
            }
            set_last_model_health(health_result)
            logger.error(
                "[model_health] check failed (app continues): {err}",
                err=type(e).__name__,
            )
        
        # ログ補強（1回だけ）
        stable = health_result["stable"]
        score = health_result["score"]
        reasons = health_result["reasons"]
        meta = health_result["meta"]
        model_path = meta.get("model_path", "n/a")
        trained_at = meta.get("trained_at", None)
        scaler_path = meta.get("scaler_path", None)
        
        log_parts = [f"stable={stable}", f"score={score:.1f}", f"reasons={reasons}"]
        if model_path != "n/a":
            log_parts.append(f"model_path={model_path}")
        if trained_at:
            log_parts.append(f"trained_at={trained_at}")
        if scaler_path:
            log_parts.append(f"scaler_path={scaler_path}")
        
        logger.info(f"[model_health] {' '.join(log_parts)}")

        # --- 口座プロファイル帯（タブの上・最上段、常時表示） ---
        self.account_banner = QLabel(self)
        self.account_banner.setStyleSheet("""
            QLabel {
                background-color: #6B7280;
                color: white;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }
        """)

        # --- モデル健全性バナー（タブの上に配置） ---
        self.health_banner = QLabel(self)
        self.health_banner.setStyleSheet("""
            QLabel {
                background-color: #F5F5F5;
                border: 1px solid #CCCCCC;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }
        """)
        self._update_health_banner(health_result)
        
        # --- QTabWidget をインスタンス変数として保持 ---
        self.tabs = QTabWidget(self)

        # === 1段目タブ（メインタブ）の色 + 角丸スタイル ===
        self.tabs.setStyleSheet("""
QTabBar::tab {
    background: #F0F0F0;              /* 非選択：薄い灰色 */
    padding: 6px 12px;
    border: 1px solid #CCCCCC;
    border-top-left-radius: 4px;      /* ← 角丸 */
    border-top-right-radius: 4px;     /* ← 角丸 */
}

QTabBar::tab:selected {
    background: #D7EEFF;              /* 選択：薄い水色 */
    border: 1px solid #A0C8E8;
}

QTabBar::tab:hover {
    background: #E5F4FF;
}
""")

        # まずは軽いタブだけ即座に生成
        self.tabs.addTab(DashboardTab(), "Dashboard")
        control_tab = ControlTab(main_window=self)
        self.control_tab = control_tab
        self.tabs.addTab(control_tab, "Control")
        self.tabs.addTab(HistoryTab(), "History")
        self.tabs.addTab(VisualizeTab(), "Visualize")

        # --- AIタブはプレースホルダを入れておき、実体は後で生成 ---
        # プレースホルダ用ウィジェット
        ai_placeholder = QWidget(self.tabs)
        ph_layout = QVBoxLayout(ai_placeholder)
        ph_label = QLabel("AIタブは選択時に読み込みます（起動を軽くするための仕様）", ai_placeholder)
        ph_label.setWordWrap(True)
        ph_layout.addStretch(1)
        ph_layout.addWidget(ph_label)
        ph_layout.addStretch(1)

        # プレースホルダタブを追加し、そのインデックスを保存
        self._ai_tab: Optional[AITab] = None
        self._ai_tab_index = self.tabs.addTab(ai_placeholder, "AI")

        # KPI サービスを生成（BacktestTab と KPITab で使用）
        self.kpi_service = KPIService(base_dir=Path("."))

        # 残りのタブを追加
        self.tabs.addTab(
            BacktestTab(
                parent=self,
                kpi_service=self.kpi_service,
                profile_name="michibiki_std",
            ),
            "Backtest"
        )
        self.tabs.addTab(
            VirtualBTTab(parent=self),
            "Virtual BT"
        )
        self.tabs.addTab(
            KPITab(
                parent=self,
                kpi_service=self.kpi_service,
                profile_name="michibiki_std",
            ),
            "運用KPI"
        )
        settings_tab = SettingsTab()
        self.tabs.addTab(settings_tab, "Settings")
        self.tabs.addTab(SchedulerTab(), "Scheduler")
        self.tabs.addTab(OpsTab(), "Ops")

        # バナーとタブを縦に配置するコンテナ
        central_container = QWidget(self)
        central_layout = QVBoxLayout(central_container)
        central_layout.setContentsMargins(4, 4, 4, 4)
        central_layout.setSpacing(4)
        central_layout.addWidget(self.account_banner)
        central_layout.addWidget(self.health_banner)
        central_layout.addWidget(self.tabs)
        
        # コンテナをメインウィンドウにセット
        self.setCentralWidget(central_container)

        # タブ切り替えシグナルにハンドラを接続
        self.tabs.currentChanged.connect(self._on_tab_changed)

        settings_tab.active_profile_changed.connect(self._update_account_banner)

        control_tab.mt5_login_toggle_requested.connect(self._on_mt5_login_toggle)

        # 口座帯を起動時の active_profile で初期表示
        self._update_account_banner()
        self._refresh_login_button()
        self._refresh_mt5_stats()
        # 未ログイン時は取引ボタンを押せない状態にする（UIと内部状態の整合）
        try:
            self.control_tab.set_trading_controls_enabled(mt5_selftest.is_mt5_connected())
        except Exception:
            pass

        # --- MT5口座ステータス：ログイン中だけ10秒更新 ---
        self._mt5_stats_timer = QTimer(self)
        self._mt5_stats_timer.setInterval(10_000)

        def _tick_mt5_stats():
            try:
                if mt5_selftest.is_mt5_connected():
                    self._refresh_mt5_stats()
                else:
                    # 未接続ならタイマー停止（ムダ撃ち防止）
                    if self._mt5_stats_timer.isActive():
                        self._mt5_stats_timer.stop()
            except Exception:
                # UI継続最優先：例外は握る
                pass

        self._mt5_stats_timer.timeout.connect(_tick_mt5_stats)

        app_logger.setup()

        # --- スケジューラTick起動（GUI起動中に run_pending() を定期実行） ---
        self._scheduler_tick = SchedulerTickRunner(self, interval_ms=10_000)

        # --- 取引ループランナー（取引ボタンで開始/停止） ---
        self._trade_loop = TradeLoopRunner(self, interval_ms=3000)

        # --- ★GUI軽量化：ドライランタイマーはデフォルト無効 ---
        ENABLE_DRYRUN_TIMER = False  # 必要になったら True に変更

        if ENABLE_DRYRUN_TIMER:
            self.timer = QTimer(self)

            def _tick_safe():
                try:
                    evaluate_and_log_once()
                except Exception:
                    print("[gui.timer] evaluate failed:\\n" + traceback.format_exc())

            self.timer.timeout.connect(_tick_safe)
            self.timer.start(3000)
            _tick_safe()

    def request_test_entry(self) -> None:
        """ControlTab のテストエントリーボタンからの橋渡し（UI層は判断しない）。"""
        try:
            if not hasattr(self, "_trade_loop") or self._trade_loop is None:
                logger.info("[TEST_ORDER] skipped reason=trade_loop_not_ready")
                return
            if not self._trade_loop.is_running():
                logger.info("[TEST_ORDER] skipped reason=trade_loop_not_running")
                return
            self._trade_loop.test_entry_with_sl_tp()
        except Exception as e:
            logger.info("[TEST_ORDER] skipped reason=exception error={}", e)

    def _on_tab_changed(self, index: int) -> None:
        """
        タブが切り替わったときに呼ばれる。
        初めて AI タブが選択されたときにだけ AITab を生成して差し替える。
        """
        # まだ AI タブを生成しておらず、かつ AI タブのインデックスが選択されたときだけ実行
        if self._ai_tab is None and index == self._ai_tab_index:
            # 本物の AITab を生成
            self._ai_tab = AITab()

            # いま入っているプレースホルダタブを削除し、
            # 同じ位置に AITab を挿入（インデックスも更新）
            self.tabs.removeTab(self._ai_tab_index)
            self._ai_tab_index = self.tabs.insertTab(index, self._ai_tab, "AI")

            # 念のため、フォーカスも AI タブに合わせておく
            self.tabs.setCurrentIndex(index)
    
    def _update_health_banner(self, health_result: Dict[str, Any]) -> None:
        """
        モデル健全性バナーの表示を更新する。
        
        Args:
            health_result: check_model_health_at_startup() の戻り値
        """
        try:
            stable = health_result.get("stable", False)
            score = health_result.get("score", 0.0)
            reasons = health_result.get("reasons", [])
            
            # reasons の整形（空なら "(none)"、複数なら "; " で連結）
            if not reasons:
                full_reasons_str = "(none)"
                display_reasons_str = "(none)"
            else:
                # 全文保持用
                full_reasons_str = "; ".join(str(r) for r in reasons)
                # 表示用（長い場合は省略）
                if len(full_reasons_str) > 80:
                    display_reasons_str = full_reasons_str[:77] + "..."
                else:
                    display_reasons_str = full_reasons_str
            
            # 表示テキスト（省略版を使用）
            text = f"Model health: stable={stable} score={score:.1f} reasons={display_reasons_str}"
            
            # バナーに設定
            self.health_banner.setText(text)
            # tooltip には全文を使用
            self.health_banner.setToolTip(f"Full reasons: {full_reasons_str}" if reasons else "No issues detected")
            
            # stable=False の場合は背景色を変える（視認性向上）
            if not stable:
                self.health_banner.setStyleSheet("""
                    QLabel {
                        background-color: #FFF3CD;
                        border: 1px solid #FFC107;
                        border-radius: 3px;
                        padding: 4px 8px;
                        font-size: 11px;
                    }
                """)
            else:
                self.health_banner.setStyleSheet("""
                    QLabel {
                        background-color: #F5F5F5;
                        border: 1px solid #CCCCCC;
                        border-radius: 3px;
                        padding: 4px 8px;
                        font-size: 11px;
                    }
                """)
        except Exception:
            # 例外は握る（表示失敗でもアプリは継続）
            self.health_banner.setText("Model health: (check failed)")
            self.health_banner.setToolTip("Health check failed to display")

    def _update_account_banner(self, profile_name: str | None = None) -> None:
        """
        口座プロファイル帯の表示を更新する。
        profile_name が None のときは mt5_account_store.load_config() から active_profile を読む。
        """
        try:
            if profile_name is None:
                cfg = mt5_account_store.load_config()
                profile_name = cfg.get("active_profile") or ""
            key = (profile_name or "").strip().lower()
            if key == "demo":
                text = "MT5口座: DEMO（デモ）"
                self.account_banner.setStyleSheet("""
                    QLabel {
                        background-color: #1E5AA8;
                        color: white;
                        border-radius: 3px;
                        padding: 4px 8px;
                        font-size: 11px;
                    }
                """)
            elif key == "real":
                text = "MT5口座: REAL（本番注意）"
                self.account_banner.setStyleSheet("""
                    QLabel {
                        background-color: #B3261E;
                        color: white;
                        border-radius: 3px;
                        padding: 4px 8px;
                        font-size: 11px;
                    }
                """)
            else:
                text = f"MT5口座: {profile_name or '(未設定)'}"
                self.account_banner.setStyleSheet("""
                    QLabel {
                        background-color: #6B7280;
                        color: white;
                        border-radius: 3px;
                        padding: 4px 8px;
                        font-size: 11px;
                    }
                """)
            if mt5_selftest.is_mt5_connected():
                text += " / ログイン中"
            self.account_banner.setText(text)
        except Exception:
            fail_text = "MT5口座: (取得失敗)"
            if mt5_selftest.is_mt5_connected():
                fail_text += " / ログイン中"
            self.account_banner.setText(fail_text)
            self.account_banner.setStyleSheet("""
                QLabel {
                    background-color: #6B7280;
                    color: white;
                    border-radius: 3px;
                    padding: 4px 8px;
                    font-size: 11px;
                }
            """)

    def _on_mt5_login_toggle(self) -> None:
        """
        Control タブのログイン/ログアウトボタン押下時。
        services 経由で接続状態を判定し、未接続なら connect_mt5、接続中なら disconnect_mt5 を呼ぶ。
        ログアウト時は取引ループ停止＋取引OFFへ強制復元する。
        """
        try:
            connected = mt5_selftest.is_mt5_connected()
            if connected:
                # ログアウト前：取引ループを必ず停止（reason を明示）
                try:
                    if hasattr(self, "_trade_loop") and self._trade_loop is not None and self._trade_loop.is_running():
                        self._trade_loop.stop(reason="mt5_logout")
                except Exception as e:
                    logger.warning("[GUI][MT5] trade_loop stop on logout: {}", e)
                mt5_selftest.disconnect_mt5()
                logger.info("[GUI][MT5] ログアウトしました")
                self.control_tab.set_trading_controls_enabled(False)
            else:
                if not mt5_selftest.connect_mt5():
                    logger.warning("[GUI][MT5] ログインに失敗しました（アクティブプロファイル未設定または initialize 失敗）")
                    return
                logger.info("[GUI][MT5] ログインしました")
                self.control_tab.set_trading_controls_enabled(True)
            self._update_account_banner()
            self._refresh_login_button()
            self._refresh_mt5_stats()

            # ログイン中だけ10秒更新を回す
            try:
                if mt5_selftest.is_mt5_connected():
                    if not self._mt5_stats_timer.isActive():
                        self._mt5_stats_timer.start()
                else:
                    if self._mt5_stats_timer.isActive():
                        self._mt5_stats_timer.stop()
            except Exception:
                pass
        except Exception as e:
            logger.exception("[GUI][MT5] ログイン/ログアウト処理でエラー: {}", e)

    def _refresh_login_button(self) -> None:
        """Control タブのログインボタン表記を接続状態に合わせて更新する。"""
        if not hasattr(self, "control_tab") or self.control_tab is None:
            return
        try:
            connected = mt5_selftest.is_mt5_connected()
            self.control_tab.login_btn.setText("ログアウト" if connected else "ログイン")
        except Exception:
            pass

    def _refresh_mt5_stats(self) -> None:
        """Control タブの口座ステータス表示を更新する（ログイン中は実値、未接続は --）。"""
        if not hasattr(self, "control_tab") or self.control_tab is None:
            return
        try:
            if not mt5_selftest.is_mt5_connected():
                self.control_tab.set_mt5_stats_text(
                    "残高: -- / 有効証拠金: -- / 余剰証拠金: -- / ポジション: --"
                )
                return

            snap = mt5_selftest.get_account_snapshot()
            if not snap.get("ok"):
                self.control_tab.set_mt5_stats_text(
                    "残高: -- / 有効証拠金: -- / 余剰証拠金: -- / ポジション: --"
                )
                return

            bal = snap.get("balance")
            eq = snap.get("equity")
            mf = snap.get("margin_free")
            npos = snap.get("positions")

            def _fmt(x):
                try:
                    return f"{float(x):,.0f}"
                except Exception:
                    return "--"

            txt = (
                f"残高: {_fmt(bal)} / 有効証拠金: {_fmt(eq)} / 余剰証拠金: {_fmt(mf)} / "
                f"ポジション: {npos if npos is not None else '--'}"
            )
            self.control_tab.set_mt5_stats_text(txt)
        except Exception:
            self.control_tab.set_mt5_stats_text(
                "残高: -- / 有効証拠金: -- / 余剰証拠金: -- / ポジション: --"
            )


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
