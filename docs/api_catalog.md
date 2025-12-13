# API Catalog
このファイルは tools/gen_api_catalog.py により自動生成されます。
## app/core/backtest/backtest_engine.py
- **class BacktestEngine**  (L20)  — v5.1 準拠のバックテストエンジン
  - **BacktestEngine.__init__(self, profile: str='michibiki_std', initial_capital: float=100000.0, contract_size: int=100000, filter_level: int=3)**  (L27)  — Parameters
  - **BacktestEngine._normalize_filter_ctx(self, filters_ctx: dict | None) -> dict**  (L69)  — Backtest 用 filters_ctx を v5.1 仕様に揃える:
  - **BacktestEngine.run(self, df: pd.DataFrame, out_dir: Path, symbol: str='USDJPY') -> Dict[str, Any]**  (L93)  — バックテストを実行する
  - **BacktestEngine._build_entry_context(self, row: pd.Series, timestamp: pd.Timestamp) -> Dict[str, Any]**  (L240)  — EntryContext を作成する
  - **BacktestEngine._build_decision(self, ai_out: Any, filter_pass: bool, filter_reasons: List[str], entry_context: Dict[str, Any]) -> Dict[str, Any]**  (L274)  — 決定を構築する
  - **BacktestEngine._build_decision_trace(self, timestamp: pd.Timestamp, symbol: str, ai_out: Any, decision: Dict[str, Any], entry_context: Dict[str, Any]) -> Dict[str, Any]**  (L332)  — decisions.jsonl 用のトレースを構築する
  - **BacktestEngine._normalize_for_json(self, obj: Any) -> Any**  (L401)  — JSON シリアライズ可能な形式に変換する
  - **BacktestEngine._normalize_for_json_recursive(self, obj: Any) -> Any**  (L428)  — JSON シリアライズ可能な形式に再帰的に変換する
  - **BacktestEngine._generate_outputs(self, df_features: pd.DataFrame, out_dir: Path, symbol: str) -> Dict[str, Any]**  (L450)  — 出力ファイルを生成する

## app/core/backtest/simulated_execution.py
- **class SimulatedTrade**  (L12)  — シミュレートされたトレード
- **class SimulatedExecution**  (L26)  — バックテスト用のシミュレート実行エンジン
  - **SimulatedExecution.__init__(self, initial_capital: float=100000.0, contract_size: int=100000)**  (L34)  — Parameters
  - **SimulatedExecution.open_position(self, side: str, price: float, timestamp: pd.Timestamp, lot: float=0.1, atr: Optional[float]=None, sl: Optional[float]=None, tp: Optional[float]=None) -> None**  (L49)  — ポジションを開く
  - **SimulatedExecution.close_position(self, price: float, timestamp: pd.Timestamp) -> Optional[SimulatedTrade]**  (L96)  — ポジションをクローズする
  - **SimulatedExecution.force_close_all(self, price: float, timestamp: pd.Timestamp) -> None**  (L133)  — 最終バーで強制的にすべてのポジションをクローズする
  - **SimulatedExecution.get_trades_df(self) -> pd.DataFrame**  (L140)  — トレード履歴をDataFrame形式で返す
  - **SimulatedExecution.get_equity_curve(self, timestamps: pd.Series, prices: pd.Series) -> pd.Series**  (L172)  — エクイティ曲線を生成する

## app/core/config_loader.py
- **def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]**  (L7)  — 
- **def load_config() -> Dict[str, Any]**  (L16)  — 

## app/core/data_finder.py
- **def _expand_path(p: str) -> Iterable[Path]**  (L8)  — 
- **def _load_csv(path: Path) -> Optional[pd.DataFrame]**  (L15)  — 
- **def resolve_csv(symbol: str, timeframe: str, search_paths: Iterable[str]) -> Tuple[Optional[Path], Optional[pd.DataFrame]]**  (L26)  — search_paths から {SYMBOL}_{TF}.csv を探して返す。最初に見つかったものを採用。

## app/core/edition.py
- **class EditionCapability**  (L17)  — 各エディションごとの「できる・できない」をまとめた能力値。
- **def _load_edition_yaml() -> Dict[str, Any]**  (L119)  — configs/edition.yaml を読み込んで dict を返す。
- **def get_current_edition_name() -> str**  (L140)  — edition.yaml の `edition` キーから現在のエディション名を取得。
- **def get_capability(name: Optional[str]=None) -> EditionCapability**  (L152)  — エディション名から EditionCapability を取得。
- **class EditionGuard**  (L173)  — アプリ全体から利用する「現在のエディション情報」のフロントエンド。
  - **EditionGuard.__init__(self, name: Optional[str]=None) -> None**  (L181)  — 
  - **EditionGuard.demo_only(self) -> bool**  (L190)  — 
  - **EditionGuard.lot_limit(self) -> Optional[float]**  (L194)  — 
  - **EditionGuard.scheduler_jobs_max(self) -> Optional[int]**  (L198)  — 
  - **EditionGuard.diagnosis_level(self) -> str**  (L202)  — 
  - **EditionGuard.filter_level(self) -> str**  (L206)  — 
  - **EditionGuard.shap_limit(self) -> Optional[int]**  (L210)  — 
  - **EditionGuard.fi_limit(self) -> Optional[int]**  (L214)  — 
  - **EditionGuard.profile_multi(self) -> bool**  (L218)  — 
  - **EditionGuard.profile_auto_switch(self) -> bool**  (L222)  — 
- **def get_guard() -> EditionGuard**  (L227)  — アプリ全体から使うためのシングルトン EditionGuard。
- **def _print_capabilities_table() -> None**  (L239)  — 
- **def _print_current() -> None**  (L245)  — 

## app/core/filter/strategy_filter_engine.py
- **class FilterConfig**  (L10)  — フィルタエンジンの設定
- **class StrategyFilterEngine**  (L25)  — ミチビキ v5.1 フィルタエンジン（コア層）
  - **StrategyFilterEngine.__init__(self, config: FilterConfig | None=None)**  (L39)  — 初期化
  - **StrategyFilterEngine.evaluate(self, ctx: Dict, filter_level: int) -> Tuple[bool, List[str]]**  (L49)  — エントリー可否を評価する
  - **StrategyFilterEngine._check_time_window(self, ctx: Dict) -> bool**  (L130)  — 取引時間帯フィルタ
  - **StrategyFilterEngine._check_atr(self, ctx: Dict) -> bool**  (L149)  — ATR フィルタ
  - **StrategyFilterEngine._check_volatility(self, ctx: Dict) -> bool**  (L167)  — ボラティリティ帯フィルタ
  - **StrategyFilterEngine._check_trend(self, ctx: Dict) -> bool**  (L187)  — トレンド強度フィルタ
  - **StrategyFilterEngine._check_losing_streak(self, ctx: Dict, reasons: List[str]) -> bool**  (L201)  — 連敗回避フィルタ（T-21 本番仕様）:
  - **StrategyFilterEngine._check_profile_autoswitch(self, ctx: dict, reasons: list[str]) -> None**  (L252)  — profile_stats から最適プロファイルを推奨し、

## app/core/logger.py
- **def setup()**  (L9)  — Loguru ロガーの共通設定

## app/core/market.py
- **def _pip_from_symbol_info(si: Any) -> float**  (L5)  — 
- **def select_symbol(symbol: str) -> bool**  (L12)  — 
- **def spread_pips(symbol: str) -> Optional[float]**  (L21)  — 現在のスプレッドを pips 単位で返す。
- **def tick(symbol: str) -> Optional[Tuple[float, float]]**  (L53)  — (bid, ask) を返す。
- **def pips_to_price(symbol: str, pips: float) -> Optional[float]**  (L74)  — 

## app/core/mt5_client.py
- **class TickSpec**  (L29)  — 
- **class MT5Client**  (L34)  — MT5 発注・接続ラッパー（最小構成）
  - **MT5Client.__init__(self, login: int, password: str, server: str, timeout: float=5.0)**  (L37)  — 
  - **MT5Client.initialize(self) -> bool**  (L48)  — MT5ターミナルの初期化（ログインは login_account()）
  - **MT5Client.login_account(self) -> bool**  (L62)  — 設定されたログイン情報で MT5.login() を実行
  - **MT5Client.shutdown(self)**  (L81)  — MT5 をシャットダウン
  - **MT5Client.order_send(self, symbol: str, order_type: str, lot: float, sl: Optional[float]=None, tp: Optional[float]=None, retries: int=3) -> Optional[int]**  (L90)  — 成行発注（BUY / SELL）
  - **MT5Client.close_position(self, ticket: int, symbol: str, retries: int=3) -> bool**  (L207)  — 指定チケットの成行クローズ
  - **MT5Client.get_positions(self)**  (L261)  — 
  - **MT5Client.get_positions_by_symbol(self, symbol: str)**  (L272)  — 
  - **MT5Client.get_positions_df(self, symbol: Optional[str]=None)**  (L278)  — 
  - **MT5Client.get_equity(self) -> float**  (L310)  — 現在口座の有効証拠金（equity）を返す。
  - **MT5Client.get_tick_spec(self, symbol: str) -> TickSpec**  (L318)  — 指定シンボルの tick_size / tick_value を返す。
