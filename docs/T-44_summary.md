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


T-44-2
■ 目的

MFE（伸び代）を算出できる既存ログの有無を“実ファイル観測”で確定し、無い場合は 確定済み profit_jpy（CLOSE）だけで作れる proxy に切替えて、profit_metrics.upside_potential（LOW/MID/HIGH）を 必ず返す 状態にする。

■ 実施内容（事実）

logs/ 配下の .jsonl/.json を対象に、MFE関連キー（unrealized/floating/mfe/runup/position/tick/bid/ask/max_* 等）を総当り検索し、MFE算出に必要な建玉中ログが存在しないことを確定。

MFEが作れない前提で、勝ちトレードの profit_jpy 分布（realized）を proxy にして upside_potential を段階化し、profit_metrics に 追加のみで拡張。

触ったレイヤ：services のみ（GUI/COREは不変更）

新規関数：なし（既存 compute_profit_metrics() の返却dictにキー追加のみ）

■ 変更ファイル

app/services/ops_history_service.py（compute_profit_metrics() 付近：profit_metrics の返却dictに upside_potential を追加）

既存APIのみ使用

責務境界（gui/services/core）遵守

■ 挙動の変化

変わった点：profit_metrics に upside_potential: LOW|MID|HIGH が追加され、常に返る。

変わっていない点：既存の profit_metrics のキー/型/意味（profit_factor, max_favorable_excursion など）は維持（追加のみ）。

■ 確認方法

python -X utf8 -m compileall app/services → exit_code=0

summarize_ops_history(...) の返却 profit_metrics に upside_potential が含まれることを確認（例：'upside_potential': 'LOW'）。


T-44-3 仕様固定
テーマ / Step

T-44-3（Exit as Decision）追加作業：record_trade_result(info) 入力契約の仕様固定

目的

caller不在（repo内に呼び出し元が無い）でも、将来どこから呼ばれても「推測ゼロ」で exit_reason/exit_type を渡せる契約を services 側で固定

実施内容（事実）

TradeService.record_trade_result() に docstring で許容キー/優先順位/正規化ルールを明文化

後方互換キー reason/close_reason は "TP"/"SL" のときだけ採用（推測禁止）

UiEvent.exit_* は Optional のまま維持（変更なし）

変更ファイル

app/services/trade_service.py（record_trade_result の docstring 追記）

trade_service

守った制約

挙動不変（ルール明文化のみ）、追加のみ、servicesのみ、推測禁止、責務境界遵守

挙動の変化

仕様固定（コメント/ドック）。ロジック自体は既存方針のまま

確認方法

python -X utf8 -m compileall app/services：OK

info={"reason":"TP"} で CLOSE が exit_reason="TP" / exit_type="PROFIT" になることを観測
