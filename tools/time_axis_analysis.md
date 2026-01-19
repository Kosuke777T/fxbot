# 時刻軸監査レポート

## Step 1: 影響範囲の抽出結果
- 検索結果: `tools/time_axis_audit.txt` に704件のマッチを保存

## Step 2: チョークポイント（MT5時刻系の入口/出口）

### 主要な時刻変換関数

#### 1. `jst_from_mt5_epoch()` (scripts/make_csv_from_mt5.py:125-134)
- **役割**: MT5のepoch秒（UTC基準）をJST naive datetimeに変換
- **実装**: `pd.to_datetime(series, unit="s", utc=True)` → `tz_convert("Asia/Tokyo")` → `tz_localize(None)`
- **問題点の可能性**: MT5 epochがUTC基準と仮定しているが、実際はserver時刻基準の可能性

#### 2. `_to_utc_naive()` (scripts/make_csv_from_mt5.py:153-161)
- **役割**: JST naive → UTC naive（MT5 API用）
- **実装**: naive datetimeをJSTとして解釈 → UTC変換 → tzinfo削除
- **問題点の可能性**: 修正済み（JST前提に変更済み）

#### 3. `_to_local_naive()` (scripts/make_csv_from_mt5.py:164-167)
- **役割**: JST naive → JST naive（ローカルnaive要求環境向け）
- **実装**: 単純にtzinfoを削除
- **問題点の可能性**: 低（単純なtzinfo削除のみ）

#### 4. `_to_utc_aware()` (scripts/make_csv_from_mt5.py:169-175)
- **役割**: JST naive → UTC aware
- **実装**: JSTとして解釈 → UTC変換 → UTC aware
- **問題点の可能性**: 低（aware datetimeとして扱うため）

### MT5 API呼び出し箇所

#### 1. `_range_attempts()` (scripts/make_csv_from_mt5.py:179-223)
- **copy_rates_range()呼び出し**: 3方式（utc_naive, local_naive, utc_aware）で試行
- **時刻変換**: `_to_utc_naive()`, `_to_local_naive()`, `_to_utc_aware()` を使用
- **問題点の可能性**: `_to_utc_naive()` は修正済みだが、MT5 epoch解釈が問題の可能性

#### 2. `fetch_rates()` フォールバック (scripts/make_csv_from_mt5.py:266-318)
- **copy_rates_from()呼び出し**: `dt_to = _to_utc_naive(end_ts)` でUTC naiveを生成
- **時刻変換**: `jst_from_mt5_epoch()` でepoch→JST変換
- **問題点の可能性**: `dt_to` の生成が正しくても、epoch解釈がズレている可能性

## Step 3: 観測ログ追加
- **追加箇所**: `main()` 内、シンボル解決後（scripts/make_csv_from_mt5.py:549-570）
- **観測内容**:
  - `now_epoch = int(time.time())` (ローカル時刻)
  - `tick_epoch = int(tick.time)` (MT5サーバ時刻)
  - `delta_sec = tick_epoch - now_epoch`
  - `delta_hours_round = round(delta_sec / 3600)` (server_offset候補)

## Step 4: 統一処理が悪さをしているかの判定

### 疑わしい箇所

#### 1. `jst_from_mt5_epoch()` (scripts/make_csv_from_mt5.py:125-134)
```python
s = pd.to_datetime(series, unit="s", utc=True)
```
- **問題**: MT5 epochを`utc=True`で解釈しているが、実際はserver時刻基準の可能性
- **影響**: epoch→JST変換時にoffsetが考慮されず、時刻がズレる
- **判定条件**: `delta_hours_round != 0` の場合、この変換がズレ源

#### 2. `_to_utc_naive()` 修正前の動作
- **修正前**: naive datetimeをUTCと誤解釈していた可能性
- **修正後**: JST前提に変更済み（修正済み）

#### 3. フォールバック内の `dt_to` 生成
- **箇所**: `scripts/make_csv_from_mt5.py:268`
- **実装**: `dt_to = _to_utc_naive(end_ts)`
- **問題点**: `_to_utc_naive()` は修正済みだが、MT5が期待する時刻系とズレている可能性

## Step 5: 原因断定（観測ログ実行後に確定）

### 確認すべき観測結果

1. **server_offsetの確定**
   - `[time_audit]` ログから `delta_hours_round` を確認
   - 0以外（例: +2, +9）の場合、MT5サーバ時刻とローカル時刻にズレがある

2. **epoch解釈の確認**
   - `jst_from_mt5_epoch()` が `utc=True` でepochを解釈している
   - server_offsetが0以外の場合、この解釈がズレ源になる可能性が高い

3. **影響範囲**
   - `copy_rates_range()` が返す時刻が未来に見える（server_offset分ズレ）
   - `copy_rates_from()` の `dt_to` がズレて、フィルタで全落ち（collected 0）

### 次の最小差分修正案

#### 案1: `jst_from_mt5_epoch()` にserver_offsetを考慮
- `jst_from_mt5_epoch()` 内で `delta_hours_round` を参照
- epoch変換時にoffsetを加算/減算
- **問題**: server_offsetをどこから取得するか（グローバル変数？引数？）

#### 案2: MT5 epoch解釈をserver時刻基準に変更
- `pd.to_datetime(series, unit="s", utc=True)` を `utc=False` に変更
- または、server_offsetを考慮した変換に変更
- **問題**: MT5 epochが本当にserver時刻基準か確認が必要

## 次のアクション

1. **観測ログ実行**: `python -X utf8 .\scripts\make_csv_from_mt5.py --symbol USDJPY --timeframes M5 --start 2026-01-19 --end 2026-01-20`
2. **server_offset確定**: ログから `delta_hours_round` を確認
3. **原因特定**: server_offsetが0以外の場合、`jst_from_mt5_epoch()` が原因の可能性が高い
4. **修正実施**: 観測結果に基づいて最小差分で修正