- **def _get_env(name: str) -> str**  (L348)  — 必須の環境変数を取得（なければ RuntimeError）
- **def _get_client() -> MT5Client**  (L360)  — 環境変数 MT5_LOGIN / PASSWORD / SERVER から MT5Client の
- **def initialize() -> bool**  (L381)  — scripts/selftest_mt5.py などから呼ばれる想定のラッパー。
- **def login() -> bool**  (L390)  — 必要なら MT5Client.login_account() を呼ぶためのラッパー。
- **def shutdown() -> None**  (L396)  — MT5 のシャットダウンラッパー。
- **def get_account_info()**  (L413)  — アカウント情報を取得するラッパー。
- **def get_positions()**  (L425)  — オープンポジション一覧（Rawのリスト）を返すラッパー。
- **def get_positions_df(symbol: Optional[str]=None)**  (L433)  — オープンポジションを pandas.DataFrame で返すラッパー。
- **def get_equity() -> float**  (L441)  — 有効証拠金（equity）を float で返すラッパー。
- **def get_tick_spec(symbol: str) -> TickSpec**  (L449)  — 指定シンボルの TickSpec を返すラッパー。

## app/core/strategy_profile.py
- **class StrategyProfile**  (L25)  — 戦略プロファイル 1 件分の定義。
  - **StrategyProfile.backtest_root(self) -> Path**  (L59)  — バックテスト結果を置くルートフォルダを返す。
  - **StrategyProfile.monthly_returns_path(self) -> Path**  (L68)  — monthly_returns.csv の標準パス。
  - **StrategyProfile.compute_lot_size_from_atr(self, *, equity: float, atr: float, tick_value: float, tick_size: float, expected_trades_per_month: int=40, worst_case_trades_for_dd: int=10, avg_r_multiple: float=0.6, min_lot: float=0.01, max_lot: float=1.0) -> LotSizingResult**  (L75)  — このプロファイルに設定された target_monthly_return / max_monthly_dd / atr_mult_sl を使用して
- **def get_profile(name: str='michibiki_std') -> StrategyProfile**  (L150)  — プロファイル ID から StrategyProfile を取得する。
- **def list_profiles() -> Dict[str, StrategyProfile]**  (L163)  — 定義済みプロファイル一覧を dict で返す（読み取り専用想定）。

## app/core/symbol_map.py
- **def _load_preferred_map() -> Dict[str, str]**  (L13)  — 
- **def resolve_symbol(pair: str) -> str**  (L29)  — 'USDJPY' のような論理ペア名を、実際のMT5シンボル名に解決する。

## app/core/trade/decision_logic.py
- **class SignalDecision**  (L12)  — 
- **def decide_signal(prob_buy: Optional[float], prob_sell: Optional[float], best_threshold: float) -> SignalDecision**  (L22)  — best_threshold に基づくシグナル判定を共通化する関数。

## app/gui/ai_tab.py
- **class AITab**  (L34)  — 
  - **AITab.__init__(self, ai_service: AISvc | None=None, parent: QWidget | None=None) -> None**  (L35)  — 
  - **AITab.refresh_kpi(self) -> None**  (L177)  — recent_kpi.compute_recent_kpi_from_decisions を呼び出し、
  - **AITab.refresh_model_metrics(self) -> None**  (L224)  — モデル指標ウィジェットを再読込する。

## app/gui/backtest_tab.py
- **def _thousands(x, pos)**  (L28)  — 
- **def _find_trades_csv(equity_csv: Path)**  (L34)  — equity_curve.csv と同じフォルダにある trades*.csv を探す（優先: test -> train -> trades）
- **def plot_equity_with_markers_to_figure(fig: Figure, csv_path: str, note: str='')**  (L46)  — equity_curve.csv を描画。signal変化点でマーク。変化が無ければ trades*.csv の entry_time でマーク。
- **class PlotWindow**  (L147)  — 
  - **PlotWindow.__init__(self, parent=None)**  (L148)  — 
  - **PlotWindow.plot_equity_csv(self, csv_path: str)**  (L169)  — 
  - **PlotWindow.overlay_wfo_equity(self, df_train: Optional[pd.DataFrame], df_test: Optional[pd.DataFrame]) -> None**  (L233)  — Overlay WFO train/test equity lines on this window's axes.
  - **PlotWindow.plot_price_preview(self, csv_path: str, note: str='')**  (L294)  — 
  - **PlotWindow.plot_heatmap(self, df, note: str='')**  (L325)  — tools/backtest_run が生成する monthly_returns_*.csv の形式に対応したヒートマップ描画。
  - **PlotWindow.jump_range(self, mode: str) -> None**  (L425)  — ポップアウト表示で 1W / 1M / ALL の X 範囲を切り替える。
- **class BacktestTab**  (L590)  — 
  - **BacktestTab.__init__(self, parent: QtWidgets.QWidget | None=None, kpi_service: Optional[Any]=None, profile_name: str='michibiki_std') -> None**  (L591)  — 
  - **BacktestTab._on_progress_timer(self)**  (L791)  — 
  - **BacktestTab._append_progress(self, text: str)**  (L800)  — 
  - **BacktestTab._on_mode_changed(self, checked: bool=False)**  (L803)  — 
  - **BacktestTab._current_mode_text(self) -> str**  (L808)  — UI 上のモード文字列を返す（Backtest / Walk-Forward / Overlay）。
  - **BacktestTab._find_latest_wfo_dir(self) -> Optional[pathlib.Path]**  (L816)  — 
  - **BacktestTab._load_latest_wfo_data(self) -> Optional[Dict[str, object]]**  (L828)  — 
  - **BacktestTab._update_wfo_stats_panel(self, metrics: Dict[str, object]) -> None**  (L850)  — 
  - **BacktestTab._overlay_wfo_equity(self, df_train: Optional[pd.DataFrame], df_test: Optional[pd.DataFrame]) -> None**  (L875)  — Overlay Train/Test equity lines onto the current plot axes.
- **class _WFOResult**  (L924)  — Walk-Forward 検証の結果セットをまとめて持つだけの小さな入れ物。
  - **BacktestTab._WFOResult.__init__(self, report_json: Dict[str, Any], equity_train: Optional[pd.DataFrame], equity_test: Optional[pd.DataFrame], run_id: str, parent: Optional[QtCore.QObject]=None) -> None**  (L933)  — 
  - **BacktestTab._find_latest_wfo_files(self) -> Optional['_WFOResult']**  (L951)  — logs/retrain/ 配下から最新の report_*.json を探し、
  - **BacktestTab._debug_print_wfo_summary(self, wfo: '_WFOResult') -> None**  (L1024)  — とりあえず「ちゃんと読めたか」を確認するために、
  - **BacktestTab._load_model_info(self)**  (L1046)  — 
  - **BacktestTab._update_data(self)**  (L1061)  — 
  - **BacktestTab._run_test(self)**  (L1092)  — 
  - **BacktestTab._on_proc_ready_read_stdout(self)**  (L1178)  — 
  - **BacktestTab._on_proc_ready_read_stderr(self)**  (L1219)  — 
  - **BacktestTab._on_proc_finished(self, code: int, status: QtCore.QProcess.ExitStatus, sym: str, tf: str, mode: str)**  (L1258)  — 
  - **BacktestTab._pick_file(self)**  (L1351)  — 
  - **BacktestTab._save_png(self)**  (L1357)  — 
  - **BacktestTab._export_result_json(self)**  (L1366)  — 
  - **BacktestTab._show_heatmap(self)**  (L1394)  — 
  - **BacktestTab._pop_out(self)**  (L1428)  — 
  - **BacktestTab._load_plot(self, path_or_csv)**  (L1450)  — 
  - **BacktestTab._load_metrics(self, metrics_path: Path)**  (L1497)  — 
  - **BacktestTab._on_range_jump(self, mode: str) -> None**  (L1540)  — Backtestタブのインライン描画と、ポップアウト済みウィンドウの両方に期間ジャンプを適用する。

## app/gui/control_tab.py
- **class ControlTab**  (L20)  — 
  - **ControlTab.__init__(self, parent=None)**  (L21)  — 
  - **ControlTab._sync_from_state(self)**  (L98)  — 
  - **ControlTab._refresh_status(self)**  (L110)  — 
  - **ControlTab._toggle_trading(self)**  (L118)  — 
  - **ControlTab._on_thr_changed(self, *_)**  (L131)  — 
  - **ControlTab._on_exit_changed(self, *_)**  (L139)  — 
  - **ControlTab._close_all_mock(self)**  (L143)  — 
  - **ControlTab._cb_reset(self)**  (L148)  — 

