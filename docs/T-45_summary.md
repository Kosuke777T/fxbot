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


T-45-4
