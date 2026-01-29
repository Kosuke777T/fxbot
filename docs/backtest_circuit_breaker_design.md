# バックテスト用サーキットブレーカー（BT-CB）設計

## 観測で確定した事実（現状）

- **backtest_engine.py** / **tools/backtest_run.py** には、equity / DD / 連敗に基づく「停止判定」は**存在しない**。
- equity, peak_equity, max_drawdown, consecutive_losses は**集計・メトリクス用**にのみ利用されている（debug_counters, stats, equity_curve.csv）。
- エントリーブロックは次のみ:
  - 既存ポジション保有中
  - decision.action != ENTRY または side が None
  - 無効な side
- **live/stub 用 CircuitBreaker**（app/services/circuit_breaker.py）は連敗・日次損失のみで、equity/peak/dd は使わず、**backtest では未使用**。

## 接続設計（未実装・最小差分で実装するための方針）

### 判定入力

| 入力 | 取得元（backtest_engine 内） |
|------|------------------------------|
| equity | `self.executor.equity` |
| peak_equity | `debug_counters.get("peak_equity", self.executor.initial_capital)` |
| dd | `(equity / peak_equity - 1.0)`（peak_equity > 0 のとき） |
| losing_streak | `self.consecutive_losses`（トレードクローズ時に更新済み） |

### 判定タイミング

- **推奨**: 各バー・エントリー試行直前（`debug_counters["n_entry_attempts"] += 1` の直後、既存ポジションブロックの直前）。
- 代替: 各トレードクローズ直後（consecutive_losses 更新直後）。その場合は「次バー以降のエントリー禁止」として扱う。

### トリップ時の挙動

- **新規エントリー禁止**: 上記タイミングで tripped なら `continue` して当該バーのエントリーをスキップ。
- 強制クローズ / クールダウン: 必要なら別レイヤで設計（現段階では「エントリー禁止」のみを接続点とする）。

### 責務境界

- **core/backtest** に「BacktestCircuitBreaker」相当を置く（新規クラス/関数は最小限。例: `app/core/backtest/backtest_cb.py` の `should_block_entry(equity, peak, dd, streak, thresholds) -> (bool, str|None)`）。
- **services の CircuitBreaker は流用しない**（仕様が異なる: live は連敗・日次損失のみ、BT は equity/dd/streak を想定）。
- しきい値の設定源は**未確定**。実装時は設定ファイル・環境変数等を観測で確定してから追加する（推測で埋めない）。

### 接続点（コード上の位置）

- **ファイル**: `app/core/backtest/backtest_engine.py`
- **位置**: `run()` 内、`debug_counters["n_entry_attempts"] += 1` の直後、コメント「既存ポジション保有中の場合はブロック」の直前。
- ここで `eq, peak, dd, streak` を算出し、BT-CB の判定を呼び、tripped なら `continue` する。

## 観測ログ（実装済み）

- バックテスト実行時、**最初のエントリー試行時**に 1 行だけ出力:
  - `[BT-CB] no_circuit_breaker eq=... peak=... dd=... streak=... (no threshold check, entry not gated)`
- これにより「CB が未接続であること」をログで確定できる。