## app/gui/dashboard_tab.py
- **class DashboardTab**  (L22)  — Realtime Metrics (ATR / Grace / Trail) を表示するだけの軽量パネル。
  - **DashboardTab.__init__(self, master, *args, **kwargs)**  (L27)  — 
  - **DashboardTab._refresh_metrics(self)**  (L72)  — 

## app/gui/dashboard_tab_qt.py
- **class DashboardTab**  (L10)  — PyQt6版 Dashboard。runtime/metrics.json を1秒ごとに再読込し、
  - **DashboardTab.__init__(self, parent: QtWidgets.QWidget | None=None) -> None**  (L15)  — 
  - **DashboardTab._refresh_metrics(self) -> None**  (L66)  — 
  - **DashboardTab._set(self, key: str, val: str) -> None**  (L95)  — 

## app/gui/history_tab.py
- **class HistoryTab**  (L13)  — 
  - **HistoryTab.__init__(self, parent=None)**  (L14)  — 
  - **HistoryTab.refresh(self) -> None**  (L43)  — 
  - **HistoryTab._export_csv(self) -> None**  (L52)  — 

## app/gui/kpi_tab.py
- **class KPITab**  (L11)  — 運用KPIタブ（メインタブ）
  - **KPITab.__init__(self, parent: Optional[QWidget]=None, kpi_service: Optional[KPIService]=None, profile_name: str='michibiki_std') -> None**  (L19)  — 
  - **KPITab.refresh(self, profile: Optional[str]=None) -> None**  (L46)  — KPIダッシュボードを更新する。

## app/gui/main.py
- **class MainWindow**  (L29)  — 
  - **MainWindow.__init__(self) -> None**  (L30)  — 
  - **MainWindow._on_tab_changed(self, index: int) -> None**  (L123)  — タブが切り替わったときに呼ばれる。
- **def main() -> None**  (L142)  — 

## app/gui/settings_tab.py
- **class SettingsTab**  (L25)  — MT5 口座設定タブ。
  - **SettingsTab.__init__(self, parent: Optional[QWidget]=None) -> None**  (L33)  — 
  - **SettingsTab._setup_ui(self) -> None**  (L42)  — 
  - **SettingsTab._load_profiles(self) -> None**  (L124)  — 設定ファイルからプロファイル一覧を読み込み、コンボボックスに反映。
  - **SettingsTab._apply_profile_to_fields(self, profile_name: str) -> None**  (L154)  — 指定プロファイルの情報を入力欄に反映。
  - **SettingsTab._on_profile_changed(self, name: str) -> None**  (L174)  — 
  - **SettingsTab._on_save_clicked(self) -> None**  (L177)  — 
  - **SettingsTab._on_switch_clicked(self) -> None**  (L203)  — 
  - **SettingsTab._on_selftest_clicked(self) -> None**  (L237)  — 「MT5 接続テスト（自己診断）」ボタン押下時のハンドラ。
  - **SettingsTab._on_orderflow_selftest_clicked(self) -> None**  (L286)  — 「テスト発注（selftest_order_flow）」ボタン押下時のハンドラ。
  - **SettingsTab._refresh_active_label(self) -> None**  (L340)  — 

## app/gui/widgets/diagnosis_ai_widget.py
- **class DiagnosisAIWidget**  (L9)  — 
  - **DiagnosisAIWidget.__init__(self, parent=None)**  (L10)  — 
  - **DiagnosisAIWidget.update_data(self, data: Any) -> None**  (L60)  — 診断結果を各タブに反映する
  - **DiagnosisAIWidget._update_time_of_day_tab(self, stats: Any) -> None**  (L79)  — 
  - **DiagnosisAIWidget._update_winning_tab(self, data: Any) -> None**  (L134)  — 
  - **DiagnosisAIWidget._update_dd_tab(self, data: Any) -> None**  (L205)  — 
  - **DiagnosisAIWidget._update_anomaly_tab(self, data: Any) -> None**  (L262)  — 

## app/gui/widgets/feature_importance.py
- **class FeatureImportanceWidget**  (L15)  — 
  - **FeatureImportanceWidget.__init__(self, ai_service, parent: Optional[QtWidgets.QWidget]=None)**  (L16)  — 
  - **FeatureImportanceWidget.refresh(self)**  (L70)  — 
  - **FeatureImportanceWidget._plot_current(self)**  (L101)  — 
  - **FeatureImportanceWidget._fill_table(self, df: pd.DataFrame)**  (L116)  — 
  - **FeatureImportanceWidget._render_empty(self)**  (L158)  — 
  - **FeatureImportanceWidget._load_alias(self) -> Dict[str, str]**  (L166)  — 

## app/gui/widgets/future_scenario_widget.py
- **class FutureScenarioWidget**  (L9)  — 
  - **FutureScenarioWidget.__init__(self, parent=None) -> None**  (L10)  — 
  - **FutureScenarioWidget.update_data(self, data: Optional[dict]) -> None**  (L20)  — 

## app/gui/widgets/kpi_dashboard.py
- **class KPIDashboardWidget**  (L24)  — 正式KPIダッシュボード（v5.1 仕様準拠）
  - **KPIDashboardWidget.__init__(self, profile: str='std', parent: Optional[QWidget]=None) -> None**  (L35)  — 
  - **KPIDashboardWidget.refresh(self) -> None**  (L124)  — KPIServiceからデータを取得してダッシュボードを更新
  - **KPIDashboardWidget._show_no_data(self) -> None**  (L139)  — データがない場合の表示
  - **KPIDashboardWidget._show_data(self, data: dict) -> None**  (L152)  — データがある場合の表示
  - **KPIDashboardWidget.set_trade_stats(self, win_rate: float, pf: float, avg_rr: float, total_trades: int) -> None**  (L192)  — トレード統計を表示する。
  - **KPIDashboardWidget._draw_chart(self, data: dict) -> None**  (L215)  — 12ヶ月折れ線グラフを描画

## app/gui/widgets/model_info_widget.py
- **def get_model_info()**  (L9)  — get_model_metrics() のエイリアス（後方互換性のため）
- **class ModelInfoWidget**  (L14)  — モデル指標表示ウィジェット
  - **ModelInfoWidget.__init__(self, parent=None)**  (L17)  — 
  - **ModelInfoWidget.update_view(self) -> None**  (L47)  — ラベル更新用メソッド
  - **ModelInfoWidget.reload(self) -> None**  (L73)  — モデル情報を再読込して表示を更新

## app/gui/widgets/monthly_dashboard.py
- **class MonthlyDashboardGroup**  (L8)  — 月次3%ダッシュボード（AIタブ用）
  - **MonthlyDashboardGroup.__init__(self, parent: QWidget | None=None) -> None**  (L11)  — 
  - **MonthlyDashboardGroup.refresh(self) -> None**  (L35)  — 今月リターン（backtest + live があれば）と3%目標の比較、グラフ更新

## app/gui/widgets/monthly_returns_widget.py
- **class MonthlyReturnsWidget**  (L15)  — backtests/{profile}/monthly_returns.csv を読み込んで、
  - **MonthlyReturnsWidget.__init__(self, parent: Optional[QtWidgets.QWidget]=None) -> None**  (L27)  — 
  - **MonthlyReturnsWidget._load_monthly_df(self) -> Optional[pd.DataFrame]**  (L57)  — StrategyProfile から monthly_returns.csv を探して読み込む。
  - **MonthlyReturnsWidget.refresh(self) -> None**  (L105)  — monthly_returns.csv を読み込み直してグラフを更新。

## app/gui/widgets/shap_bar.py
- **class ShapBarWidget**  (L18)  — AISvc.get_shap_top_features() の結果を棒グラフ＋テーブルで表示するウィジェット。
  - **ShapBarWidget.__init__(self, ai_service: AISvc, parent: Optional[QtWidgets.QWidget]=None) -> None**  (L23)  — 
  - **ShapBarWidget.refresh(self, force: bool=False) -> None**  (L93)  — AISvc.get_shap_top_features() を呼び出して再描画する。
  - **ShapBarWidget._plot(self, df: pd.DataFrame) -> None**  (L128)  — SHAPグローバル重要度の水平棒グラフを描画。
  - **ShapBarWidget._fill_table(self, df: pd.DataFrame) -> None**  (L149)  — テーブルに SHAP 順位を表示。
  - **ShapBarWidget._update_top3(self, df: Optional[pd.DataFrame]) -> None**  (L174)  — 上位3特徴量の簡易サマリをラベルに表示。
  - **ShapBarWidget._render_empty(self, message: str) -> None**  (L206)  — データが無い or エラー時の簡単な表示。
  - **ShapBarWidget._load_alias(self) -> Dict[str, str]**  (L225)  — configs/feature_alias.json から feature 名のエイリアスを読み出す。

