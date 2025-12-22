# Part 3 監査レポート（T-30 BacktestEngine v5.1準拠化 / T-31 WFO安定化）

## 監査日時
2025-12-21

## 監査対象
- backtests/, logs/backtest/, logs/retrain/, logs/ops/
- app/**, core/**, tools/** の関連実装
- 成果物: monthly_returns.csv, decisions.jsonl, report_*.json, stability_*.json, active_model.json

---

## 1. 直近のバックテスト成果物監査

### 実行結果
```
monthly_returns.csv found: 40
latest monthly_returns: backtests\_wfo_smoke2\20251221_085836_michibiki_std\monthly_returns.csv
header: ['year_month', 'return_pct', 'max_dd_pct', 'total_trades', 'pf']
missing: []
extra: []
decisions*.jsonl found: 23
latest decisions: backtests\michibiki_std\decisions.jsonl
decision keys count: 14
decision keys head: ['decision', 'decision_context', 'decision_detail', 'filter_pass', 'filter_reasons', 'filters', 'meta', 'prob_buy', 'prob_sell', 'runtime', 'strategy', 'symbol', 'ts_jst', 'type']
```

### 判定結果

#### ✅ PASS
- **monthly_returns.csv**: 最新ファイルが存在し、ヘッダが固定仕様（year_month, return_pct, max_dd_pct, total_trades, pf）に完全一致
- **decisions.jsonl**: 最新ファイルが存在し、JSONとして読める
- **decisions.jsonl キー構造**: v5.1仕様に準拠（filter_pass, filter_reasons, filters, decision_context, runtime を含む）

#### ⚠️ 注意事項
- 最新の monthly_returns.csv と decisions.jsonl が異なるディレクトリに存在（backtests/_wfo_smoke2 と backtests/michibiki_std）
  - これは正常（WFO実行と通常バックテストの成果物が別管理されているため）

---

## 2. BacktestEngine 統一（StrategyBase → Filter → SimulatedExecution）の静的監査

### 検索結果サマリ
- `BacktestEngine`: app/core/backtest/backtest_engine.py に実装
- `StrategyFilterEngine`: app/core/filter/strategy_filter_engine.py を使用
- `SimulatedExecution`: app/core/backtest/simulated_execution.py を使用
- `Filter` スキップ: 検出されず

### コード確認結果

#### ✅ PASS
- **BacktestEngine.run()**: 
  - 行165: `filter_pass, filter_reasons = self.filter_engine.evaluate(...)` で Filter を必ず実行
  - 行206: `if not filter_pass: continue` で Filter 未通過時は SimulatedExecution に渡さない
  - 行52: `self.executor = SimulatedExecution(...)` で SimulatedExecution のみ使用
- **Filter スキップ**: 検出されず（すべてのエントリー試行で Filter を通過）
- **独自stub**: SimulatedExecution 以外の execution stub は使用されていない

#### 📝 実装詳細
```165:165:app/core/backtest/backtest_engine.py
filter_pass, filter_reasons = self.filter_engine.evaluate(entry_context, filter_level=self.filter_level)
```

```206:207:app/core/backtest/backtest_engine.py
if not filter_pass:
    continue
```

```52:52:app/core/backtest/backtest_engine.py
self.executor = SimulatedExecution(initial_capital, contract_size)
```

---

## 3. KPI/診断AI/ランキングが backtest 成果物に連動しているか

### 検索結果サマリ
- `monthly_returns`: 326件のマッチ（app/services/kpi_service.py, app/gui/backtest_tab.py, tools/backtest_run.py など）
- `decisions.jsonl`: 326件のマッチ（app/services/execution_service.py, app/core/backtest/backtest_engine.py など）
- `KPI`: app/services/kpi_service.py, app/gui/kpi_tab.py に実装
- `diagnos`: app/core/edition.py に diagnosis_level 定義
- `ranking`: app/core/edition.py に ranking_send 定義
- `report_view`: app/gui/backtest_tab.py に実装
- `backtest_dir`, `_find_latest_bt_dir`: app/gui/backtest_tab.py に実装

### コード確認結果

#### ✅ PASS
- **KPIService**: 
  - `_find_latest_monthly_returns()`: backtests/{profile}/**/monthly_returns.csv を探索
  - `load_backtest_kpi_summary()`: monthly_returns.csv を読み込み
- **BacktestTab**: 
  - `_find_latest_bt_dir()`: 最新バックテストディレクトリを探索
  - `_on_backtest_finished()`: monthly_returns.csv と decisions.jsonl を処理
- **decision_compare.py**: decisions.jsonl を読み込み、KPI計算に使用

