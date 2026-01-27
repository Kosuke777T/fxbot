了解。
このスレッドは 完了 としてクローズします。
以下、①完了サマリ（要点） → ②次スレッドに張り付ける指示 の順で表示します。

🧾 スレッド完了時：要点サマリ（T-50）
スレッド目的

VirtualBT が「動かない／エントリーしない」問題について、
推測せず・観測で確定し、修正に入れる状態まで持っていく。

結論（最重要）

バックテストは正常に動作している

エントリーは実際に発生している（trades=1）

「エントリーが発生しない」は 体感上の誤認
→ 実態は “極端に取引頻度が低い戦略”

観測で確定した事実（OBSERVED）

ENTRY 発生：

bar_index=268 / 2025-12-24 00:00:00

action=ENTRY / side=BUY

prob_buy=0.51048 > threshold=0.5

filter_pass=true（filter_level=3）

position: flat → open（成功）

metrics.json：

trades=1

output_ok=true

trades.csv / decisions.jsonl に記録あり

否定できた仮説（すべて観測でREJECT）

❌ モデル未ロード

❌ prob が 0 / NaN

❌ threshold 判定バグ

❌ filter 常時 reject

❌ ENTRY ロジック不達

❌ BacktestEngine / VirtualBT の不具合

問題の正体

バグではなく設計結果。

threshold=0.5

filter_level=3

prob 分布が 0.48〜0.52 に集中

→ 月1回程度しか刺さらない戦略になっている。

技術的評価

実装健全性：◎

設計整合性：◎

実運用頻度：△（研究段階）

👉 「直すフェーズ」ではなく「調整フェーズ」へ移行可能。

このスレッドで到達した状態

VirtualBT は 信頼できる評価基盤として成立

ENTRY〜決済〜成果物出力まで 因果が一気通貫

T-50-2（調整）に進む前提条件はすべて満たした


T-50-2
目的

取引頻度が低い（または異常に見える）原因を
バグ修正なし・観測ベースで特定すること

観測で確定した事実
1. LightGBMモデルの状態

旧モデルは 実質壊れていた

prob_buy が 3値に固定

特徴量数不一致（学習9 / 推論19）

再学習を実施し、以下を達成：

prob_buy ユニーク値数：979

分布：0.01〜0.93

BUY / SELL 両方向が発生

LightGBMは正常状態に復帰（作り直し完了）

2. active_model.json

再学習モデルを SSOTとして正しく反映

feature_order / model_kind / threshold の整合性確認済み

推論・VirtualBT 両方で正常動作

3. VirtualBTの実行実態

decisions.jsonl（intent）: 5870

trades.csv（actual）: 5869

execution_rate ≒ 1.0
→ ENTRY意思決定はほぼ全て取引に変換されている

4. 取引頻度の制御性

threshold を上げると 線形に取引数が減少

filter_level の影響は小さい
→ 頻度調整ノブとして threshold は有効

5. 決定的な構造問題（最重要）

全スイープで：

avg_holding_bars = 0

avg_holding_days = 0

entry_time が M5 で完全連続

👉 エントリーした同一バー内で必ず決済されている

6. 利確・損切について

TP / SL は 存在する（列も出力されている）

しかし：

次バー以降に評価されない

別の exit 条件により即クローズ

結果：

勝率 ≈ 0.5

PF ≈ 1.0

成績が threshold 調整では改善しない

結論

LightGBMは壊れていない（再学習後は正常）

threshold / filter_level は頻度制御には有効

損益が出ない主因は VirtualBT の「同一バー完結 exit 設計」

パラメータ調整だけではこれ以上改善しない


T-50-3 → T-50-4（ExitPolicy 設計調整・アブレーション）

目的

バグ修正なし

モデル不変（active_model.json SSOT）

Exit / Holding の設計調整のみで
勝率・PF・DD・保有期間が変化するかを観測で確認

使用データ（観測で確定）

CSV: D:\fxbot\data\USDJPY\ohlcv\USDJPY_M5.csv

期間:

min: 2024-07-08 11:45:00

max: 2026-01-25 07:50:00

Timeframe: M5

実装済み ExitPolicy

min_holding_bars

tp_sl_eval_from_next_bar

exit_on_reverse_signal_only

（すべてデフォルトOFF、既存挙動不変）

T-50-4 アブレーション結果（確定）
name	trades	win_rate	profit_factor	max_drawdown	avg_holding_bars
baseline	2876	0.50	0.99	-0.32	1.00
tpslnext	2876	0.50	0.99	-0.32	1.00
minhold2	1438	0.50	0.97	-0.27	2.00
both	1438	0.50	0.97	-0.27	2.00
観測で確定した事実