## app/main_tk.py
- **def main() -> None**  (L6)  — 

## app/services/ai_service.py
- **def _safe_float(value: Any) -> Optional[float]**  (L26)  — 数値 or 数値っぽい文字列だけ float に変換し、それ以外は None を返す小さいユーティリティ。
- **def get_model_metrics(models_dir: str | Path='models') -> Dict[str, Any]**  (L38)  — active_model.json からモデル指標情報を取得する。
- **class AISvc**  (L139)  — 既存の推論サービス想定。モデル群は self.models に格納されている想定。
  - **AISvc.__init__(self) -> None**  (L145)  — 
  - **AISvc._normalize_features_for_model(self, feats: 'Mapping[str, float]') -> 'dict[str, float]'**  (L166)  — モデルの expected_features に合わせて特徴量を揃える。
  - **AISvc._sync_expected_features(self) -> None**  (L193)  — active_model.json / モデル本体から expected_features を
- **class ProbOut**  (L228)  — AISvc 内部で使うだけのシンプルな入出力コンテナ。
  - **AISvc.ProbOut.__init__(self, p_buy: float, p_sell: float, p_skip: float=0.0) -> None**  (L233)  — 
  - **AISvc._ensure_model_loaded(self) -> None**  (L238)  — self.models に推論用モデルが未ロードなら、active_model.json を見てロードする。
  - **AISvc.predict(self, X: np.ndarray | Dict[str, float], *, no_metrics: bool=False) -> 'AISvc.ProbOut'**  (L302)  — 単一サンプルの特徴量を受け取り、p_buy / p_sell / p_skip を返す。
  - **AISvc.get_feature_importance(self, method: str='gain', top_n: int=20, cache_sec: int=300) -> pd.DataFrame**  (L389)  — GUI から呼び出して Feature Importance を取得する API。
  - **AISvc._load_shap_background_features(self, max_rows: int=2000, *, csv_path: Path | None=None) -> pd.DataFrame**  (L477)  — SHAP計算用の背景特徴量を読み込むヘルパ。
  - **AISvc.get_shap_top_features(self, *, top_n: int=20, max_background: int=2000, csv_path: Path | None=None, cache_sec: int=300) -> pd.DataFrame**  (L519)  — LightGBMモデルに対する SHAP グローバル重要度（平均絶対SHAP）を計算し、
  - **AISvc.get_shap_values(self)**  (L631)  — SHAP 結果を EditionGuard に従って制限して返す。
  - **AISvc.feature_importance(self) -> pd.DataFrame**  (L687)  — FI を edition に応じて TopN で返す。
  - **AISvc.shap_summary(self) -> Dict[str, Any]**  (L741)  — SHAP を edition に応じて TopN で返す。
  - **AISvc.get_live_probs(self, symbol: str) -> dict[str, float]**  (L813)  — Live 用：execution_stub と同じ特徴量パイプラインを使って
  - **AISvc.build_decision_from_probs(self, probs: dict, symbol: str) -> dict**  (L920)  — Live 用：execution_stub の ENTRY/SKIP 判定を最小限で再現。
- **def get_ai_service() -> AISvc**  (L962)  — AISvc のシングルトンインスタンスを返す。

## app/services/aisvc_loader.py
- **class ActiveModelInfo**  (L10)  — 
  - **ActiveModelInfo.model_path(self) -> Path**  (L12)  — 
- **def load_active_model_meta() -> ActiveModelInfo | None**  (L16)  — 
- **def resolve_model_path() -> Path | None**  (L23)  — 
- **def load_model_for_inference()**  (L32)  — 

## app/services/circuit_breaker.py
- **class CBState**  (L12)  — 
- **class CircuitBreaker**  (L21)  — Resettable circuit breaker that combines consecutive-loss and daily-loss budgets
  - **CircuitBreaker.__init__(self, max_consecutive_losses: int=5, daily_loss_limit_jpy: float=0.0, cooldown_min: int=30)**  (L27)  — 
  - **CircuitBreaker.on_trade_result(self, profit_jpy: float) -> None**  (L41)  — Record a trade result and trip if thresholds are violated.
  - **CircuitBreaker.can_trade(self) -> bool**  (L60)  — Return True if trading is allowed (not tripped or cool-down finished).
  - **CircuitBreaker.reset(self) -> None**  (L69)  — Reset trip status (but keep daily accumulator).
  - **CircuitBreaker.status(self) -> dict**  (L76)  — Return a serialisable snapshot of the breaker state.
  - **CircuitBreaker._trip(self, reason: str) -> None**  (L90)  — 
  - **CircuitBreaker._rollover_if_new_day(self) -> None**  (L95)  — 

## app/services/data_guard.py
- **def csv_path(symbol_tag: str, timeframe: str, layout: str='per-symbol') -> Path**  (L10)  — symbol_tag は接尾辞なし（例: USDJPY）
- **def ensure_data(symbol_tag: str, timeframe: str, start_date: str, end_date: str, env: str='laptop', layout: str='per-symbol') -> Path**  (L19)  — 指定の [start_date, end_date] を満たすCSVが存在するか確認し、足りなければ scripts.make_csv_from_mt5 を呼んで追記する。

## app/services/decision_log.py
- **class DecisionRecord**  (L19)  — decisions_*.jsonl の 1 行を、GUI や KPI 計算から使いやすい形に薄くラップしたもの。
- **def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]**  (L35)  — JSONL ファイルを 1 行ずつ dict として返すジェネレータ。壊れた行はスキップ。
- **def _extract_decision_record(j: dict[str, Any]) -> DecisionRecord**  (L52)  — �� JSON ����AAI�^�u/KPI �ł悭�g���������������� DecisionRecord �����B
- **def _find_first_numeric_by_keys(container: Any, key_candidates: tuple[str, ...]) -> float | None**  (L110)  — 任意にネストした dict/list 構造の中から、
- **def _ensure_pnl_column(df: pd.DataFrame) -> pd.DataFrame**  (L138)  — decisions_* の生ログ DataFrame に「pnl 列」が無ければ、
- **def _get_decision_log_dir() -> Path**  (L178)  — 決定ログのルートディレクトリを返す。
- **def load_recent_decisions(limit: int | None=None) -> pd.DataFrame**  (L188)  — decisions_*.jsonl から最新の N レコードを pandas.DataFrame で読み込む。

## app/services/diagnosis_service.py
- **class DiagnosisParams**  (L14)  — 診断AIの入力条件（必要になれば拡張する）
- **class DiagnosisService**  (L22)  — 診断AIサービス（v0）
  - **DiagnosisService.analyze(self, profile: str='std', start=None, end=None) -> dict**  (L32)  — 診断AI v0:
  - **DiagnosisService._load_decision_records(self, start: Optional[date]=None, end: Optional[date]=None) -> List[Dict[str, Any]]**  (L78)  — decisions_*.jsonl を読み込んで、期間でフィルタリングしたレコードを返す。
  - **DiagnosisService._compute_time_of_day_stats(self, records: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]**  (L161)  — 時間帯ごとの勝率・PF・件数を計算する。
  - **DiagnosisService._compute_winning_conditions(self, records: List[Dict[str, Any]], time_of_day_stats: Dict[int, Dict[str, Any]]) -> Dict[str, Any]**  (L218)  — 全体の勝率と PF、時間帯ベースの「勝ちやすい条件」をまとめる v0 ロジック。
  - **DiagnosisService._compute_dd_pre_signal(self, decisions: List[Dict[str, Any]]) -> Dict[str, Any]**  (L302)  — decisions.jsonl（バックテスト or 実運用ログ）を元に
  - **DiagnosisService._compute_future_scenario(self, records: List[Dict[str, Any]]) -> Dict[str, Any]**  (L384)  — Expert 限定：来週のシナリオ（v1）
- **def get_diagnosis_service() -> DiagnosisService**  (L496)  — 

## app/services/edition_guard.py
- **class CapabilitySet**  (L18)  — ミチビキ v5 / v5.1 の CapabilitySet 定義。
- **def _project_root() -> Path**  (L102)  — services 層から見たプロジェクトルートを推定する。
- **def _load_edition_from_config() -> Optional[str]**  (L111)  — config/edition.json から edition を読み込む。
- **def _detect_edition() -> str**  (L133)  — 実際に使用する edition 名を決定する。
- **class EditionGuard**  (L164)  — v5.1 EditionGuard 本体。
  - **EditionGuard.__init__(self, edition: Optional[str]=None) -> None**  (L172)  — 
  - **EditionGuard.edition(self) -> str**  (L187)  — 
  - **EditionGuard.capabilities(self) -> CapabilitySet**  (L191)  — 
  - **EditionGuard.get_capability(self, name: str) -> Any**  (L196)  — 任意の Capability 値を取得する。
  - **EditionGuard.allow_real_account(self) -> bool**  (L206)  — 実口座トレードを許可するか。
  - **EditionGuard.scheduler_limit(self) -> int**  (L216)  — スケジューラのジョブ数上限を返す。
  - **EditionGuard.filter_level(self) -> int**  (L237)  — 
  - **EditionGuard.ranking_level(self) -> int**  (L240)  — 
