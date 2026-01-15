T-45-1
目的

GUI の取引スイッチで dry_run / live_run を安全に切り替えられる状態を作る

既存ロジックを壊さず、実発注の最終ゲートを1点に集約する

■ 実施内容（事実）

MT5発注直前の1点で trading_enabled を参照し、dry_run を最終確定

trading_enabled=False の場合のみ 強制 dry_run

trading_enabled=True の場合は 呼び出し側の dry_run 引数を尊重

ログに effective_dry_run を出力し、挙動を観測可能にした

GUI / 判断ロジック / 分岐構造は 一切変更なし

■ 触ったレイヤ

services のみ

gui / core：変更なし

■ 新規関数

なし（既存コードへの最小差分）

■ 変更ファイル

app/services/execution_service.py

MT5発注直前の dry_run 分岐ブロック（1箇所のみ）

■ 守った制約

最小差分

既存APIのみ使用

責務境界（gui / services / core）厳守

推測で直さず、ログ観測で挙動を確定

PowerShell 7 前提の確認

■ 挙動の変化

変わった点

GUI の取引ON/OFFが「実発注するかどうか」に実際に効くようになった

OFF時は必ず実取引に到達しない（dry_run強制）

変わっていない点（重要）

売買判断ロジック

ログ構造（add-only）

GUI の操作方法

dry_run 引数の意味（ON時は従来どおり）

■ 確認方法

python -X utf8 -m py_compile app/services/execution_service.py

ログ観測：

Select-String -Path .\logs\app.log -Pattern "\[exec\] trading_enabled=.* effective_dry_run=.*"


ON/OFF 切替で effective_dry_run が期待どおり変化することを確認

■ 結論

T-45-1 は設計どおり完了

自動売買に入るための「電源スイッチ」が、安全・説明可能な形で確定した

T-45-2


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
目的

ENTRY / SLTP / CLOSE が同一 inflight 水準で管理されているか

例外や失敗があっても inflight が残留しないか

自動売買が止まる原因を“犯人特定できるログ”で可視化する

この3点を ロジック不変・観測のみで確定させる。

1. inflight の単位を symbol に統一（設計確定）

inflight key を symbol-only（例: USDJPY-） に統一

ENTRY / SLTP / CLOSE を 同一 inflight として扱う設計を採用

これにより「同一シンボルでの競合・二重発注・決済衝突」を最も保守的に防止

👉 設計判断として A案（symbol 1本化）を確定

2. ENTRY 経路（order_send）の観測配線
対象

app/core/mt5_client.py

実施内容

order_send() の 直前で inflight mark

finally で必ず inflight clear

trade_service 呼び出しは try/except 維持（挙動不変）

loguru.logger による inflight ログを必ず出力

観測ログ
[inflight][mark] key=USDJPY-
[inflight][clear] key=USDJPY- ok=True symbol=USDJPY

3. SLTP 更新経路の観測配線
対象

app/services/mt5_service.py

safe_order_modify_sl()

実施内容

ENTRY と同じ symbol inflight を使用

mark_order_inflight() → finally clear を保証

trade_service 依存とは独立して app.log に必ずログを残す

MT5 comment に intent=SLTP / ticket を明示（28文字制限内）

観測ログ
[inflight][mark] key=USDJPY- intent=SLTP ticket=6903036
[inflight][clear] key=USDJPY- intent=SLTP ok=True symbol=USDJPY ticket=6903036

4. CLOSE（決済）経路の観測配線
対象

app/core/mt5_client.py

close_position()

実施内容

MT5 request comment に intent=CLOSE t=<ticket> を明示

CLOSE 専用で inflight ログに intent=CLOSE / ticket を付与

inflight mark → finally clear を必ず通す（例外でも）

観測ログ
[inflight][mark] key=USDJPY- intent=CLOSE ticket=6903072
[inflight][clear] key=USDJPY- intent=CLOSE ok=True symbol=USDJPY ticket=6903072

5. inflight 残留ゼロの実証
実測結果

inflight mark / clear 件数一致

inflight diff = 0

PositionGuard 内 inflight_orders は常に空

marks=7 clears=7 diff=0
n= 0
[]


👉 「詰まり続ける inflight」は存在しないことを観測で確定。

6. deny ログが出ないことの確認

[guard][entry] denied reason=inflight_orders

inflight_keys=[...]

👉 いずれも未発生
＝ inflight が自動売買を止めている可能性は排除。

T-45-4 の結論（重要）

inflight 周りは 設計・実装・観測すべて正常

ENTRY / SLTP / CLOSE の 犯人特定ログが完全に揃った

自動売買が動かない原因は inflight ではないと断定可能

👉 次に疑うべきは
「戦略がエントリー条件を出していない / スケジューラが起動していない / dry_run / ガード条件」 側。
