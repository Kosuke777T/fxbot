# 自動売買ループ起動点の観測結果（grepで確定）

## 1. 観測フェーズ：呼び出し経路

### GUI のボタン押下 → どこが呼ばれるか

| 操作 | GUI の起点 | 呼び出し先 | 備考 |
|------|------------|------------|------|
| 「取引ON」（取引：停止中 → クリックで開始） | `control_tab.py` `btn_toggle` clicked | `_toggle_trading()` | 取引ボタンは `btn_toggle`（取引：停止中（クリックで開始）） |
| `_toggle_trading()` 内で enabled=True のとき | `control_tab.py` | `self._main_window._trade_loop.start()` | **GUI から services は直呼びしない**。MainWindow の `_trade_loop`（TradeLoopRunner）を呼ぶのみ。 |
| `_trade_loop.start()` | `main.py` | `TradeLoopRunner.start()` | **自動売買ループの起動点（1箇所のみ）** |
| `_trade_loop.stop(reason=...)` | `control_tab.py`（取引OFF / ログアウト時） | `TradeLoopRunner.stop()` | **停止処理の唯一の箇所** |

### services のどのメソッドが呼ばれているか

- **GUI のボタン押下では services を直接呼ばない。**
- `TradeLoopRunner.start()`（main.py）の**内部**で、次の services が使われる：
  - `trade_service.run_start_diagnosis(symbol)` … 開始時事前診断（T-65）
  - `ExecutionService.execute_entry(...)` … タイマー tick ごとに `_on_tick` → `_run` から呼ばれる
- 責務境界: GUI → `_trade_loop.start()/stop()` のみ。services への入り口は TradeLoopRunner 内。

### grep で確認したキーワード

- **取引ON / 取引：稼働中 / クリックで開始**: `control_tab.py` の `btn_toggle` とラベル
- **start / trade_loop**: `control_tab.py` で `_trade_loop.start()`、`main.py` で `TradeLoopRunner.start()` と `_timer.start()`
- **loop / QTimer / thread**: `main.py` の `TradeLoopRunner`（QTimer + `_on_tick`、別スレッドで `_run`）
- **JobScheduler**: 自動売買ループとは別（SchedulerTickRunner で `run_pending()`）。取引ボタン経路には含まれない

---

## 2. 起動点の確定

- **自動売買ループを実際に開始している箇所**: `app/gui/main.py` の **`TradeLoopRunner.start()`** の 1 箇所のみ。
- 呼び出し元: `app/gui/control_tab.py` の `_toggle_trading()` 内の `_main_window._trade_loop.start()` のみ。
- コード上に「【起動点】」コメントを追加済み。

---

## 3. ログの一点化

- **`[trade_loop] started mode=... dry_run=... symbol=...`**  
  → **`TradeLoopRunner.start()` 内の 1 箇所のみ**（main.py。GUI・補助・別スレッドでは出さない）。
- **`[trade_loop] stopped reason=...`**  
  → **`TradeLoopRunner.stop()` 内の 1 箇所のみ**（同上）。
- services 側には `[trade_loop] start blocked` 等はあるが、`started` / `stopped` のログはなし（重複なし）。

---

## 4. 多重起動ガード

- **既存の `_is_running`**（`TradeLoopRunner`）を使用。
- `start()` の先頭で `if self._is_running:` なら `[trade_loop] start denied reason=already_running` を出して `return False`。
- 新規フラグは追加していない。

---

## 5. 動作確認の目安

- `python -m app.gui.main` で起動。
- 「取引ON」1 回 → `[trade_loop] started ...` が 1 回だけ出る。
- 連打 → `started` は 2 回以上出ず、`start denied reason=already_running` が出る。
- 停止操作 → `[trade_loop] stopped reason=...` が 1 回出る。