- **def _default_guard() -> EditionGuard**  (L247)  — デフォルト EditionGuard をキャッシュして返す。
- **def get_capability(name: str) -> Any**  (L254)  — GUI/Service から直接呼ばれる想定のショートカット関数。
- **def allow_real_account() -> bool**  (L261)  — 
- **def scheduler_limit() -> int**  (L265)  — 
- **def filter_level() -> int**  (L269)  — 
- **def ranking_level() -> int**  (L273)  — 
- **def current_edition() -> str**  (L277)  — 旧 API が get_current_edition() などだった場合の互換用。

## app/services/event_store.py
- **class UiEvent**  (L23)  — 
- **def _now() -> str**  (L36)  — 
- **class _EventStore**  (L40)  — 
  - **_EventStore.__init__(self, maxlen: int=1000)**  (L41)  — 
  - **_EventStore.append(self, ev: UiEvent) -> None**  (L44)  — 
  - **_EventStore.add(self, **kwargs: Any) -> None**  (L50)  — 
  - **_EventStore.recent(self, n: int=200) -> List[UiEvent]**  (L54)  — 

## app/services/execution_service.py
- **def _symbol_to_filename(symbol: str) -> str**  (L34)  — シンボル名を安全なファイル名に変換
- **def _normalize_filter_reasons(reasons: Any) -> list[str]**  (L41)  — filter_reasons を必ず list[str] に正規化する（v5.1 仕様）
- **class DecisionsLogger**  (L65)  — 決定ログ専用のロガークラス
  - **DecisionsLogger.log(record: Dict[str, Any]) -> None**  (L69)  — ExecutionService 用 AI判断ログ (decisions.jsonl) を 1 レコード追記する。
- **class ExecutionService**  (L143)  — Live 用の実行サービス：
  - **ExecutionService.__init__(self)**  (L150)  — 初期化
  - **ExecutionService._build_entry_context(self, symbol: str, features: Dict[str, float], timestamp: Optional[datetime]=None) -> Dict[str, Any]**  (L158)  — EntryContext を構築する
  - **ExecutionService.process_tick(self, symbol: str, price: float, timestamp: datetime, features: Optional[Dict[str, float]]=None, dry_run: bool=False) -> Dict[str, Any]**  (L214)  — 1ティック分の処理をまとめて行うヘルパー。
  - **ExecutionService._apply_profile_autoswitch(self, symbol: str, reasons: list[str]) -> None**  (L267)  — フィルタ結果の reasons からプロファイル自動切替指示を読み取り、
  - **ExecutionService.execute_entry(self, features: Dict[str, float], *, symbol: Optional[str]=None, dry_run: bool=False, timestamp: Optional[datetime]=None) -> Dict[str, Any]**  (L310)  — 売買判断 → フィルタ判定 → decisions.jsonl 出力まで一貫処理
  - **ExecutionService.execute_exit(self, symbol: Optional[str]=None, dry_run: bool=False) -> Dict[str, Any]**  (L598)  — 決済監視/クローズ処理

## app/services/execution_stub.py
- **def _load_runtime_threshold(default: float=0.5) -> float**  (L53)  — 
- **def reset_atr_gate_state() -> None**  (L72)  — ???/????????ATR????????????
- **def _atr_gate_ok(atr_pct_now: float, runtime_cfg: Dict[str, Any]) -> bool**  (L79)  — Hysteresis-enabled ATR gate to avoid rapid flip-flops around thresholds.
- **def _tick_to_dict(tick: Any) -> Optional[Dict[str, float]]**  (L126)  — 
- **def _pip_size_for(symbol: str) -> float**  (L145)  — 
- **def _point_for(symbol: str) -> float**  (L148)  — 
- **def _mid_price(tick_dict: Optional[Dict[str, float]]) -> Optional[float]**  (L151)  — 
- **def _current_price_for_side(tick_dict: Optional[Dict[str, float]], side: str, price_source: str) -> Optional[float]**  (L156)  — 
- **def _register_trailing_state(symbol: str, signal: Dict[str, Any], tick_dict: Optional[Dict[str, float]], *, no_metrics: bool=False) -> None**  (L166)  — 
- **def _update_trailing_state(symbol: str, tick_dict: Optional[Dict[str, float]]) -> Optional[Dict[str, Any]]**  (L248)  — 
- **def _session_hour_allowed() -> bool**  (L320)  — config.session.allow_hours_jst ??????????????????????
- **def _symbol_to_filename(symbol: str) -> str**  (L349)  — 
- **def _write_decision_log(symbol: str, record: Dict[str, Any]) -> None**  (L354)  — 
- **def _ai_to_dict(ai_out: Any) -> Dict[str, Any]**  (L360)  — AISvc.predict() の戻り値（ProbOut など）を安全に dict 化する。
- **def _normalize_filter_reasons(reasons: Any) -> list[str]**  (L400)  — filter_reasons を必ず list[str] に正規化する（v5.1 仕様）
- **def _build_decision_trace(*, ts_jst: str, symbol: str, ai_out: 'ProbOut', cb_status: Dict[str, Any], filters_ctx: Dict[str, Any], decision: Dict[str, Any], prob_threshold: float, calibrator_name: str, entry_context: Optional[Dict[str, Any]]=None) -> Dict[str, Any]**  (L424)  — v5.1 仕様に準拠した決定トレースを構築する
- **def _collect_features(symbol: str, base_features: Tuple[str, ...], tick: Optional[Tuple[float, float]], spread_pips: Optional[float], open_positions: int) -> Dict[str, float]**  (L555)  — Live 用の軽量なフィーチャ生成。
- **class ExecutionStub**  (L628)  — ドライラン用の実行スタブ：
  - **ExecutionStub.__post_init__(self) -> None**  (L638)  — 
  - **ExecutionStub.on_tick(self, symbol: str, features: Dict[str, float], runtime_cfg: Dict[str, Any]) -> Dict[str, Any]**  (L654)  — 
- **def evaluate_and_log_once() -> None**  (L1234)  — Dry-run evaluation that mirrors the live decision path.
- **def debug_emit_single_decision() -> None**  (L1400)  — フィルタ + decisions.jsonl ログを 1 回だけテスト出力するデバッグ関数。

## app/services/feature_importance.py
- **class FeatureImportanceItem**  (L24)  — 1つの特徴量についての FI 情報.
- **def _unwrap_model(model: Any) -> Any**  (L33)  — CalibratedClassifierCV やラッパーに包まれている場合、
- **def _detect_model_type(model: Any) -> str**  (L45)  — LightGBM / XGBoost / その他 をざっくり判定する。
- **def _fi_lightgbm(model: Any, feature_names: Optional[Sequence[str]]=None) -> Tuple[np.ndarray, List[str]]**  (L91)  — LightGBM 用の raw FI 抽出.
- **def _fi_xgboost(model: Any, feature_names: Optional[Sequence[str]]=None) -> Tuple[np.ndarray, List[str]]**  (L148)  — XGBoost 用の raw FI 抽出.
- **def compute_feature_importance(model: Any, feature_names: Optional[Sequence[str]]=None, top_n: Optional[int]=30) -> List[FeatureImportanceItem]**  (L201)  — LightGBM / XGBoost モデルから Feature Importance を取り出し、

## app/services/filter_service.py
- **def _get_engine() -> StrategyFilterEngine**  (L15)  — StrategyFilterEngine のシングルトンを返す。
- **def evaluate_entry(entry_context: EntryContext) -> Tuple[bool, List[str]]**  (L27)  — Strategy / Execution から呼び出すための窓口。
- **def extract_profile_switch(reasons: Iterable[str]) -> Optional[Tuple[str, str]]**  (L60)  — filter_engine の reasons から 'profile_switch:std->aggr' 形式を拾って

