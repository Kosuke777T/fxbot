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

python -X utf8 scripts/weekly_retrain.py --dry-run

OK条件：[DATA BALANCE][RAW] と [DATA BALANCE][TRAIN] が出力され、dry-run 終了まで到達

■ 観測結果（確定）

RAW: buy 47.2% / sell 45.9% / skip 6.9%

TRAIN: buy 50.7% / sell 49.3%