# Runtime Schema v1 / v2

## 概要

`decisions.jsonl` の `runtime` フィールドの標準スキーマ定義。

- **v1**: 基本キーのみ（schema_version, ts, spread_pips, open_positions, max_positions）
- **v2**: v1 に加えて、追加キー（symbol, mode, source 等）を含む（推奨）

## 必須キー（v1/v2 共通）

| キー | 型 | 説明 |
|------|-----|------|
| `schema_version` | `int` | スキーマバージョン（`1` または `2`） |
| `ts` | `str` | タイムスタンプ（JST ISO形式、例: `"2025-12-20T20:49:00+09:00"`） |
| `spread_pips` | `float` | 現在のスプレッド（pips単位） |
| `open_positions` | `int` | 現在のオープンポジション数（0以上） |
| `max_positions` | `int` | 最大ポジション数（1以上） |

## v2 追加キー

| キー | 型 | 説明 |
|------|-----|------|
| `symbol` | `str` | シンボル名（例: `"USDJPY-"`） |
| `mode` | `str \| None` | 実行モード（例: `"live"`, `"demo"`, `"backtest"`）。None 許容 |
| `source` | `str \| None` | データソース（例: `"mt5"`, `"csv"`, `"stub"`）。None 許容 |
| `timeframe` | `str \| None` | タイムフレーム（例: `"M5"`, `"H1"`）。オプション、None 許容 |
| `profile` | `str \| None` | プロファイル名（例: `"michibiki_std"`）。オプション、None 許容 |
| `price` | `float \| None` | 現在価格。オプション、None 許容 |

### source（生成元の語彙）

`source` は以下の語彙のいずれかを使用し、意味を固定する：

- `"stub"`      : ExecutionStub / demo / dryrun 経路
- `"mt5"`       : MT5 経由の live 実行
- `"backtest"`  : バックテスト実行経路

※ 新しい実行経路を追加する場合は、必ずここに語彙を追記すること。

## 型チェック

- `schema_version`: `int` のみ（`bool` は不可）
- `open_positions`: `int` のみ（`bool` は不可）
- `max_positions`: `int` のみ（`bool` は不可）
- `spread_pips`: `int` または `float`（数値型）
- `ts`: `str`（文字列型）

## Deprecated キー

以下のキーは非推奨であり、警告が出力されます（例外は発生しません）：

### Prefix マッチ
- `_sim_*` - すべての `_sim_` で始まるキー

### Exact マッチ
- `runtime_open_positions` - 代わりに `open_positions` を使用
- `runtime_max_positions` - 代わりに `max_positions` を使用
- `sim_pos_hold_ticks` - 代わりに `pos_hold_ticks` を使用（`runtime` には含めない）

## 検証方針

- **必須キー欠落**: `strict=True` の場合、`ValueError` を発生
- **型不正**: `strict=True` の場合、`TypeError` を発生
- **Deprecated キー**: 警告のみ（例外は発生しない）
- **v1/v2 互換性**: `validate_runtime()` は v1 と v2 の両方を許容する
  - v1 の必須キー（schema_version, ts, spread_pips, open_positions, max_positions）は v2 でも必須
  - v2 の追加キー（symbol, mode, source 等）は v2 の場合のみ型チェック（None 許容）

## 生成方法

`app/services/trade_state.py` の `build_runtime()` 関数を使用：

```python
from app.services import trade_state

runtime = trade_state.build_runtime(
    symbol="USDJPY-",
    market=market,  # オプション
    ts_str=None,    # オプション（None の場合は現在時刻）
    spread_pips=None,  # オプション（None の場合は market から取得、それでも無ければ 0.0）
)
```

この関数は以下を保証します：
- 必須キーがすべて含まれる
- 型が正しい
- `validate_runtime(runtime, strict=True)` を通過する
- `schema_version=2` を返す（v2 推奨）

**v2 追加キーの指定例:**
```python
runtime = trade_state.build_runtime(
    symbol="USDJPY-",
    market=market,
    mode="live",  # v2 追加
    source="mt5",  # v2 追加
    timeframe="M5",  # v2 追加（オプション）
    profile="michibiki_std",  # v2 追加（オプション）
)
```

## 検証

`validate_runtime()` 関数（`app/services/execution_stub.py`）で検証されます。

検証は `_write_decision_log()` の出口で自動的に実行されます。

## 警告タグ

すべての警告は `[runtime_schema]` プレフィックスで統一されています。

検索例：
```powershell
Select-String -Pattern "\[runtime_schema\]" -SimpleMatch
```

## decision_context（判断材料の分離）

`decision_context` は **判断材料** を格納するフィールドで、`runtime` とは分離されています。

### 構造

```json
{
  "decision_context": {
    "ai": {
      "prob_buy": 0.5187,
      "prob_sell": 0.4813,
      "model_name": "LightGBM_clf",
      "threshold": 0.45
    },
    "filters": {
      "filter_pass": false,
      "filter_reasons": ["volatility"],
      "spread": 0.5,
      "adx": 15.2,
      "filter_level": 3
    },
    "decision": {
      "action": "SKIP",
      "side": "BUY",
      "reason": "volatility",
      "blocked_reason": "volatility"
    },
    "meta": {}
  }
}
```

### 必須キー

- `ai` (dict | None): AI判定結果（prob_buy, prob_sell, model_name, threshold 等）
- `filters` (dict | None): フィルタ結果（filter_pass, filter_reasons, spread, adx 等）
- `decision` (dict | None): 決定内容（action, side, reason, blocked_reason 等）
- `meta` (dict | None): メタ情報

### runtime との分離

- **runtime**: 環境状態のみ（schema_version, ts, spread_pips, open_positions, max_positions, symbol, mode, source 等）
- **decision_context**: 判断材料のみ（ai, filters, decision, meta）

`runtime` に判断材料（ai, filters, decision, decision_detail, decision_context）が混入した場合は警告が出力されます。

### 検証

`validate_decision_context()` 関数（`app/services/execution_stub.py`）で検証されます。

検証は `_write_decision_log()` の出口で自動的に実行されます（warn-only、strict=False）。

### 警告タグ

すべての警告は `[decision_context_schema]` プレフィックスで統一されています。