## app/services/kpi_service.py
- **class KpiMonthlyRecord**  (L19)  — 
- **class KpiDashboard**  (L28)  — 
- **class KPIService**  (L37)  — バックテスト結果を元に KPI ダッシュボード用のデータを作るサービス.
  - **KPIService.__init__(self, backtest_root: Optional[Path]=None, base_dir: Optional[Path]=None) -> None**  (L40)  — backtest_root:
  - **KPIService.load_backtest_kpi_summary(self, profile: str) -> Dict[str, Any]**  (L66)  — バックテストKPIサマリを読み込む（仕様書 v5.1 準拠）。
  - **KPIService.compute_monthly_dashboard(self, profile: str) -> KpiDashboard**  (L152)  — 月次ダッシュボードデータを計算する（仕様書 v5.1 準拠）。
  - **KPIService.compute_target_progress(self, return_pct: float, target: float=TARGET_MONTHLY_RETURN) -> float**  (L229)  — 月3%に対する進捗率（0.0〜2.0=0〜200%）を返す。
  - **KPIService.compute_trade_stats(self, profile: str) -> dict**  (L272)  — バックテスト or 実運用のトレード結果から
  - **KPIService._load_monthly_returns(self, profile: str) -> pd.DataFrame**  (L360)  — BacktestRun が出力した monthly_returns.csv を読み込む。
  - **KPIService.load_monthly_returns(self, profile: str) -> pd.DataFrame**  (L413)  — 指定プロファイルの monthly_returns.csv を読み込んで返す。
  - **KPIService.refresh_monthly_returns(self, profile: str) -> pd.DataFrame**  (L421)  — BacktestRun が monthly_returns.csv を更新した後に呼び出す前提。

## app/services/loss_streak_service.py
- **class LossStreakState**  (L10)  — プロファイル×シンボルごとの連敗状態
- **def get_consecutive_losses(profile: str, symbol: str) -> int**  (L20)  — 現在の連敗数を返す。
- **def update_on_trade_result(profile: str, symbol: str, pl: float) -> int**  (L30)  — 取引結果を反映して連敗数を更新する。

## app/services/metrics.py
- **def publish_metrics(kv: Dict[str, Any]) -> None**  (L9)  — Dashboardが読むランタイム指標を KVS と JSON(atomic write) に出力する。

## app/services/mt5_account_store.py
- **def _default_config() -> Dict[str, Any]**  (L18)  — 設定ファイルが存在しない場合の初期値。
- **def load_config() -> Dict[str, Any]**  (L26)  — JSON 設定ファイルを読み込んで dict を返す。
- **def save_config(cfg: Dict[str, Any]) -> None**  (L47)  — 設定ファイルを保存する。
- **def get_profile(name: str) -> Optional[Dict[str, Any]]**  (L58)  — プロファイル名から設定を取得する。存在しなければ None。
- **def upsert_profile(name: str, *, login: int, password: str, server: str) -> None**  (L70)  — プロファイルを追加または更新する。
- **def set_active_profile(name: str, *, apply_env: bool=True) -> None**  (L86)  — アクティブプロファイルを変更する。
- **def get_active_profile_name() -> str**  (L108)  — 

## app/services/mt5_selftest.py
- **def _json_safe_str(s: object) -> str**  (L22)  — JSON安全な文字列に正規化する。
- **def _get_attr(obj: Any, name: str, default: Any='(n/a)') -> Any**  (L34)  — dict / MT5 の AccountInfo のどちらでも安全に属性を取り出すヘルパー。
- **def run_mt5_selftest() -> Tuple[bool, str]**  (L47)  — MT5 自己診断を実行して、(成功フラグ, ログ文字列) を返す。
- **def run_mt5_orderflow_selftest() -> Tuple[bool, str]**  (L163)  — scripts/selftest_order_flow.py をサブプロセスとして実行し、
- **def mt5_smoke(symbol: str='USDJPY-', lot: float=0.01, close_now: bool=True, dry: bool=False) -> Dict[str, Any]**  (L252)  — MT5 接続・テスト発注のスモークテストを実行し、結果を安全なdictで返す。

## app/services/mt5_service.py
- **class BrokerConstraints**  (L10)  — 
- **def _symbol_props(symbol: str) -> BrokerConstraints**  (L22)  — 
- **def _round_to_point(price: float, point: float) -> float**  (L40)  — 
- **def _sl_min_distance_ok(side: str, price_now: float, sl_price: float, min_points: int, point: float) -> bool**  (L44)  — 
- **def _freeze_level_ok(side: str, price_now: float, sl_price: float, freeze_points: int, point: float) -> bool**  (L52)  — 
- **def _snap_sl_to_rules(side: str, price_now: float, desired_sl: float, bc: BrokerConstraints) -> Optional[float]**  (L57)  — 望ましいSLを、StopLevel/FreezeLevel/丸めに収まるよう調整。
- **def _price_for_side(tick: Dict[str, float], side: str) -> float**  (L89)  — 
- **def _position_of(ticket: int) -> Any**  (L96)  — 
- **def _current_tick(symbol: str) -> Dict[str, float]**  (L103)  — 
- **class MT5Service**  (L109)  — 本線：安全なSL更新（OrderModify）
  - **MT5Service.__init__(self, max_retries: int=3, backoff_sec: float=0.3, min_change_points: int=2)**  (L113)  — 
  - **MT5Service.safe_order_modify_sl(self, ticket: int, side: str, symbol: str, desired_sl: float, reason: str='') -> Tuple[bool, Optional[float], str]**  (L118)  — 返り値: (成功/失敗, 実際に送ったSL, 詳細メッセージ)

## app/services/orderbook_stub.py
- **class MockPosition**  (L12)  — 
- **class OrderBook**  (L26)  — 
  - **OrderBook.__init__(self) -> None**  (L27)  — 
  - **OrderBook.count_open(self, symbol: Optional[str]=None) -> int**  (L31)  — 
  - **OrderBook.open(self, symbol: str, side: str, lot: float, entry: float, sl: float, tp: float) -> MockPosition**  (L34)  — 
  - **OrderBook._close(self, p: MockPosition, price: float, reason: str) -> None**  (L46)  — 
  - **OrderBook.update_with_market_and_close_if_hit(self, symbol: str) -> None**  (L63)  — 現在の価格で SL/TP 到達、またはTIMEOUTでクローズ。
  - **OrderBook.close_all(self, symbol: Optional[str]=None) -> None**  (L85)  — 現在値で全クローズ（ドライラン）。
- **def orderbook() -> OrderBook**  (L103)  — 

## app/services/profile_stats_service.py
- **class ProfileStat**  (L13)  — プロファイルごとの簡易統計（v1）
  - **ProfileStat.to_dict(self) -> Dict[str, Any]**  (L26)  — 
- **class ProfileStatsConfig**  (L39)  — 
  - **ProfileStatsConfig.stats_dir(self) -> Path**  (L43)  — 
- **class ProfileStatsService**  (L47)  — バックテスト結果からプロファイル統計を読み込むサービス。
  - **ProfileStatsService.__init__(self, base_dir: Optional[Path]=None) -> None**  (L63)  — 
  - **ProfileStatsService._backtest_csv_path(self, profile: str) -> Path**  (L76)  — 
  - **ProfileStatsService._load_latest_row(self, profile: str) -> Optional[ProfileStat]**  (L79)  — 指定プロファイルの monthly_returns.csv から
  - **ProfileStatsService.get_profile_stats(self, profiles: Optional[List[str]]=None) -> Dict[str, Dict[str, Any]]**  (L123)  — プロファイル名のリストを受け取り、
  - **ProfileStatsService._path(self, symbol: str) -> Path**  (L152)  — symbol: 'USDJPY-' を前提
  - **ProfileStatsService.load(self, symbol: str) -> dict[str, Any]**  (L158)  — プロファイル統計を読み込む
  - **ProfileStatsService.save(self, symbol: str, stats: dict[str, Any]) -> None**  (L171)  — プロファイル統計を保存する
  - **ProfileStatsService.update_from_trade(self, symbol: str, profile_name: str, pnl: float) -> dict[str, Any]**  (L178)  — 決済トレード1件から profile_stats を更新する。
  - **ProfileStatsService.set_current_profile(self, symbol: str, profile_name: str) -> dict[str, Any]**  (L226)  — 現在選択されているプロファイル名を更新する。
  - **ProfileStatsService.get_summary_for_filter(self, symbol: str) -> dict[str, Any]**  (L241)  — フィルタエンジンに渡す軽量サマリを返す。
- **def get_profile_stats_service() -> ProfileStatsService**  (L264)  — 

## app/services/recent_kpi.py
- **class RecentKpiResult**  (L25)  — 直近 N トレードの簡易 KPI 集計結果。
- **def _extract_pnl_series(trades: Union['DataFrame', Sequence[Mapping[str, Number]]], profit_field: str) -> Sequence[float]**  (L50)  — 汎用的に「pnl の列」を取り出すヘルパー。
- **def compute_kpi_from_trades(trades: Union['DataFrame', Sequence[Mapping[str, Number]]], *, profit_field: str='pnl', starting_equity: Optional[float]=None) -> RecentKpiResult**  (L74)  — 直近 N トレードの KPI を計算するメイン関数。
- **class KPIService**  (L209)  — 月次KPI（今月の損益％、最大月次DD、月次リターン系列）を一括で返すサービス。
  - **KPIService.__init__(self, root: str | Path=None) -> None**  (L215)  — 
  - **KPIService._find_latest_monthly_returns(self, profile: str) -> Path | None**  (L218)  — backtests/{profile}/**/monthly_returns.csv を探索し、最新の1つを返す。
  - **KPIService._load_monthly_returns(self, path: Path) -> pd.DataFrame**  (L228)  — 
  - **KPIService._load_runtime_metrics(self) -> dict**  (L233)  — runtime/metrics.json を読み、今月の実運用損益を加算する。
  - **KPIService.get_kpi(self, profile: str='default') -> dict**  (L246)  — GUI側が使うメインAPI
