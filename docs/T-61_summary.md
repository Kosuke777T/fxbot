# T-61 設計主導パッチ｜開始・停止・判断のログ観測

## 目的

自動売買ループの **開始 / 停止 / 判断** が必ずログで観測できる状態に固定する。  
人間がログを見て「今は動いているか」「止まっているか」「危険か」を即断できることを最優先とする。

---

## 1. 起動点（観測で確定）

| 項目 | 箇所 |
|------|------|
| **自動売買ループ開始の唯一の入口** | `app/gui/main.py` の `TradeLoopRunner.start()` |
| **その呼び出し元** | `app/gui/control_tab.py` の `_toggle_trading()` 内で `_main_window._trade_loop.start()` の **1 箇所のみ** |
| **開始ログ** | 上記 `start()` 内でのみ `[trade_loop] started mode=... dry_run=... symbol=...` を出力（T-61 で 1 箇所に統一済み） |

### 多重起動の可能性

- **Control タブの「取引：稼働中」トグルを連打した場合**: `start()` 内の `_is_running` チェックで 2 回目以降は拒否され、`[trade_loop] start denied reason=already_running` がログに出る。
- それ以外に `start()` を呼ぶコードは存在しないため、**起動点は実質 1 箇所**。

---

## 2. 停止経路（観測で確定）

すべて次の 1 行に収束する:  
`[trade_loop] stopped reason=...`

| 経路 | reason の値 | トリガー |
|------|-------------|----------|
| **GUI 停止** | `ui_toggle_off` | Control タブで「取引：稼働中」をオフにしたとき |
| **自動停止** | `auto_stop_trading_disabled` | タイマー tick で `trading_enabled` が False と判定されたとき |

### 補足

- **エラー停止**: `_run()` 内で例外が発生した場合は、その tick のみ失敗ログを出し、**ループは止まらない**（タイマーは継続）。  
  現状、例外で `stop()` を呼ぶ経路はない。必要なら T-62 以降で「例外時 stop(reason="error")」を検討可能。

---

## 3. 判断ログの網羅性（ENTRY / SKIP / BLOCK / EXIT）

「以降のすべての判断」が次のいずれかに残ることを確認した。

### 3.1 decisions.jsonl（ログファイル: `logs/decisions_YYYY-MM-DD.jsonl`）

| action | 残る | 備考 |
|--------|------|------|
| ENTRY | ✓ | ExecutionService → DecisionsLogger.log → _write_decision_log |
| ENTRY_SIMULATED | ✓ | dry_run 時の擬似エントリー |
| SKIP | ✓ | 同上 |
| BLOCKED | ✓ | フィルタ NG 等 |
| EXIT | ✓ | 実ポジション決済 |
| EXIT_SIMULATED | ✓ | dry_run 時の擬似決済 |

※ Live の 1 tick 1 判断は、いずれも ExecutionService 経由で上記のいずれかとして decisions.jsonl に 1 行ずつ書かれる。

### 3.2 ui_events（ログファイル: `logs/ui_events.jsonl`）

| 種別 | 残る | 備考 |
|------|------|------|
| ENTRY（発注実行時） | ✓ | trade_service.open_position 内で EVENT_STORE.add(kind="ENTRY", ...) |
| CLOSE（決済時） | ✓ | 決済処理で EVENT_STORE.add(kind="CLOSE", ...) |
| guard_entry_denied / guard_streak_denied 等 | ✓ | kind="INFO" で記録 |
| TRAIL（トレール更新） | ✓ | trailing_hook から EVENT_STORE.add(kind="TRAIL", ...) |
| **SKIP/BLOCK 単体** | ✗ **残っていない** | SKIP/BLOCK は decisions.jsonl のみ。ui_events には INFO としての guard_* はあるが、一般的な「今 tick は SKIP」というイベントは ui_events には書かない。 |

### 3.3 ops_history（サービス経由の履歴）

| 種別 | 残る | 備考 |
|------|------|------|
| ENTRY 結果・ポジション開設 | ✓ | trade_service / ops_service 経由で append_ops_result |
| CLOSE 結果・決済 | ✓ | 同上 |
| **SKIP/BLOCK 単体** | ✗ **残っていない** | ポジションを張らない判断のため、ops_history の 1 レコードとしては記録しない。decisions.jsonl で観測可能。 |

### 3.4 「ここは残っていない」の明示

- **SKIP / BLOCK**:  
  - **decisions.jsonl**: 残る。  
  - **ui_events**: 残らない（ENTRY/CLOSE/INFO/TRAIL のみ）。  
  - **ops_history**: 残らない（ポジションに紐づく結果のみ）。
- 上記により、「ログに残らない判断」は存在しない。  
  SKIP/BLOCK は少なくとも decisions.jsonl に必ず残る。

---

## 4. ログ形式（成功条件）

- **開始時**: `[trade_loop] started mode=... dry_run=... symbol=USDJPY-`（1 箇所のみ）
- **停止時**: `[trade_loop] stopped reason=...`（全停止経路で共通）
- 判断: すべて decisions.jsonl / ui_events / ops_history のいずれかに上記のとおり残る。

---

## 5. 実施した変更（コード）

- `app/gui/main.py`
  - 開始ログをテンプレ通り `mode= dry_run= symbol=` に統一（1 箇所のみ）。
  - 停止ログを `[trade_loop] stopped reason=...` に統一（従来の `stopped symbol=` を `reason=` に変更）。
  - 上記以外の起動点・停止経路の追加は行っていない（推測での配線なし）。

---

## 6. 禁止事項の遵守

- 起動点を増やしていない（開始ログは start() の 1 箇所のみ）。
- 「たぶんここだろう」で配線を追加していない。
- GUI から MT5 / core を直接触っていない（既存の GUI → services → core の境界を維持）。
- 仮ログ・TODO は残していない。

---

## 7. 次のステップ（T-62）

この Step は T-62（Control タブ ON 配線）の前提。  
「始まった / 止まった」が 100% 観測できる状態になっている。売買ロジックの正しさは T-62 以降で扱う。
