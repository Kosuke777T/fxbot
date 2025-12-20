# FX AI Bot (Python + MT5 + PyQt6)
Python 3.13 / PyQt6 / MetaTrader5

## 環境セットアップ

### 仮想環境の作成と有効化

```powershell
# Windows PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1

# WSL / Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 依存パッケージのインストール

```powershell
pip install -r requirements.txt
```

## 開発・検証手順

### Runtime Schema 検証（必須）

コミット前やCIで実行する統合検証スクリプト：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/smoke_all.ps1
```

このスクリプトは以下を検証します：
1. Demo側の runtime schema 警告が 0 件であること
2. LIVE側の runtime schema 警告が 0 件であること（負のテスト含む）
3. decisions.jsonl に旧キー（`_sim_*`, `runtime_open_positions`, `runtime_max_positions`, `sim_pos_hold_ticks`）が混入していないこと

**Exit Code:**
- `0`: すべての検証が成功
- それ以外: 検証失敗（詳細はエラーメッセージを参照）

**個別実行:**
- Demo側: `python -X utf8 scripts/demo_run_stub.py`
- LIVE側: `python -X utf8 tools/live_runtime_smoke.py`
- 負のテスト: `python -X utf8 tools/live_runtime_smoke.py --inject-runtime-warn`

**注意:**
- `scripts/smoke_all.ps1` は実行に使用された Python（command / executable / version）を冒頭に必ず表示します。
- `scripts/demo_run_stub.py` は `no_metrics=True` 既定で metrics は更新されません。metrics 確認は `tools/live_runtime_smoke.py` または `tools/backtest_run.py` を使用してください。

**詳細仕様:**
- Runtime Schema v1 の定義: `docs/runtime_schema.md`

## データ更新手順

### 概要

MT5 ターミナルから OHLCV（USDJPY）データを CSV へエクスポートし、学習データとして使用します。

**3台の PC（開発A / B / 運用VPS）で一貫した出力先を実現するため、以下の環境変数設定を必須とします。**

### ステップ1: 環境変数 `FXBOT_DATA` の設定

データディレクトリを指定する環境変数を設定します。以下から選択してください。

#### 方法A: 開発環境（.env ファイル）

プロジェクトルートに `.env` ファイルを作成し、以下を記述：

```bash
# .env
FXBOT_DATA=C:\Users\macht\OneDrive\fxbot\data
```

**メリット**：プロジェクト毎に設定可能、git管理外（`.gitignore` に記載）

**デメリット**：スクリプト実行時に python-dotenv で読み込む必要がある

#### 方法B: 本番環境（setx コマンド / システム環境変数）

PowerShell で以下を実行し、システムレベルに永続化：

```powershell
# 運用VPS の場合
setx FXBOT_DATA "C:\fxbot\data"

# または OneDrive パスの場合
setx FXBOT_DATA "C:\Users\macht\OneDrive\fxbot\data"
```

**メリット**：永続化、システムワイドで有効（再ログイン後）

**デメリット**：管理者権限が必要、全ユーザーに見える可能性あり

#### 方法C: セッション環境変数（テスト用）

PowerShell セッション内でのみ有効：

```powershell
$env:FXBOT_DATA = 'C:\Users\macht\OneDrive\fxbot\data'
```

**用途**：テスト・検証用（セッション終了時に消える）

### ステップ2: MT5 ターミナルの準備

- MetaTrader 5 を起動
- 口座にログイン（ユーザー名 / パスワード は `config.yaml` の `mt5_login` / `mt5_password` で指定）
- ターミナルを起動したままにしておく（スクリプト実行中に使用）

### ステップ3: CSV エクスポート

Python スクリプトで USDJPY の複数タイムフレーム（M5, M15, H1 等）を取得：

#### 基本的な実行方法

```powershell
# 環境変数 FXBOT_DATA が設定されている場合（推奨）
python scripts\make_csv_from_mt5.py --symbol USDJPY --timeframes M5 M15 H1 --start 2020-11-01

# または --data-dir で明示指定
python scripts\make_csv_from_mt5.py --symbol USDJPY --timeframes M5 M15 H1 --data-dir C:\Users\macht\OneDrive\fxbot\data
```