- **def compute_recent_kpi_from_decisions(limit: Optional[int]=None, *, profit_field: str='pnl', starting_equity: Optional[float]=None) -> RecentKpiResult**  (L319)  — Read logs/decisions/decisions_*.jsonl, filter trades with numeric pnl, and compute recent KPI.

## app/services/scheduler_guard.py
- **def _edition_rank(name: Optional[str]) -> int**  (L27)  — Edition 名を「強さ順」の整数に変換する。
- **def filter_jobs_for_current_edition(jobs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]**  (L38)  — EditionGuard の設定に基づき、現在のエディションで実行可能な

## app/services/shap_service.py
- **class ShapFeatureImpact**  (L15)  — 1つの特徴量に対するSHAP影響度情報
- **def _normalize_background_frame(X: pd.DataFrame, max_background: int=2000, feature_names: Optional[Sequence[str]]=None) -> pd.DataFrame**  (L23)  — 背景サンプル用に DataFrame を整理するヘルパ。
- **def compute_shap_feature_importance(model, X: pd.DataFrame, *, feature_names: Optional[Sequence[str]]=None, top_n: int=20, max_background: int=2000) -> List[ShapFeatureImpact]**  (L58)  — LightGBM などツリーモデルに対して SHAP (TreeExplainer) を使って
- **def shap_items_to_frame(items: List[ShapFeatureImpact]) -> pd.DataFrame**  (L134)  — ShapFeatureImpact のリストを pandas.DataFrame に変換するヘルパ。

## app/services/trade_service.py
- **class LotRule**  (L28)  — 
- **def round_to_step(x: float, step: float) -> float**  (L35)  — 
- **def calc_lot(equity: float, rule: LotRule=LotRule()) -> float**  (L39)  — 
- **def snapshot_account() -> Optional[dict]**  (L45)  — 
- **class TradeService**  (L54)  — Facade that coordinates guards, circuit breaker, and decision helpers.
  - **TradeService.__init__(self, mt5_client: MT5Client | None=None, profile: StrategyProfile | None=None) -> None**  (L57)  — 
  - **TradeService._get_lot_scaler(self) -> float**  (L85)  — バックテスト（月次リターン / 最大DD）から計算したロット補正係数を返す。
  - **TradeService._compute_lot_for_entry(self, symbol: str, atr: float) -> LotSizingResult**  (L141)  — 1トレードあたりのロット数を、現在の equity / ATR / tick 情報から計算する。
  - **TradeService.reload(self) -> None**  (L241)  — 
  - **TradeService._periodic_reconcile(self, symbol: str) -> None**  (L260)  — 
  - **TradeService.can_open(self, symbol: Optional[str]) -> bool**  (L269)  — 
  - **TradeService.decide_entry_from_probs(self, p_buy: float, p_sell: float) -> Dict**  (L274)  — 
  - **TradeService.decide_entry(self, p_buy: float, p_sell: float) -> Optional[str]**  (L306)  — 
  - **TradeService.can_trade(self) -> bool**  (L310)  — 
  - **TradeService.open_position(self, symbol: str, side: str, lot: float | None=None, atr: float | None=None, sl: float | None=None, tp: float | None=None, comment: str='', features: Dict[str, Any] | None=None) -> None**  (L314)  — MT5 への発注。ATR を元に lot 計算を優先し、なければ ATR なしのフォールバック lot で送信。
  - **TradeService.mark_order_inflight(self, order_id: str) -> None**  (L399)  — 
  - **TradeService.on_order_result(self, *, order_id: str, ok: bool, symbol: str) -> None**  (L402)  — 
  - **TradeService.on_order_success(self, *, ticket: Optional[int], side: str, symbol: str, price: Optional[float]=None) -> None**  (L407)  — 
  - **TradeService.on_broker_sync(self, symbol: Optional[str], fix: bool=True) -> None**  (L415)  — 
  - **TradeService.record_trade_result(self, *, symbol: str, side: str, profit_jpy: float, info: Optional[dict[str, Any]]=None) -> None**  (L418)  — 
- **def execute_decision(decision: Dict[str, Any], *, symbol: Optional[str]=None, service: Optional[TradeService]=None) -> None**  (L466)  — Live 用のヘルパ:
- **def can_open_new_position(symbol: Optional[str]=None) -> bool**  (L546)  — 
- **def decide_entry(p_buy: float, p_sell: float) -> Optional[str]**  (L554)  — 
- **def decide_entry_from_probs(p_buy: float, p_sell: float) -> dict**  (L558)  — 
- **def get_account_summary() -> dict[str, Any] | None**  (L562)  — 
- **def build_exit_plan(symbol: str, ohlc_tail: Optional[Iterable[dict[str, Any]]]) -> dict[str, Any]**  (L566)  — 
- **def mark_filled_now() -> None**  (L627)  — Record the timestamp of the latest successful fill.
- **def post_fill_grace_active() -> bool**  (L633)  — Return True when the post-fill grace window is active.
- **def mark_order_inflight(order_id: str) -> None**  (L647)  — 
- **def on_order_result(order_id: str, ok: bool, symbol: str) -> None**  (L651)  — 
- **def reconcile_positions(symbol: Optional[str]=None, desync_fix: bool=True) -> None**  (L655)  — 
- **def on_order_success(ticket: Optional[int], side: str, symbol: str, price: Optional[float]=None) -> None**  (L659)  — 
- **def record_trade_result(*, symbol: str, side: str, profit_jpy: float, info: Optional[dict[str, Any]]=None) -> None**  (L663)  — 
- **def circuit_breaker_can_trade() -> bool**  (L673)  — 

## app/services/trade_state.py
- **class TradeSettings**  (L5)  — 
- **class TradeRuntime**  (L20)  — 
- **def get_runtime() -> TradeRuntime**  (L29)  — 
- **def update_runtime(**kwargs: Any) -> None**  (L33)  — 
- **def get_settings() -> TradeSettings**  (L38)  — 
- **def update(**kwargs: Any) -> None**  (L41)  — 
- **def as_dict() -> dict[str, Any]**  (L46)  — 

## app/services/trailing.py
- **class TrailConfig**  (L8)  — 
- **class TrailState**  (L21)  — 
- **def _round_to_point(price: float, point: float) -> float**  (L30)  — 
- **def _pips(price_diff: float, pip_size: float) -> float**  (L35)  — 
- **def _price_from_pips(pips: float, pip_size: float) -> float**  (L39)  — 
- **def _profit_side(side: str, entry: float, price: float) -> float**  (L43)  — 
- **class AtrTrailer**  (L47)  — 
  - **AtrTrailer.__init__(self, cfg: TrailConfig, state: TrailState)**  (L48)  — 
  - **AtrTrailer.activation_threshold(self) -> float**  (L52)  — 
  - **AtrTrailer.step_size(self) -> float**  (L55)  — 
  - **AtrTrailer.be_threshold(self) -> float**  (L58)  — 
  - **AtrTrailer.suggest_sl(self, current_price: float) -> Optional[float]**  (L61)  — 
  - **AtrTrailer._hard_floor_sl(self) -> float**  (L100)  — 
  - **AtrTrailer._breakeven_sl(self) -> float**  (L108)  — 
  - **AtrTrailer._layer_sl(self, move_layers: int, current_price: float) -> float**  (L111)  — 
  - **AtrTrailer._ensure_profit_side(self, sl: float) -> float**  (L120)  — 
  - **AtrTrailer._apply_if_better(self, new_sl: float) -> Optional[float]**  (L133)  — 

## app/services/trailing_hook.py
- **def apply_trailing_update(*, ticket: Optional[int], side: str, symbol: str, new_sl: float, reason: str='trail') -> bool**  (L22)  — Apply trailing-stop loss updates (dry-run logs or live MT5 OrderModify).

