# Public API

このファイルは `tools/gen_public_api.py` により `docs/api_catalog.json` から自動生成されます。

## 方針（ミチビキ仕様 寄せ）
- Services は **入口クラス**を公開（public method を列挙）
- Core は **契約として固定したいメソッドのみ**公開（最小限）
- allowlist: `docs\public_api_allowlist.txt`（強制追加。blockより優先）
- blocklist: `docs\public_api_blocklist.txt`（強制除外）

## Services 公開クラス
- `AISvc`
- `DiagnosisService`
- `ExecutionService`
- `JobScheduler`
- `KPIService`
- `RankingService`

## Core 公開メソッド
- `BacktestEngine.run`
- `MT5Client.close_position`
- `MT5Client.get_positions`
- `MT5Client.get_price`
- `MT5Client.initialize`
- `MT5Client.order_buy`
- `MT5Client.order_sell`
- `StrategyFilterEngine.evaluate`

## Services Layer

### AISvc
- `AISvc.predict(self, X: np.ndarray | Dict[str, float], *, no_metrics: bool=False) -> 'AISvc.ProbOut'`  (app/services/ai_service.py:L320) — 単一サンプルの特徴量を受け取り、p_buy / p_sell / p_skip を返す。
- `AISvc.get_feature_importance(self, method: str='gain', top_n: int=20, cache_sec: int=300) -> pd.DataFrame`  (app/services/ai_service.py:L407) — GUI から呼び出して Feature Importance を取得する API。
- `AISvc.get_shap_top_features(self, *, top_n: int=20, max_background: int=2000, csv_path: Path | None=None, cache_sec: int=300) -> pd.DataFrame`  (app/services/ai_service.py:L537) — LightGBMモデルに対する SHAP グローバル重要度（平均絶対SHAP）を計算し、
- `AISvc.get_shap_values(self)`  (app/services/ai_service.py:L649) — SHAP 結果を EditionGuard に従って制限して返す。
- `AISvc.feature_importance(self) -> pd.DataFrame`  (app/services/ai_service.py:L705) — FI を edition に応じて TopN で返す。
- `AISvc.shap_summary(self) -> Dict[str, Any]`  (app/services/ai_service.py:L759) — SHAP を edition に応じて TopN で返す。
- `AISvc.get_live_probs(self, symbol: str) -> dict[str, float]`  (app/services/ai_service.py:L831) — Live 用：execution_stub と同じ特徴量パイプラインを使って
- `AISvc.build_decision_from_probs(self, probs: dict, symbol: str) -> dict`  (app/services/ai_service.py:L938) — Live 用：execution_stub の ENTRY/SKIP 判定を最小限で再現。

### DiagnosisService
- `DiagnosisService.analyze(self, profile: str='std', start=None, end=None) -> dict`  (app/services/diagnosis_service.py:L32) — 診断AI v0:

### ExecutionService
- `ExecutionService.process_tick(self, symbol: str, price: float, timestamp: datetime, features: Optional[Dict[str, float]]=None, dry_run: bool=False) -> Dict[str, Any]`  (app/services/execution_service.py:L214) — 1ティック分の処理をまとめて行うヘルパー。
- `ExecutionService.execute_entry(self, features: Dict[str, float], *, symbol: Optional[str]=None, dry_run: bool=False, timestamp: Optional[datetime]=None) -> Dict[str, Any]`  (app/services/execution_service.py:L310) — 売買判断 → フィルタ判定 → decisions.jsonl 出力まで一貫処理
- `ExecutionService.execute_exit(self, symbol: Optional[str]=None, dry_run: bool=False) -> Dict[str, Any]`  (app/services/execution_service.py:L598) — 決済監視/クローズ処理

### KPIService
- `KPIService.load_backtest_kpi_summary(self, profile: str) -> Dict[str, Any]`  (app/services/kpi_service.py:L66) — バックテストKPIサマリを読み込む（仕様書 v5.1 準拠）。
- `KPIService.compute_monthly_dashboard(self, profile: str) -> KpiDashboard`  (app/services/kpi_service.py:L152) — 月次ダッシュボードデータを計算する（仕様書 v5.1 準拠）。
- `KPIService.compute_target_progress(self, return_pct: float, target: float=TARGET_MONTHLY_RETURN) -> float`  (app/services/kpi_service.py:L229) — 月3%に対する進捗率（0.0〜2.0=0〜200%）を返す。
- `KPIService.compute_trade_stats(self, profile: str) -> dict`  (app/services/kpi_service.py:L272) — バックテスト or 実運用のトレード結果から
- `KPIService.load_monthly_returns(self, profile: str) -> pd.DataFrame`  (app/services/kpi_service.py:L413) — 指定プロファイルの monthly_returns.csv を読み込んで返す。
- `KPIService.refresh_monthly_returns(self, profile: str) -> pd.DataFrame`  (app/services/kpi_service.py:L421) — BacktestRun が monthly_returns.csv を更新した後に呼び出す前提。
- `KPIService.get_kpi(self, profile: str='default') -> dict`  (app/services/recent_kpi.py:L246) — GUI側が使うメインAPI

## Core Layer

### BacktestEngine
- `BacktestEngine.run(self, df: pd.DataFrame, out_dir: Path, symbol: str='USDJPY-') -> Dict[str, Any]`  (app/core/backtest/backtest_engine.py:L93) — バックテストを実行する

### MT5Client
- `MT5Client.initialize(self) -> bool`  (app/core/mt5_client.py:L48) — MT5ターミナルの初期化（ログインは login_account()）
- `MT5Client.close_position(self, ticket: int, symbol: str, retries: int=3) -> bool`  (app/core/mt5_client.py:L207) — 指定チケットの成行クローズ
- `MT5Client.get_positions(self)`  (app/core/mt5_client.py:L261)

### StrategyFilterEngine
- `StrategyFilterEngine.evaluate(self, ctx: Dict, filter_level: int) -> Tuple[bool, List[str]]`  (app/core/filter/strategy_filter_engine.py:L49) — エントリー可否を評価する

---
- allowlist entries: 0
- blocklist entries: 0