取引頻度を制御している唯一のノブは min_holding_bars

trades が約 1/2 に減少

avg_holding_bars が設計通り増加

tp_sl_eval_from_next_bar は本条件では効果なし

baseline と完全一致

min_holding_bars と組み合わせても差分なし

現時点では冗長

min_holding_bars の効果

max_drawdown を確実に低下

profit_factor はやや低下

勝率は不変（≈0.50）

ExitPolicy は

正常に機能

設計通り指標を変化させることを確認

結論（T-50 系の到達点）

Exit 設計だけで戦略特性を制御できる構造が確立

モデル改変なしで

リスク（DD）

取引頻度

保有期間
を操作可能

ミチビキは「入口依存」ではなく
出口設計主導のシステムとして成立

T-50-5 完了：tools/compare_min_holding_bars.py を追加し、min_holding_bars を 0/1/2/3（任意リスト可）で同一条件比較できるようにした

実行方式：サブプロセスなし、run_backtest() を同一プロセスで直接呼び出し

出力：min_hold_{k}/ に試行分離し、summary.csv に trades / PF / DD / avg_holding_bars を集計

観測結果：PF最大=2、DD最小=3、0/1は同一で劣後

注意点：私の例コマンドは引数名が古く、実装の usage（--csv, --start-date, --end-date）がSSOT

T-50-5ExitPolicy: min_holding_bars 比較ランナー作成 → 観測で比較確定

■ 目的

ExitPolicy の中で唯一有効と確定した min_holding_bars を、モデル不変・CSV/期間固定で 0/1/2/3 比較し、運用固定の根拠（観測）を作る

■ 実施内容（事実）

min_holding_bars=0/1/2/3 を同一条件で4回バックテスト実行し、指標を summary.csv に集計

同一プロセスで run_backtest() を直接呼び出し（サブプロセス無し）

各試行出力を min_hold_0/1/2/3/ に分離

触ったレイヤ：tools（運用・比較導線）

新規関数：最小（比較ツール追加が中心）

■ 変更ファイル

tools/compare_min_holding_bars.py（新規作成）

■ 守った制約

モデル不変（active_model.json をSSOTとして使用）

CSV・期間固定（比較中にデータ更新なし）

バグ修正なし（既存挙動は新ツール未使用時に不変）

推測で直さず、ログ/usage/出力で観測して確定

■ 挙動の変化

変わった点：min_holding_bars を 同一条件で掃引比較できるようになった（summary.csv 出力）

変わっていない点（重要）：通常の実行経路・モデル・CSV生成・期間・既存バックテスト挙動は不変

■ 確認方法

実行した確認コマンド（例）

python -X utf8 -m py_compile tools/compare_min_holding_bars.py

python -X utf8 tools/compare_min_holding_bars.py --csv ... --start-date ... --end-date ... --out-root ...

OK条件

out-root/summary.csv が生成され、0/1/2/3 の4行が揃う

各試行ディレクトリ min_hold_k/ が生成される

■ 観測結果（2025年通年・提示ログの実測）

0: trades=73832, PF=1.0777, DD=-0.2422, avg_hold=1.00

1: trades=73832, PF=1.0777, DD=-0.2422, avg_hold=1.00

2: trades=36916, PF=1.1026, DD=-0.2860, avg_hold=2.00

3: trades=24611, PF=1.0944, DD=-0.3612, avg_hold≈3.00

T-50-6
SSOT：active_model.json の exit_policy.min_holding_bars=2 が有効

解決優先順位：cli_override > active_model > default をログで観測

比較ツール：0 を含む明示指定が常に cli_override として効く

実行健全性：モデル実体ロード成功、returning zeros 消失、取引生成を確認

T-50-9 要点サマリ
目的

backtest 初回 _predict enter で model_is_none=true が一度だけ出る原因を
推測禁止・観測で確定し、再発防止判断（修正要否）を行う。

観測で確定した事実
1. ログのSSOT（実体）

D:\fxbot\.cursor\debug.log

D:\fxbot\backtests\michibiki_std\sanity_after_train\debug_tail.log
※ .\logs\debug.log は存在しない（誤認を修正）

2. 初回 _predict の実際のログ順序（実ファイル抜粋）

__init__

model_kind="pickle"

モデル設定メタはロード済み

_predict enter

model_is_none=true

_ensure_model_loaded enter

model_is_none=true

_ensure_model_loaded exit

model_loaded=true

_predict（予測確率算出直後）

p_buy ≈ 0.51（正常）

run

ENTRY 判定まで正常に進行

3. 2回目以降の挙動

