学習データの buy / sell 比率の観測

■ 目的

学習に使うラベル（buy/sell/skip）の比率を推測なしで観測・確定する

■ 実施内容（事実）

scripts/weekly_retrain.py に [DATA BALANCE][RAW]（build_labels直後）と [DATA BALANCE][TRAIN]（align_features_and_labels直後）を追加

[DATA] ログ行の NameError を修正（buy_train/sell_train に統一）

触ったレイヤ：scripts（※gui/services/core 変更なし）

新規関数：なし（ログブロック追加のみ）

■ 変更ファイル

scripts/weekly_retrain.py（run_weekly_retrain 内：観測ログ追加＋NameError修正）

（既存）scripts/walkforward_train.py には既に観測ログあり

■ 守った制約

最小差分

既存ロジック不変（観測のみ）

責務境界（gui/services/core）不変更

動作確認を実施（py_compile / dry-run 実行ログ）

■ 挙動の変化

変わった点：学習時に RAW/TRAIN のラベル比率がログ出力される／NameErrorが消えた

変わっていない点：ラベル生成・学習処理の中身は不変（ログのみ追加）

■ 確認方法

python -X utf8 -m py_compile scripts/weekly_retrain.py

python -X utf8 -m scripts.weekly_retrain --dry-run

OK条件：[DATA BALANCE][RAW] と [DATA BALANCE][TRAIN] が出力され、dry-run 終了まで到達

■ 観測結果（確定）

RAW: buy 47.2% / sell 45.9% / skip 6.9%

TRAIN: buy 50.7% / sell 49.3%

1. ラベル設計の確定

min_pips = 0.6 を config.yaml に固定

label_horizon = 10 を基準として明示

CLI 上書きも含め、設定 → 実行 → ログ の一貫性を確立

2. Walk-Forward + threshold 最適化の完成度向上
optimize_threshold() 改修内容

fold別・全体で以下をすべて可視化：

best_thr

total (equity)

win_rate

n_trades

top3候補（thr / total / win / n）

**fold=1 のズレの正体が「n_trades が極端に少ないノイズ」**であることを特定

3. ノイズ対策の実装（重要）

min_trades パラメータを追加（デフォルト 500）

n_trades < min_trades の threshold を eligible=False として除外

best / top3 は eligible のみから選択（fallbackあり）

n_trades==0 は total=NaN に変更（巨大マイナス値廃止）

4. ログ・追跡性の強化（A/B/C 案すべて適用）
A案：run_id 追跡

run_id = int(time.time()) を生成

fold別 / 全体 THR ログに [run_id=...] を付与

B案：ログファイル名改善

weekly_retrain_YYYYMMDD_HHMMSS.log

同日複数実行でも完全分離

C案：grid詳細CSV出力

logs/retrain/thr_grid_<run_id>.csv

fold × thr × total × win_rate × n_trades × eligible を保存

PowerShell で即集計・検証可能な状態を確認済み

5. 現時点での運用知見（重要な結論）

全体 best_thr = 0.45 は一貫して最強

fold別でズレるケースは、ほぼ例外なく n_trades が原因

「勝率が高い threshold」≠「運用に耐える threshold」

しきい値最適化は ノイズ除去込みで初めて意味を持つ

6. 現在の到達点

学習 → WFO → しきい値決定 → 根拠保存 → ノイズ除去
すべて 再現可能・検証可能・説明可能 な状態

これはもう「実験コード」ではなく 運用前検証システム。