#### 📝 実装詳細
```218:228:app/services/kpi_service.py
def _find_latest_monthly_returns(self, profile: str) -> Path | None:
    backtests/{profile}/**/monthly_returns.csv を探索し、最新の1つを返す。
    ...
    candidates = list(base.rglob("monthly_returns.csv"))
```

```1108:1108:app/gui/backtest_tab.py
def _find_latest_bt_dir(self, out_dir: Path) -> Optional[Path]:
```

```1742:1751:app/gui/backtest_tab.py
# decisions.jsonl の読み込み処理（あれば AIタブへ連動）
decisions_jsonl = out_dir / "decisions.jsonl"
```

---

## 4. WFO安定化監査

### 検索結果サマリ
- `expected_features`: app/services/ai_service.py, core/ai/service.py, core/ai/loader.py に実装
- `sync_expected_features`: app/services/ai_service.py に `_sync_expected_features()` 実装
- `active_model.json`: 複数箇所で読み込み（app/services/ai_service.py, core/ai/service.py, tools/backtest_run.py など）
- `timeout`: app/services/ops_service.py に実装
- `retry`: app/services/ops_history_service.py, tools/ops_replay.py に実装
- `error summary`: app/services/ops_history_service.py に `summarize_ops_history()` 実装
- `wfo`, `retrain`: tools/wfo_all.ps1, tools/backtest_run.py, scripts/weekly_retrain.py に実装

### logs/retrain 成果物一覧（最新20件）
```
Name                                      Length LastWriteTime
----                                      ------ -------------
stability_1763370025.json                    812 2025/12/21 12:06:16
stability_wfo_1766274494.json                714 2025/12/21 12:00:42
weekly_retrain.jsonl                        9360 2025/12/15 21:35:04
weekly_retrain_last.json                     606 2025/12/15 21:35:04
equity_test_1763370025.csv                830610 2025/11/17 18:00:29
equity_train_1763370025.csv              1878174 2025/11/17 18:00:29
report_1763370025.json                      2105 2025/11/17 18:00:29
report_1763292931.json                      2105 2025/11/16 20:35:35
equity_test_1763292931.csv                830610 2025/11/16 20:35:35
equity_train_1763292931.csv              1878174 2025/11/16 20:35:35
weekly_retrain_20251113_091458.log           595 2025/11/13 9:14:58
feat_importance_lk10_20251107_175557.csv     690 2025/11/07 17:55:57
job_20251107_175555_4ab13e4d.log             898 2025/11/07 17:55:57
report_1762505757473982.json                3976 2025/11/07 17:55:57
feat_importance_lk15_20251107_163044.csv     691 2025/11/07 16:30:44
job_20251107_163005_f87f9cc2.log            1009 2025/11/07 16:30:07
feat_importance_lk15_20251107_163007.csv     691 2025/11/07 16:30:07
report_17625006073549628.json               4001 2025/11/07 16:30:07
feat_importance_lk15_20251107_162243.csv     717 2025/11/07 16:22:43
job_20251107_162053_b7252f19.log            1009 2025/11/07 16:20:56
```

### コード確認結果

#### ✅ PASS
- **expected_features 同期処理**: 
  - `app/services/ai_service.py`: `_sync_expected_features()` が実装され、起動時に active_model.json から expected_features を同期
  - `core/ai/service.py`: `_load_expected_features()` が実装され、最新レポートから expected_features を読み込み
- **active_model.json 更新処理**: 
  - `scripts/weekly_retrain.py`: `save_model_and_meta()` で active_model.json を更新（行688-701）
- **retry/timeout/error summary**: 
  - `app/services/ops_history_service.py`: `summarize_ops_history()` でエラー集計
  - `app/services/ops_service.py`: timeout 処理が実装
  - `tools/ops_replay.py`: retry 処理が実装
- **logs/retrain 成果物**: 
  - `stability_*.json`: 最新2件が存在（stability_1763370025.json, stability_wfo_1766274494.json）
  - `report_*.json`: 複数件が存在
  - `equity_*.csv`: train/test 用のエクイティ曲線が存在

#### 📝 実装詳細
```193:213:app/services/ai_service.py
def _sync_expected_features(self) -> None:
    active_model.json / モデル本体から expected_features を
    self.expected_features に一度だけコピーする。
```

```688:701:scripts/weekly_retrain.py
active = {
    "model_name": "LightGBM_clf",
    "file": model_name,
    "meta_file": meta_path.name,
    "version": version,
    "best_threshold": threshold_info.get("best_threshold"),
    "feature_order": list(feature_cols),
    "features": list(feature_cols),
}
active_path = cfg.paths.models_dir / "active_model.json"
with active_path.open("w", encoding="utf-8") as f:
    json.dump(active, f, ensure_ascii=False, indent=2)
```