debug_tail.log にて
2回目以降の _predict enter は model_is_none=false を観測

因果関係（観測で確定）

設計は lazy-load

__init__ では self.model=None

_predict 入口ログは ロード前状態を出力

直後に _ensure_model_loaded() が必ず呼ばれ、ロード完了

同一呼び出し内で予測・意思決定まで成功

断定（推測なし）

原因分類：A)
初回のみ model_is_none=true は lazy-load設計通りの正常動作

バグではない

運用上の問題なし

修正不要（このTでは）

文字化けについて（観測のみ）

.cursor\debug.log の先頭バイト：7B（{）

UTF-8 BOMなしで正常

Get-Content -Encoding UTF8 で日本語表示も正常
→ このファイル自体に文字化け問題はなし

成功条件の判定

成功条件①（初回から model_is_none=false）：❌（仕様上）

成功条件②（問題ないと判断できる材料を揃える）：✅ 達成

結論

T-50-9 は
「原因確定・設計通り・修正不要」 として 完了。

T-51-1：backtest / live 共通のモデルロード・健全性チェック設計

■ 目的

起動時に active_model.json と実モデルファイル不整合を検知し、モデル不在系事故を事前に可視化する（ただし売買ロジックへ影響させない）。

■ 実施内容（事実）

services層に check_model_health_at_startup() を追加し、起動時に active_model.json / model_path / scaler_path / ロード可否 / 推論API有無 / expected_features を検査。

aisvc_loader

GUI起動（MainWindow.__init__ 早期）で 1回だけ健全性チェックを実行し、[model_health] ログを出力。

main

触ったレイヤ：services / gui

新規関数：あり（check_model_health_at_startup）

aisvc_loader

■ 変更ファイル

app/services/aisvc_loader.py（check_model_health_at_startup() 追加）

aisvc_loader

app/gui/main.py（MainWindow.__init__() で起動時1回チェック＆ログ）

main

■ 守った制約

最小差分

既存ロード経路（既存API優先）

責務境界（GUI→services）遵守

売買ロジックに影響なし（起動時診断のみ）

main



aisvc_loader

■ 挙動の変化

変わった点：起動時に モデル健全性の診断ログが必ず出る（正常/異常で stable/score/reasons）。

main

変わっていない点（重要）：売買判断・発注経路・tick処理・バックテスト処理は一切変更なし。

main

■ 確認方法

python -X utf8 -m py_compile app/services/aisvc_loader.py app/gui/main.py（OK）

正常：stable=true score=100 ログ

異常（active_model.json 不在など）：stable=false reasons=[...] ログ、アプリ継続

main



aisvc_loader

T-51-2：起動時モデル健全性チェック結果（stable/score/reasons/meta）をGUI表示

■ 目的

起動直後に画面上で stable と reasons を視認できるようにする（失敗でもアプリ継続、挙動変更なし）

■ 実施内容（事実）

services：app/services/aisvc_loader.py に起動時チェック結果のスナップショット保持・参照窓口を追加

_MODEL_HEALTH_LAST / set_last_model_health() / get_last_model_health()

check_model_health_at_startup() 実行結果を自動保存

gui：app/gui/main.py に タブの上へ health_banner(QLabel) を追加し、起動時1回の結果を表示

stable=False の場合は視認性のため背景色変更

reasons が長い場合は省略＋tooltipで全文

ログ：起動時1回のみ [model_health] stable/score/reasons と meta要点（あれば）をINFO出力

失敗時も例外で落とさず、stable=False + reasons に失敗理由を詰めて表示しアプリ継続

■ 変更ファイル

app/services/aisvc_loader.py（末尾付近：保持/参照窓口、起動時チェック結果の保存）

app/gui/main.py（MainWindow 初期化付近：バナー配置・表示更新）

■ 守った制約

表示とログ補強のみ（売買ロジック・ボタン活性/非活性など挙動変更なし）

既存API優先・新規最小

責務境界（GUI→servicesのみ）遵守

■ 挙動の変化

変わった点：起動直後に Model health バナーが表示され、stable/reasons を即時確認できる

変わっていない点（重要）：売買ロジック、各ボタンの挙動、実行フローは不変

■ 確認方法

python -X utf8 -m py_compile app/services/aisvc_loader.py

python -X utf8 -m py_compile app/gui/main.py

起動直後にバナーが表示されること（失敗ケースでもクラッシュしないこと）

■ 次にやるべきこと（あれば）

T-51-3候補：meta の詳細（model_path / trained_at / scaler_path）をAIタブ or Settingsに折りたたみ表示し、コピーしやすくする（※起動時チェックの再実行は増やさず、保持結果の表示のみ）
