T-60
ゴール

自動売買に入る前段として、
MT5の接続状態・口座状態・資金状態を人間が即判断できるGUIを完成させ、
誤操作・誤認・無自覚な real 接続を防ぐ。

実装完了内容（確定）
1. 口座帯（常時表示）

GUI上部に MT5口座帯 を常時表示

DEMO：青帯

REAL：赤帯

ログイン中は 「/ ログイン中」 を付加

どのタブにいても現在の口座・接続状態が即判別可能

2. 手動ログイン / ログアウト（Controlタブ）

Controlタブ「運転」枠に ログイン / ログアウトボタン を設置

GUI起動時の自動ログインは 行わない（安全側）

既存の MT5自己診断と同一の接続経路を再利用

GUI → services → core の責務境界を厳守

3. 口座ステータス表示（Controlタブ）

ログインボタンと取引ボタンの間に以下を表示：

残高（Balance）

有効証拠金（Equity）

余剰証拠金（Free Margin）

保有ポジション数

表示仕様

ログアウト中：すべて --

ログイン中：実値表示

残高系フォントサイズを2倍（20px）に拡大し、即視認可能

4. ログイン中のみ10秒ごとの自動更新（今回追加）

MainWindow に QTimer（10秒） を追加

ログイン中のみタイマーを start()

ログアウト時は即 stop()

未接続時に MT5 へ問い合わせない安全設計

更新内容

残高

有効証拠金

余剰証拠金

ポジション数

※すべて read-only スナップショット（取引・状態変更なし）

技術的に守られていること（重要）

GUIから MetaTrader5 を 直接 import / 呼び出ししていない

MT5接続・状態取得は services 層に集約

起動時に real 口座へ勝手に接続しない

例外発生時も GUI が落ちない（常に -- へフォールバック）

python -X utf8 -m py_compile 全通過

現在の到達点

Controlタブは 操作盤＋計器盤として完成

人間が「見てから押す」ための情報がすべて揃った

T-60（自動売買可能な状態への下地）達成


T-62：Controlタブ「取引ON」→ 自動売買ループ開始の“配線”を観測で確定

■ 目的

「押しても始まらない」をゼロにするため、GUI→起動点→停止点の呼び出し経路を観測で一意に固定した。

■ 実施内容（事実）

grepで呼び出し経路を観測確定（推測で増築なし）

起動点＝TradeLoopRunner.start()、停止点＝TradeLoopRunner.stop() を唯一の点として明示（コメント追加）

[trade_loop] started/stopped のログが main.py の各1箇所のみで出ることを確認

多重起動ガードは 既存 _is_running を利用（新規フラグ追加なし）

■ 変更ファイル

app/gui/main.py（TradeLoopRunner：起動点/停止点のコメント明示、ログ一点化の前提を明文化）

app/gui/control_tab.py（取引ONが _main_window._trade_loop.start()/stop() にのみ配線されている旨をコメント明示）

docs/trade_loop_start_observation.md（観測結果ドキュメント化：配線・起動点・停止点・ログ・多重起動ガード）

■ 守った制約

最小差分

既存APIのみ使用（新規関数・新規ループ・新規スレッドなし）

責務境界（gui/services/core）遵守（GUIは services 直呼びしない）

推測で直さず、観測で確定してから明示

■ 挙動の変化

変わった点：

起動点・停止点がコード上でもドキュメント上でも一意に確定し、運用者が追跡可能になった

変わっていない点（重要）：

dry_run/real判定やガードロジックは未変更（これはT-63で扱う）

取引ロジックやservices/coreの責務は未変更

■ 確認方法

python -X utf8 -m py_compile app/gui/main.py app/gui/control_tab.py が通ること

実行してログ確認：python -m app.gui.main

「取引ON」1回で [trade_loop] started ... が1回だけ

連打で多重起動せず（必要なら start denied reason=already_running）

停止で [trade_loop] stopped reason=... が1回だけ



##########################
T-65 完了サマリ（安全な起動・ログイン連動・取引トグル整合）
ゴール

未ログイン状態で取引ONできない（誤操作防止）

ログアウト時に取引ループを必ず停止し、UI/内部状態をOFFへ復元

env未設定などで mt5_client.initialize() が例外を投げても、GUIが壊れず “BLOCK” として扱える

1) T-65.1：initialize() 例外を “BLOCK” 回収（services側）

mt5_client.initialize() が _get_env 経由で 環境変数未設定なら RuntimeError を投げるのを観測。

run_start_diagnosis() で initialize() を try/except し、例外を落とさずに 開始をBLOCK 扱いへ統一。

例外時は reason を env_missing に固定して、監査可能なログだけ残す（スタックトレース連打なし）。

結果：

ログイン前に取引ONしても、クラッシュ/スタックトレースなしで BLOCKED に収束。 

main

2) T-65：未ログイン時の取引ボタン無効化（GUI側）
ControlTab：取引系コントロールを一括でON/OFFできるAPIを追加

ControlTab.set_trading_controls_enabled(enabled: bool, reason_text: str="") を追加。

enabled=False のとき：

btn_toggle と btn_test_entry を setEnabled(False)（押せない）

btn_toggle を OFF表示へ強制（blockSignalsで再入防止）

trading_enabled=False を trade_state と runtime_flags.json に確定

表示文言：「取引：停止中（ログインしてください）」

enabled=True のとき：

取引ボタン群を有効化し、trade_state に合わせて表示復元

control_tab

3) MainWindow：起動時＋ログイン/ログアウトに連動して取引ボタンを制御

起動時：

mt5_selftest.is_mt5_connected() を見て 未ログインなら即無効化

ログイン成功時：

set_trading_controls_enabled(True)

ログアウト時：

先に trade_loop を stop(reason="mt5_logout")

disconnect_mt5()

set_trading_controls_enabled(False) で UIと内部状態をOFFへ固定

main

4) 期待どおりになった観測結果（今回のログから）

ログアウト後に取引ボタンが押せない状態へ戻り、誤って開始できない

ログイン後のみ取引ONでき、取引ループが回る

ログアウト時に停止ログが出てループが止まる（UIと整合）

5) 安全性が上がったポイント（本質）

「取引ONできる条件」を “ログイン状態” に結びつけて UI で強制

さらに services の診断で env未設定などの例外を “BLOCK” として閉じる
→ 二重の安全柵（UI柵 + 診断柵）になったのがデカいです。宇宙は残酷なので柵は多いほどいい。