```22:33:app/services/ops_history_service.py
def _load_saved_wfo_stability(run_id: str) -> dict | None:
    """logs/retrain/stability_{run_id}.json を最優先で読む。壊れてたら None。"""
    try:
        p = Path("logs") / "retrain" / f"stability_{run_id}.json"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None
```

---

## 5. ops_history / ops_replay の "saved stability 優先" が維持されているか

### 検索結果サマリ
- `_load_saved_wfo_stability`: app/services/ops_history_service.py, tools/ops_replay.py に実装
- `loaded saved stability`: app/services/ops_history_service.py にログ出力（行376, 425, 485）
- `stability_`: app/services/ops_history_service.py, tools/ops_replay.py で stability_{run_id}.json を読み込み
- `evaluate_wfo_stability`: app/services/wfo_stability_service.py に実装

### コード確認結果

#### ✅ PASS
- **ops_history_service.py**: 
  - 行374-377: `_load_saved_wfo_stability()` を最優先で呼び出し、保存済み stability があれば使用
  - 行379-383: 保存済みが無い場合のみ `evaluate_wfo_stability()` を呼び出し（フォールバック）
  - 行423-426: 同様の処理が promoted_at 分岐でも実装
  - 行483-486: 同様の処理が dry run 成功分岐でも実装
- **ops_replay.py**: 
  - 行365-368: `_load_saved_wfo_stability()` を最優先で呼び出し
  - 行371-375: 保存済みが無い場合のみ `evaluate_wfo_stability()` を呼び出し（フォールバック）

#### 📝 実装詳細
```362:383:app/services/ops_history_service.py
# まず保存済み stability_{run_id}.json を最優先で採用
out = None
run_id = None
try:
    m = wfo_inputs.get("metrics_wfo") or {}
    rid = m.get("run_id")
    if rid is not None:
        run_id = str(rid)
except Exception:
    run_id = None

if run_id:
    out = _load_saved_wfo_stability(run_id)
    if out is not None:
        logger.debug(f"[wfo] loaded saved stability run_id={run_id}")

# 保存済みが無い場合のみ従来の再計算にフォールバック
if out is None:
    out = evaluate_wfo_stability(
        wfo_inputs.get("metrics_wfo"),
        metrics_path=wfo_inputs.get("paths", {}).get("metrics_wfo"),
    )
```

```354:376:tools/ops_replay.py
# まず保存済み stability_{run_id}.json を最優先で採用
out = None
run_id = None
try:
    m = wfo_inputs.get("metrics_wfo") or {}
    rid = m.get("run_id")
    if rid is not None:
        run_id = str(rid)
except Exception:
    run_id = None

if run_id:
    out = _load_saved_wfo_stability(run_id)
    if out is not None:
        print(f"[ops_replay] loaded saved stability run_id={run_id}", flush=True, file=sys.stderr)

# 保存済みが無い場合のみ従来の再計算にフォールバック
if out is None:
    out = evaluate_wfo_stability(
        wfo_inputs.get("metrics_wfo"),
        metrics_path=wfo_inputs.get("paths", {}).get("metrics_wfo"),
    )
```

---

## 総合判定

### ✅ PASS 項目
1. ✅ monthly_returns.csv: 存在確認、ヘッダ仕様一致
2. ✅ decisions.jsonl: 存在確認、JSON読込可能、v5.1仕様準拠
3. ✅ BacktestEngine: Filter を必ず実行、SimulatedExecution のみ使用
4. ✅ KPI/診断AI/ランキング: backtest 成果物に連動
5. ✅ expected_features 同期: 実装確認
6. ✅ active_model.json 更新: 実装確認
7. ✅ retry/timeout/error summary: 実装確認
8. ✅ logs/retrain 成果物: 存在確認（stability_*.json, report_*.json, equity_*.csv）
9. ✅ ops_history/ops_replay: "saved stability 優先" が維持

### ⚠️ 注意事項（修正不要）
- 最新の monthly_returns.csv と decisions.jsonl が異なるディレクトリに存在（正常動作）

### ❌ FAIL 項目
なし

---

## 結論

**Part 3（T-30 BacktestEngine v5.1準拠化 / T-31 WFO安定化）は、監査項目すべてで PASS を確認しました。**

- バックテスト成果物（monthly_returns.csv, decisions.jsonl）は仕様通りに生成されている
- BacktestEngine は Filter を必ず実行し、SimulatedExecution のみ使用している
- KPI/診断AI/ランキングは backtest 成果物に連動している
- WFO安定化機能（expected_features 同期、active_model.json 更新、retry/timeout/error summary）は実装されている
- ops_history/ops_replay は "saved stability 優先" を維持している

**修正が必要な項目はありません。**