#### 出力先のレイアウト指定

`--layout` オプションで保存先構造を選択：

```powershell
# per-symbol: 通貨ペア毎のサブディレクトリ（推奨）
python scripts\make_csv_from_mt5.py --symbol USDJPY --timeframes M5 M15 H1 --layout per-symbol
# 出力先: <FXBOT_DATA>/USDJPY/ohlcv/USDJPY_M5.csv など

# flat: data ディレクトリ直下
python scripts\make_csv_from_mt5.py --symbol USDJPY --timeframes M5 M15 H1 --layout flat
# 出力先: <FXBOT_DATA>/USDJPY_M5.csv など
```

#### MT5 terminal.exe のカスタムパス指定

GaitameFinest等のターミナルを使う場合：

```powershell
python scripts\make_csv_from_mt5.py \
  --symbol USDJPY \
  --timeframes M5 M15 H1 \
  --terminal "C:\Program Files\MetaTrader 5\terminal64.exe"
```

### ステップ4: データの確認

生成された CSV が正常に保存されたか確認：

```powershell
# per-symbol の場合
python -c "import pandas as pd; df = pd.read_csv('data/USDJPY/ohlcv/USDJPY_M5.csv', parse_dates=['time']); print(f'Rows: {len(df)}, From: {df[\"time\"].min()}, To: {df[\"time\"].max()}')"

# または fxbot_path モジュールを使用
python -c "import pandas as pd; from fxbot_path import get_ohlcv_csv_path; p = get_ohlcv_csv_path('USDJPY','M5'); df = pd.read_csv(p, parse_dates=['time']); print(f'Rows: {len(df)}, From: {df[\"time\"].min()}, To: {df[\"time\"].max()}')"
```

### パス解決の優先順位

スクリプトは以下の順序でデータディレクトリを決定します（先行するものが優先）：

1. `--data-dir` コマンドライン引数
2. `FXBOT_DATA` 環境変数
3. `<プロジェクトルート>/data`
4. `<カレントディレクトリ>/data`

既存ディレクトリが見つかった場合は、それを使用します。見つからない場合は、プロジェクトルートの `data` ディレクトリを作成・使用します。

### トラブルシューティング

#### MT5 接続エラー
```
[fatal] MT5 initialize 失敗: ...
```
**解決方法**：
- MetaTrader 5 ターミナルが起動しているか確認
- ターミナルで口座にログインしているか確認
- `--terminal` オプションでターミナルパスを明示してみる

#### CSV ファイルが見つからない
```
FileNotFoundError: data/USDJPY/ohlcv/USDJPY_M5.csv
```
**解決方法**：
- `FXBOT_DATA` 環境変数が正しく設定されているか確認
- `python -c "from fxbot_path import get_ohlcv_csv_path; print(get_ohlcv_csv_path('USDJPY','M5'))"` でパスを確認
- ディレクトリの書き込み権限があるか確認（OneDrive の同期状態も確認）

#### 既存 CSV への追記に失敗する
```
ValueError: time column not found
```
**解決方法**：
- 既存 CSV の形式が正しいか確認（カラム名: time, open, high, low, close など）
- スクリプトを再度実行すれば、新規 CSV として再作成されます

## パス管理（fxbot_path.py）

プロジェクト内で共通的に使用する `fxbot_path.py` モジュールが以下のヘルパー関数を提供します：

- `get_project_root()`: プロジェクトルート（fxbot_path.py の親ディレクトリ）を返す
- `get_data_root(cli_data_dir=None)`: データルートを優先順位付きで決定
- `get_ohlcv_csv_path(symbol, timeframe, data_root=None, layout='per-symbol')`: OHLCV CSV のパスを統一的に生成

これらを他のスクリプトから使用することで、3台のPC間でのパス不統一を防ぎます。

### 使用例

```python
from fxbot_path import get_ohlcv_csv_path
import pandas as pd

# USDJPY M5 の CSV パスを取得（自動的に FXBOT_DATA 環境変数を参照）
csv_path = get_ohlcv_csv_path('USDJPY', 'M5')
df = pd.read_csv(csv_path)
print(df.head())
```

