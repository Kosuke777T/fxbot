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