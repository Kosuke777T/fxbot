# Step1 観測結果まとめ

## 1. OHLC取得の正規ルート（確定）

### MT5からの取得
- **ファイル**: `scripts/make_csv_from_mt5.py`
- **関数**: `fetch_rates(symbol, tf, start_ts, end_ts)` (L203-275)
- **MT5 API**:
  - 優先: `mt5.copy_rates_range(symbol, timeframe, from, to)` (L187)
  - フォールバック: `mt5.copy_rates_from(symbol, timeframe, from, count)` (L231)
- **タイムフレーム定数**: `mt5.TIMEFRAME_M5` (L62)
- **時刻変換**: MT5のUnix秒(UTC) → JST naive datetime (L120-129)

### CSV保存
- **ファイル**: `app/services/data_guard.py`
- **関数**: `ensure_data(symbol_tag, timeframe, start_date, end_date, ...)` (L21-78)
- **呼び出し**: `scripts/make_csv_from_mt5.py` を subprocess で実行 (L44-63)
- **パス生成**: `csv_path(symbol_tag, timeframe, layout="per-symbol")` (L12-19)
  - 例: `data/USDJPY/ohlcv/USDJPY_M5.csv`

### CSV読み込み（Visualize用）
- **ファイル**: `app/services/visualization_service.py`
- **関数**: `get_recent_ohlcv(symbol, timeframe, count, until)` (L52-123)
- **パス**: `data_guard.csv_path(symbol_tag, timeframe, layout="per-symbol")` (L71)
- **列**: `time`, `open`, `high`, `low`, `close`, `tick_volume`, `spread`, `real_volume`
- **時刻形式**: pandas `pd.to_datetime()` でパース可能なJST naive datetime

## 2. Visualizeが参照するCSV仕様（確定）

### パス
- **レイアウト**: `per-symbol`
- **パス**: `data/{symbol_tag}/ohlcv/{symbol_tag}_{timeframe}.csv`
- **例**: `D:\fxbot\data\USDJPY\ohlcv\USDJPY_M5.csv`

### 列
- **必須**: `time` (datetime), `open`, `high`, `low`, `close` (float)
- **オプション**: `tick_volume`, `spread`, `real_volume` (int)

### 時刻形式
- **形式**: JST naive datetime (例: `'2026-01-16 15:00:00'`)
- **保存**: `make_csv_from_mt5.py` の `jst_from_mt5_epoch()` で変換 (L120-129)
- **読み込み**: `pd.to_datetime(df["time"], errors="coerce")` (L93)

### 観測結果（2026-01-19時点）
- **CSV末尾timestamp**: `2026-01-16 15:00:00`
- **CSV存在**: `True`
- **列**: `['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']`

## 3. MT5最新tsとCSV末尾tsの差（要実行観測）

### 観測方法
```python
# MT5から最新M5バーを取得
import MetaTrader5 as mt5
from app.core.symbol_map import resolve_symbol
import pandas as pd

mt5.initialize()
rates = mt5.copy_rates_from(resolve_symbol('USDJPY-'), mt5.TIMEFRAME_M5, 0, 1)
if rates and len(rates) > 0:
    df = pd.DataFrame(rates)
    # UTC epoch秒 → JST naive
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_convert('Asia/Tokyo').dt.tz_localize(None)
    mt5_latest = df['time'].iloc[-1]
mt5.shutdown()

# CSV末尾を取得
csv_path = Path('D:/fxbot/data/USDJPY/ohlcv/USDJPY_M5.csv')
df_csv = pd.read_csv(csv_path, parse_dates=['time'])
csv_tail = df_csv['time'].max()

# 差を計算（M5は5分間隔）
diff_minutes = (mt5_latest - csv_tail).total_seconds() / 60
diff_bars = int(diff_minutes / 5)
```

### 観測で確定した事実（要実行）
- **CSV末尾ts**: `2026-01-16 15:00:00`
- **MT5最新ts**: （実行観測が必要）
- **遅れ本数**: （MT5最新ts取得後に計算）

## 4. AI推論の正規ルート（確定）

### AISvc.predict()
- **ファイル**: `app/services/ai_service.py`
- **関数**: `AISvc.predict(X, no_metrics=False)` (L332-523)
- **入力**: `np.ndarray` または `Dict[str, float]` (特徴量)
- **出力**: `AISvc.ProbOut` (p_buy, p_sell, p_skip)

### AISvc.get_live_probs()
- **ファイル**: `app/services/ai_service.py`
- **関数**: `AISvc.get_live_probs(symbol)` (L959-1058)
- **特徴**: tickから特徴量を自動生成して推論
- **出力**: `dict[str, float]` (p_buy, p_sell, p_skip, atr_for_lot)

### 推論結果の保存先（要確認）
- **decisions_*.jsonl**: `logs/decisions_YYYY-MM-DD.jsonl` (execution_service.py, execution_stub.py)
- **prob専用CSV**: （既存仕様を確認が必要）

## 5. JobScheduler（確定）

### 初期化
- **ファイル**: `app/services/job_scheduler.py`
- **クラス**: `JobScheduler` (L14-503)
- **設定ファイル**: `configs/scheduler.yaml` (L29)
- **GUI起動時**: （main.pyで確認が必要）

### ジョブ実行
- **関数**: `run_pending()` (L256-255)
- **run_always**: `true` のジョブは常時実行 (L66, L74, L80)
- **定期実行**: `weekday/hour/minute` でスケジュール (L87-90)

### 現在のジョブ（scheduler.yaml）
- `always_job`, `always_job2`, `always_test`: `run_always: true` (L66, L73, L80)

## 次のステップ（Step2）

1. **services層に新規関数追加**:
   - `app/services/ohlcv_service.py` を新規作成（または既存サービスに追加）
   - `ensure_ohlcv_uptodate(symbol, timeframe="M5")` を実装

2. **MT5最新ts取得**:
   - `scripts/make_csv_from_mt5.py` の `fetch_rates()` ロジックを再利用
   - または `mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M5, 0, 1)` で最新1本を取得

3. **CSV末尾ts取得**:
   - `data_guard.csv_path()` でパス取得
   - `pd.read_csv()` で読み込み、`df['time'].max()` で末尾ts取得

4. **不足分のみ取得・追記**:
   - `make_csv_from_mt5.py` の `ensure_csv_for_timeframe()` ロジックを再利用
   - または `fetch_rates()` で不足分を取得してCSVにappend

5. **AI推論実行**:
   - 新規行に対して `AISvc.get_live_probs(symbol)` または `AISvc.predict()` を呼び出し
   - 結果を保存（既存仕様に合わせる）

6. **JobScheduler登録**:
   - `configs/scheduler.yaml` に新規ジョブ追加
   - `run_always: true` または `weekday: null, hour: null, minute: null` で1分ごと実行
