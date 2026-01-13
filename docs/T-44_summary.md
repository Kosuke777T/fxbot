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


T-44-4（Sizing）
■ 目的

ENTRY 時に付与した size_decision が
decisions / ops_history の両方で必ず観測できる状態を確定し、
その観測手順を tools に固定する。

■ 実施内容（事実）

logs/ 配下を総当り観測し、実体パスを確定

decisions: logs/decisions_YYYY-MM-DD.jsonl

ops_history: logs/ops/ops_result.jsonl

PS7 前提の 観測専用スクリプトを tools に固定

tools/reobserve_decisions_tail.ps1（read-only）

スクリプト実行により以下を実ログで確認

decisions の ENTRY 行に decision_detail.size_decision

ops_history の meta.size_decision

■ 変更ファイル

新規

tools/reobserve_decisions_tail.ps1

※ app/services/execution_service.py の変更は 前段（T-44-4実装）由来であり、
本スレッドの「観測手順固定」ではコードロジック変更なし。

■ 守った制約

推測で直さず、ログ観測で実体を確定

services / tools の責務分離を維持

既存 API・ロジックは不変更

PowerShell 7 前提、Here-String で Python docstring 非エスケープ

■ 挙動の変化

変わった点

decisions / ops_history の正しい参照先が明文化・固定化された

size_decision の有無を即座に再観測できる

変わっていない点（重要）

取引ロジック・サイズ計算ロジック自体は不変

ログ出力仕様は変更なし

■ 確認方法

pwsh -File .\tools\reobserve_decisions_tail.ps1 -Tail 200

OK 条件

ENTRY 行に decision_detail.size_decision が存在

ops_history 行に meta.size_decision が存在

T-45-3｜自動サイズ反映
■ 目的

最終ロット確定地点を1点に固定し、そこでだけ base_lot × multiplier を適用して、実運用で“なぜそのロットか”を説明可能にする。

■ 実施内容（事実）

観測で確定：ExecutionService には発注（=最終ロット確定）が存在せず、最終ロット確定＋order_send は TradeService.open_position() にある

最小差分：TradeService.open_position() の「lot_val確定直後〜order_send直前」の 1か所でのみ乗算

後方互換：features["size_decision"]["multiplier"] 優先 → なければ features["size_multiplier"]

no-op保証：multiplier==1.0 は従来と同一（ログも出さない）

■ 変更ファイル

app/services/trade_service.py（TradeService.open_position() の最終ロット確定地点 1点）

■ 守った制約

最小差分

既存API優先（新規 public API 追加なし）

責務境界（gui/services/core）遵守

推測で直さず、観測で接続点を確定

■ 挙動の変化

変わった点：size_decision.multiplier により 最終ロットが比例変化する

変わっていない点：multiplier==1.0 時は 完全no-op（従来ロットと一致）

■ 確認方法

python -X utf8 -m py_compile app/services/trade_service.py → OK

python -X utf8 -m compileall app/services → OK

ログ観測（あなたが実施済み）：Select-String ... "\[lot\] apply size_decision" で適用ログを拾う