## app/strategies/ai_strategy.py
- **def _load_model_generic(path_str: str)**  (L15)  — 1) joblib.load()
- **def load_active_model() -> Tuple[str, str, float, Dict[str, Any]]**  (L86)  — 
- **def _rsi(x: pd.Series, period: int=14) -> pd.Series**  (L115)  — 
- **def _ema(x: pd.Series, span: int) -> pd.Series**  (L122)  — 
- **def _bbands(x: pd.Series, window: int=20, n_sigma: float=2.0)**  (L125)  — 
- **def _stoch(high: pd.Series, low: pd.Series, close: pd.Series, k_win: int=14, d_win: int=3)**  (L132)  — 
- **def build_features_recipe(df: pd.DataFrame, name: str) -> pd.DataFrame**  (L139)  — 内蔵レシピで特徴量を作成。time列は残します。
- **def build_features(df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame**  (L181)  — 外部モデル・内蔵双方で使う特徴量ビルドの統一入口
- **def _load_scaler_if_any(params: Dict[str, Any])**  (L190)  — 
- **def _ensure_feature_order(feat_df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame**  (L251)  — 
- **def _sigmoid(x)**  (L264)  — 
- **def _predict_proba_generic(model, X: np.ndarray) -> np.ndarray**  (L267)  — LightGBM / XGBoost / Sklearn いずれでも陽性確率を返す汎用ハンドラー
- **def predict_signals(kind: str, payload, df_feat: pd.DataFrame, threshold: float=0.0, params=None) -> pd.Series**  (L320)  — - builtin_sma: fast/slow のクロスで +1/-1 を返す
- **def trades_from_signals(df_feat: pd.DataFrame, initial_capital: float, params=None) -> pd.DataFrame**  (L403)  — signal列（+1/-1/0）に基づいて IN/OUT/反転。

## tools/backtest_equity_curve.py
- **def _load_active_meta() -> dict[str, Any]**  (L14)  — 
- **def _load_dataset(csv_path: str) -> pd.DataFrame**  (L21)  — 
- **def _ensure_feature_order(df: pd.DataFrame, model: Any) -> tuple[pd.DataFrame, list[str]]**  (L30)  — 
- **def run_backtest(csv_path: str, out_csv: str, init_equity: float=100000.0, show: bool=True) -> None**  (L44)  — 

## tools/backtest_run.py
- **def iter_with_progress(df: pd.DataFrame, step: int=5, use_iterrows: bool=False)**  (L32)  — バックテスト用の行イテレータ。
- **def _print_progress(pct: int) -> None**  (L68)  — バックテスト進捗を [bt_progress] 形式で出力するヘルパー
- **def _month_dd(equity: pd.Series) -> float**  (L74)  — 月内最大ドローダウンを計算する（v5.1 仕様）
- **def compute_monthly_returns(equity_csv_path: str | Path, out_path: str | Path) -> Path**  (L82)  — equity.csv から v5.1 仕様の monthly_returns.csv を作成する。
- **class Trade**  (L207)  — 
- **def equity_from_bnh(df: pd.DataFrame, capital: float) -> pd.Series**  (L216)  — Buy&Hold（現物1倍）相当の指数エクイティ。close/close0でスケール。
- **def trades_from_signal_series(df: pd.DataFrame, sig: pd.Series, lot: float=0.1, contract_size: int=100000) -> list[Trade]**  (L225)  — signal（1/-1/0）からフリップ方式でトレード列を作る。
- **def equity_from_trades(df: pd.DataFrame, trades: list[Trade], capital: float) -> pd.Series**  (L278)  — トレード配列からエクイティ曲線を作る（逐次加算）。
- **def equity_from_trade_df(df_ohlcv: pd.DataFrame, trades_df: pd.DataFrame, capital: float) -> pd.Series**  (L301)  — trades_df 形式（DataFrame）から全バーに展開したエクイティ曲線を作る。
- **def to_equity(close: pd.Series, capital: float=100000.0) -> pd.DataFrame**  (L350)  — 
- **def _max_consecutive(x: pd.Series, val: int) -> int**  (L357)  — 
- **def _dd_duration_max(eq: pd.Series) -> int**  (L370)  — ドローダウン期間の最大日数を算出。時系列がintならスキップする。
- **def metrics_from_equity(eq: pd.Series) -> dict**  (L388)  — 
- **def monthly_returns_from_equity(eq_df: pd.DataFrame, trades_df: pd.DataFrame | None=None) -> pd.DataFrame**  (L406)  — エクイティ曲線（eq_df）とトレード一覧（trades_df）から、
- **def trades_from_buyhold(df: pd.DataFrame, capital: float) -> pd.DataFrame**  (L526)  — 
- **def trade_metrics(trades: pd.DataFrame) -> dict**  (L561)  — 
- **def slice_period(df: pd.DataFrame, start: str | None=None, end: str | None=None) -> pd.DataFrame**  (L602)  — 指定期間で DataFrame をスライスする。
- **def run_backtest(data_csv: Path, start: str | None, end: str | None, capital: float, out_dir: Path, profile: str='michibiki_std', symbol: str='USDJPY') -> Path**  (L621)  — v5.1 準拠のバックテストを実行する
- **def run_wfo(data_csv: Path, start: str | None, end: str | None, capital: float, out_dir: Path, train_ratio: float=0.7) -> Path**  (L779)  — 
- **def _normalize_dates_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[str | None, str | None]**  (L886)  — --start-date / --end-date を優先しつつ、
- **def _build_period_tag(start: str | None, end: str | None) -> str**  (L918)  — ログ用の期間タグを生成する。
- **def _mirror_latest_run(period_dir: Path, base_dir: Path) -> None**  (L932)  — 期間付きフォルダに出力されたファイルのうち、
- **def main() -> None**  (L968)  — 

## tools/dump_feature_importance.py
- **def _load_latest_report() -> Tuple[str | None, dict[str, Any] | None]**  (L12)  — 
- **def _load_features_from_report(j: dict[str, Any]) -> list[str]**  (L22)  — 
- **def _load_model(pkl_path: str) -> Any**  (L27)  — 
- **def _write_feat_csv(model: Any, feat_cols: list[str], out_csv: str) -> str**  (L31)  — 
- **def main() -> None**  (L78)  — 

## tools/gen_api_catalog.py
- **class Item**  (L17)  — 
- **def _is_excluded(path: Path) -> bool**  (L27)  — 
- **def _ann_to_str(node: ast.AST | None) -> str**  (L31)  — 
- **def _arg_to_str(a: ast.arg, default: ast.AST | None) -> str**  (L39)  — 
- **def _build_signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str]**  (L51)  — 
- **def _doc_firstline(node: ast.AST) -> str**  (L97)  — 
- **def scan_file(py: Path) -> list[Item]**  (L102)  — 
- **def iter_py_files() -> list[Path]**  (L158)  — 
- **def write_outputs(items: list[Item]) -> None**  (L170)  — 
- **def main() -> int**  (L204)  — 

## tools/gen_public_api.py
- **class Entry**  (L55)  — 
- **def load_catalog() -> list[dict[str, Any]]**  (L61)  — 
- **def read_list(path: Path) -> set[str]**  (L66)  — 
- **def norm_path(p: str) -> str**  (L77)  — 
- **def class_name_from_qualname(qualname: str) -> str**  (L80)  — 
- **def is_public_name(name: str) -> bool**  (L84)  — 
- **def key_variants(item: dict[str, Any]) -> set[str]**  (L87)  — 
- **def fmt_signature(item: dict[str, Any]) -> str**  (L108)  — 
- **def build_entry(item: dict[str, Any]) -> Entry**  (L114)  — 
- **def section_name(item: dict[str, Any]) -> str**  (L132)  — 
- **def group_key(item: dict[str, Any]) -> str**  (L140)  — 
- **def is_selected_by_project_policy(item: dict[str, Any]) -> bool**  (L144)  — 
- **def write_md(by_section: dict[str, dict[str, list[Entry]]], allow: set[str], block: set[str]) -> None**  (L168)  — 
- **def main() -> int**  (L210)  — 

## tools/list_wfo_reports.py
- **class WFOReportSummary**  (L26)  — 
  - **WFOReportSummary.from_json(cls, path: Path, data: dict[str, Any]) -> WFOReportSummary**  (L39)  — 
- **def get_project_root() -> Path**  (L80)  — fxbot_path があればそれを使い、なければカレントから推測。
- **def find_wfo_reports(root: Path | None=None) -> list[WFOReportSummary]**  (L88)  — 
- **def print_table(reports: list[WFOReportSummary]) -> None**  (L110)  — 
- **def main() -> None**  (L149)  — 

## tools/mt5_smoke.py
- **def main() -> int**  (L55)  — 

## tools/ops_start.py
- **def main() -> int**  (L38)  — 

## tools/profile_switch_analyzer.py
- **class SwitchRecord**  (L11)  — 
- **def _iter_decisions(path: Path) -> Iterable[dict]**  (L19)  — 
- **def _extract_reasons(rec: dict) -> list[str]**  (L34)  — 
- **def _parse_switch_reason(reason: str) -> tuple[str, str] | None**  (L52)  — 
- **def analyze_switches(symbol: str, limit: int=20) -> list[SwitchRecord]**  (L63)  — 
- **def main() -> None**  (L89)  — 

