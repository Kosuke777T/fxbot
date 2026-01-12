T-44-1
目的

勝率ではなく 期待値ベースの利益評価軸（Profit Metrics） を
services から取得可能にする土台を作る。

観測で確定した事実

PnL の一次ソース

logs/ui_events.jsonl

kind="CLOSE" 行の profit_jpy が 確定損益

書き込み元

app/services/event_store.py

app/services/trade_service.py（決済時）

実装内容（最小差分）

変更ファイル

app/services/ops_history_service.py のみ

追加関数

compute_profit_metrics(trades: list[dict]) -> dict

算出指標

expectancy

avg_win

avg_loss

profit_factor

max_favorable_excursion（存在すれば）

既存フローへの接続

OpsHistoryService.summarize_ops_history() の戻り dict に
result.setdefault("profit_metrics", profit_metrics) で add-only 接続

売買ロジック／CM／GUI は 一切変更なし

観測結果（実測）
profit_metrics {
  'expectancy': -500.0,
  'avg_win': 0.0,
  'avg_loss': 500.0,
  'profit_factor': 0.0,
  'max_favorable_excursion': None
}

動作確認

python -X utf8 -m compileall app/services ✅ 成功

既存動作・既存テスト破壊なし

状態まとめ

✅ services から期待値ベースの利益評価軸が取得可能

✅ 「なぜ儲かっている／いないか」を勝率なしで説明可能

✅ GUI未コミット差分と非干渉

✅ 次フェーズに安全に進める状態
