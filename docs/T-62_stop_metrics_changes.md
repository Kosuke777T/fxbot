# T-62 停止後tick残り火根絶・metrics.jsonロック対策 変更メモ

## 観測結果（修正前）

### A. 停止後tick残り火

- **tick 仕組み**: `TradeLoopRunner` は `QTimer.timeout` → `_on_tick()`。`_on_tick()` 内で `threading.Thread(target=self._run).start()` で別スレッド実行。`_run()` 内で `_is_running` を確認してから `execute_entry` を呼ぶ。
- **stop() の従来順序**: `logger stopping` → `_timer.stop()` → `_is_running = False`。この順序だと、既にキューに入った timeout が発火したときに `_on_tick` が走り、その時点ではまだ `_is_running` が True のままなので `_run` が起動し得る。
- **問題**: stop 直後に tick がコールバックとして入ると、`execute_entry` に到達しうる（残り火）。

### B. metrics.json ロック

- **書き手**: `app/services/metrics.py` の `publish_metrics()` のみ。呼び元は `execution_service.py` / `execution_stub.py`。
- **読み手**: `app/gui/dashboard_tab_qt.py` の `_refresh_metrics()` で `with open(METRICS_JSON, "r")` により 1 秒ごとに開いて即閉じている（長時間オープンなし）。
- **従来の書き方**: `tempfile.mkstemp` で tmp 作成 → `write_text` → `shutil.move(tmp, path)` を 10 回リトライ（0.5s 間隔）。失敗時は `[metrics][warn] could not update ... (still locked). skipped.` で捨てていた。

---

## 実装内容

### 1) 停止後tick残り火の根絶（app/gui/main.py）

**対象**: `TradeLoopRunner._on_tick`, `TradeLoopRunner.stop`

- **_on_tick 先頭ガード**: ハンドラの**先頭**で `if not self._is_running: return` を追加。stop 後にキューに入った tick が発火しても、ここで即 return し `_run` / `execute_entry` に到達しない。
- **stop() の順序変更**:  
  - 先に `self._is_running = False` を設定  
  - 続けて `self._timer.stop()`  
  - その後、既存どおり `[trade_loop] stopping ...` / `[trade_loop] stopped ...` を出力。  
  これにより「止まった」ことを先に立ててから timer を止める。

**挿入位置の目印**:
- `_on_tick`: 関数先頭、`"""タイマーイベントハンドラ..."""` の直後、`if not self._is_running: return` を追加。
- `stop`: `if not self._is_running: return` の直後。`self._is_running = False` と `self._timer.stop()` のブロックを「停止後tick残り火根絶」コメント付きで追加。

### 2) metrics.json ロック対策（app/services/metrics.py）

**対象**: `publish_metrics()` 内の JSON 書き込みブロック

- **atomic write に統一**:  
  - `runtime/metrics.json` への直書きは行わない。  
  - 同一ディレクトリの `metrics.json.tmp` に `write_text` で書き、`os.replace(tmp_path, path)` で本ファイルを置換。
- **リトライ**: `os.replace` が `PermissionError` / `OSError` のとき、約 100ms 間隔で最大 5 回リトライ。
- **失敗時**: 捨てない。tmp は削除せず残す。ログは 1 行で `[metrics][warn] could not replace ... (locked). tmp left for next retry.`。次回の `publish_metrics` 呼び出しで同じ `.tmp` を上書きして再度 `os.replace` を試行するため、次回に回復可能。

**挿入位置の目印**: `publish_metrics` 内「JSON（別プロセス連携／Dashboard標準入力）」コメント以降の、path 決定〜tmp 書き〜replace のブロック全体を上記仕様に差し替え。

---

## 動作確認の目安

- **静的**: `python -X utf8 -m py_compile app/gui/main.py app/gui/control_tab.py app/services/metrics.py` が通ること。
- **実行**:
  - 取引ON → `[trade_loop] started ...` が出る。
  - 取引OFF → `[trade_loop] stopped ...` が出る。
  - 停止後に `[trade_loop][tick] calling execute_entry ...` が出ないこと。
  - `[metrics][warn] could not update ... (still locked). skipped.` は出ないこと（出る場合は `tmp left for next retry` のメッセージに変わり、次回で回復する挙動になっていること）。

---

## 禁止事項の遵守

- started/stopped ログの出力箇所は増やしていない（T-62 の一点化維持）。
- 停止後tick問題をログ抑制だけで誤魔化していない（`_is_running` と stop 順序で実害を防止）。
- metrics 更新失敗を握りつぶして捨てていない（tmp 残し＋次回再試行で可視化の信頼性を維持）。
