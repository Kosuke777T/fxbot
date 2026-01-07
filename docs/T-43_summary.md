T-43-1（窓情報 + min_stats の取得）クリアです。
TypeError: naive/aware も潰れて、total=None → 0 に正規化できました。

condition_mining_data

ここまでの完了点（T-43-1 Done）

get_decisions_recent_past_summary('USDJPY-') が例外なく動く

recent/past に min_stats.total が必ず入り、0埋めされる

facade get_decisions_recent_past_window_info() からも total が取れる

T-43-2で確定した成果（記録用）

get_decisions_recent_past_window_info('USDJPY-') が

recent/past それぞれ n / range(start,end) / min_stats を必ず返す

n=0 のときでも落ちない（縮退OK）

get_condition_candidates('USDJPY-', top_n=10) が

既存API（top_n）互換を維持したまま動作

内部で condition_mining_candidates.get_condition_candidates_core(top_k=top_n, max_conds=80, min_support=20) にマップ

decisions が0件のときは warnings=['no_decisions_in_recent_and_past'] で安全に 0件返却

PowerShell Here-String 絡みの事故（\\""" docstring崩壊）を回避する方針が固まった

PythonのdocstringをPS側でエスケープしない

事故回避の実務解として Facade/サービスは # コメント優先 が安全

T-43-3 Step1で達成したこと

get_decisions_recent_past_window_info('USDJPY-') が 常に以下を返すように固定化

warnings（decisions=0なら ['no_decisions_in_recent_and_past']）

ops_cards（decisions=0でも 1枚以上）

ops_cards[0] に **Ops向けの「0件理由推定」**が載る

今回の実データ上の推定：decisions_*.jsonl が存在しない（稼働停止/出力設定/権限/パス）

T-43-3 Step2data側の evidence + warnings/ops_cards 復活）は完成してます。

warnings/ops_cards：縮退シグナルが安定（None禁止、カード整形OK）

evidence：metrics.json 優先で win_rate/avg_pnl が安定して取れてる

evidence_src：具体パスまで出てる（運用で追跡できる）

T-43-3 Step2-2 完了
warnings が固定形（list）

ops_cards_first[0] が kind/title/summary/bullets/caveats/evidence を必ず持つ（カード整形統一）

evidence がカード根拠を同梱（空じゃない）

decisions=0 の縮退が 断定せず、観測可能な log_inspection を同梱（安定化）

files=0 / latest_mtime=null で「ログが無い」を事実として提示できてる（推定と分離できてる）

T-43-3 Step2-3
決め事 1：GUI/ops の情報取得は snapshot に一本化

GUI は get_condition_mining_ops_snapshot(symbol='USDJPY-') だけを呼ぶ

GUI 側は 固定キーだけを見る（warnings / ops_cards_first / evidence / evidence_kind / evidence_src / symbol）

これにより「旧Facade経由の別ロジック」が混入しても、監査で即検出できる

決め事 2：旧Facade（二重構造）は “互換専用” に降格

get_decisions_recent_past_* は GUI では使わない

残す理由は 外部/古いコード互換のみ

今後の機能追加や仕様変更は snapshot を正として進める（旧Facadeは追随しない方針でOK）

決め事 3：snapshot の「固定形」が契約（破ったら壊れる）

missing=[] がテストで担保できるので、将来変更するなら 必ず固定キー互換を維持する

0件でも落ちない縮退表示（warnings / ops_cards_first）を正規ルートにした

決め事 4：GUI import を壊す依存（ai_service）を止血

core.ai.loader に meta loader が無い状況でも GUI import が通るように縮退

get_active_model_meta() は dict を返す（keys: ['file','n_features','head','feature_order','note'] が確認できた）

“GUIがまず落ちない” を優先して、meta は後で正式ルートに寄せられる構造にした

T-43-3 Step2-4
GUI は get_condition_mining_ops_snapshot() 一択（表示側は snapshot 固定形に依存してよい）

decisions が 0 件でも 例外で落とさず、warnings と ops_cards_first で「縮退理由」を返す

“証拠” は evidence_kind / evidence_src で辿れる（今回は ops_card / logs/decisions_*.jsonl）

PowerShell + python -c は quoting 地獄なので、最終的に安定したのは

Set-Location D:\fxbot を固定

PYTHONPATH=D:\fxbot を明示

python -c @" ... "@ のワンショットで完結
という運用ルール（このパターンが再利用可能）

T-43-3 Step2-5 達成内容（記録用まとめ）
✅ 原因

decisions の生成はできていたが、読み取り側の参照ディレクトリが旧仕様 logs/decisions/ のままで、v5.2 の logs/decisions_YYYY-MM-DD.jsonl を読めず 0件 になっていた。

✅ 復旧

app/services/execution_stub.py：logs/decisions_YYYY-MM-DD.jsonl を生成できるようにした（v5.2）

app/services/decision_log.py：参照先を logs/ 直下に統一（v5.2）

get_decisions_window_summary() で n>0 を確認

condition_mining_facade.get_condition_mining_ops_snapshot() を修復し、縮退時でも嘘を言わない bullets に改善

seed 1 行で recent に decision が入る状態を作り、smoke で warnings=[] を確認（通常パス OK）

✅ 確認結果（あなたのログ）

warnings=[]

ops_cards_first_n=0

snapshot JSON 出力 OK

完了時点の確定事項（再発防止の記録）

v5.2 の decisions 正規保存先：logs/decisions_YYYY-MM-DD.jsonl（logs直下）

読み取り側の参照先：decision_log._get_decision_log_dir() は logs/ を返す

condition_mining の 0 件問題の主因：読み取りが旧 logs/decisions/ を見ていた（参照先不一致）

縮退カードの改善：get_condition_mining_ops_snapshot() は「無い」と断定せず、検出件数/最新情報に基づいて表示する（嘘をつかない）

通常パスの成立条件：recent 窓に 1 件でも decision があれば warnings=[] になる（今回 recent_n=1 で確認済み）


T-43-3 Step2-6
この作業で「何が正常になったか」（記録用）

✅ decision ログの保存先は logs/decisions_YYYY-MM-DD.jsonl に単一化（実装・説明とも一致）

✅ execution_service.py に残っていた 旧パスのコメント／未使用 LOG_DIR 作成を削除

✅ “誰かが将来、コメントを信じて旧ディレクトリを復活させる” 事故ルートを遮断

decision保存先：logs/decisions_YYYY-MM-DD.jsonl に完全単一化

旧パス残存：0（NG_files=0）

実書き確認：OK（USDJPY- 反映）

compileall：OK

condition_mining_smoke：正常（縮退警告のみ）

T-43-3 Step2-7
事象

condition_mining_smoke が warnings=['no_decisions_in_recent_and_past'] で縮退

logs/decisions_YYYY-MM-DD.jsonl は実書きOKだが、行に timestamp が無い（ts_jst / ts_utc 形式）

原因（確定）

app/services/condition_mining_data.py が 時刻キーを timestamp 前提で参照しており、
ts_jst/ts_utc を持つ decision 行を “窓判定” で落としていた

対応（最小差分・責務境界順守）

condition_mining_data.py の時刻解釈を ts_utc → ts_jst → timestamp のフォールバックに修正

新規関数追加なし、既存 _parse_iso_dt を利用

確認結果（完了条件）

tools/condition_mining_smoke.ps1 -Symbol "USDJPY-" が
warnings=[] / ops_cards_first_n=0 を出力（縮退解除）

症状：no_decisions_in_recent_and_past 縮退

原因：decision 行が timestamp を持たず ts_jst/ts_utc 形式、ConditionMining が timestamp 前提で窓判定して 0 件扱い

対応：ts_utc → ts_jst → timestamp のフォールバックに修正（新規関数なし、既存 _parse_iso_dt 使用）

確認：tools/condition_mining_smoke.ps1 -Symbol "USDJPY-" で warnings=[]

T-43-3 Step2-8
責務境界：tools→services(facade/data) のみで完結（gui/coreに侵入なし）

既存API優先：smoke は facade を呼ぶまま、facade を data に委譲するだけ

新規関数最小：不足していた facade 関数を補完＋data側の helper

品質チェック：decisions が summary に無い場合は誤検知しない

evidence 改善：recent/past range/n/min_stats と分布枠が snapshot に載る

tools/condition_mining_smoke.ps1 は app.services.condition_mining_facade.get_condition_mining_ops_snapshot を呼ぶ

facade の get_condition_mining_ops_snapshot を data 実装へ委譲して、smoke の evidence_kind を decisions_summary に統一

condition_mining_data.get_condition_mining_ops_snapshot を新設・拡張し、

decisions が summary に含まれないケースでは 誤検知 warnings を出さない

evidence に recent/past の {n, range, min_stats} と keys/dist の枠を提供

condition_mining_facade.py に混入していた ゴミ文字列 \n 行を除去してコンパイル安定化

T-43-3 Step2-9
get_condition_mining_ops_snapshot が summary.warnings / summary.ops_cards を引き継ぐ

Step2-9 の enrich で recent/past が 0 の場合、window=None の get_decisions_window_summary(include_decisions=True) を使って

evidence.all.ts_min/ts_max

evidence.all_keys_top

evidence.all_symbol_dist
を実データで埋める（重くならないよう sample は先頭3件）

timestamp 抽出は ts_jst 等の揺れにも耐えるよう候補キーを追加


T-43-3 Step2-10
達成したこと

services

recent/past 0件時に all fallback を事実として返却

evidence.window に mode / range / fallback_reason を刻印

window_range_mismatch を warnings として明示

GUI

[ALL] / [WARN] による 嘘をつかない状態表示

表示ロジックのみ追加（判断ロジックは services 側）

window 拡張

GUI → Facade → data に **kwargs 素通し

6h window で [ALL][WARN] が自然に消えることを実証

守れた制約

既存API優先 / 新規関数なし

責務境界（gui / services / core）厳守

PowerShell 7 + Here-String + python -c

symbol = USDJPY-

smoke test による回帰確認

🧠 設計的に重要な決め事（将来の自分を助ける）

GUIは事実を表示するだけ

fallback / mismatch は「状態」であって「エラー」ではない

window は 設定で性格が変わるパラメータ（ロジックではない）

Condition Mining は「静かに嘘をつかない UI」が最優先

これは後で必ず効いてきます。


T-43-3 Step2-11
1. 時間窓が「ハードコード」から「設定」になった

以前：

recent_minutes=30 などが

SchedulerTab

facade

data
に 散在して直書き

結果：

どこを見ているのか分かりにくい

GUI表示と実際の探索条件がズレる危険あり

Step2-11後：

時間窓は profile別設定として一元管理

mt5_account_store.get_condition_mining_window(profile)


demo と real で別の探索窓を持てる

👉 「探索条件は設定に属する」という設計原則に戻した

2. caller override が可能（設定より引数が優先）

設計上かなり重要なポイント。

通常：

get_condition_mining_ops_snapshot(symbol)


→ profile設定の window が自動適用される

明示指定した場合：

get_condition_mining_ops_snapshot(
    symbol,
    recent_minutes=1,
    past_minutes=2,
    past_offset_minutes=3,
)


→ 設定を上書き（override）

👉

GUI

スクリプト

デバッグ
すべてで「一時的に窓を変えて試す」ことができる

3. evidence.window が「真実」を語るようになった

ここが Step2-11 の核心。

以前：

evidence.window は

30 / 30 / 1440 が固定で表示されることがあった

実際に使われた window と 乖離する可能性

Step2-11後：

実際に解決された minutes を使って

out["evidence"]["window"] = {
    "recent_minutes": 7,
    "past_minutes": 9,
    "past_offset_minutes": 111,
    "recent_range": {...},
    "past_range": {...},
}


後続処理（勝率抽出など）でも 消されずに保持

👉
GUIが「推測」ではなく「事実」を表示するようになった

4. services / facade / gui の責務が整理された

暗黙にやっていたことを、はっきり分離。

mt5_account_store

profile別 window の保存・取得

condition_mining_facade

設定を解決して kwargs に注入

ops向けの「嘘をつかない」スナップショットを返す

condition_mining_data

実データ処理

window metadata を evidence に正しく反映

SchedulerTab

値を決めない

表示と操作だけ

👉
「GUIがロジックを持たない」状態に一段近づいた

成果を一行でまとめると

Condition Mining の時間窓が、
ハードコード → 設定 → profile別 → GUI反映 → override可能
という “運用できる設計” に進化した。

T-43-3 Step2-12
UI構造

SchedulerTab を Overview / Condition Mining / Logs のサブタブ構成に分離

**Logs タブを“運用の主戦場”**として再定義

左：Scheduled / Always（タブ）

上：ジョブ操作ツールバー（更新 / 追加 / 編集 / 削除）

右：実行ログ（detail_text）

「実行 → その場でログ確認」が 画面遷移ゼロで完結

Condition Mining

profile（demo / real）切替

recent / past / past_offset を GUI から編集

保存 → set_condition_mining_window_settings

即 get_condition_mining_ops_snapshot 再取得・反映（再起動不要）

evidence.window が UI と完全同期

設計面

新規ロジック最小、既存ハンドラ・サービスを再配置のみ

責務境界（gui / services / core）維持

Scheduler の **「概要を見る場所」と「触る場所」**が明確に分離

T-43-3 Step2-13
1秒理解：next_action（色付きバッジ）＋warnings（OK/警告）が常時視界の中心。

実質的な折りたたみ：

Overview 上段（Ops / Scheduler / AI）は本当に畳まれる（空白なし）。

Ops Overview は要点のみ常時表示、詳細は行ごと非表示で空白が消える。

安全性：ロジック追加ゼロ。既存 ops_snapshot / _refresh_ops_overview() を最大活用。

T-43-3 Step2-14
テーマ：カード化 / アイコン化 / 次の一手導線（表示のみ）

1. 目的と前提

Ops Overview を「情報の羅列」から 意思決定を助けるUI に変える

ロジックは一切触らない（表示のみ）

既存の ops_snapshot を最大活用する

Condition Mining は Ops Overview から切り離す

2. Ops Overview の構造変更（重要）
Before

QFormLayout による縦並び

チェックON/OFFで 文字が薄くなるだけ（視認性が悪い）

Status / Model / Condition Mining が混在

After

カードUI（QGroupBox + VBox）に再設計

Ops Overview は 2カード構成に固定

Status

Model Stability

Condition Mining は 専用タブに完全分離

3. カード化の設計ルール

_make_ops_card() ヘルパーを追加

太字タイトル

左側に アイコン付き見出し

中身は既存ラベルをそのまま流用

データ構造・更新ロジックは 一切変更なし

4. Ops Overview の折りたたみ挙動の修正
問題

checkable QGroupBox のデフォルト挙動により

OFF時に子Widgetが disable

結果として「文字が薄くなるだけ」

対応

toggled 時に 常に enabled を維持

表示制御は以下に限定

OFF：Statusカードのみ表示

ON：Status + Model Stability 表示

高さ制御で余白を抑制（Fixed / Preferred 切替）

👉 「視線誘導」だけを行い、意味論は変えていない
5. 「次の一手」導線（表示のみ）

Statusカード下に以下を配置

「ログを開く」

「設定へ」

disabled 状態で表示のみ

ToolTip で将来の接続先を明示
（Logsタブ / Condition Mining / 設定）

6. Condition Mining 分離の判断理由

Ops Overview は “今どうするか”を見る場所

Condition Mining は “調べる”場所

同一カード内にあると認知負荷が高い

分離により：

Ops Overview：即断用

Condition Mining：分析用
という役割が明確になった

7. 技術的に重要な注意点（再発防止）

正規表現パッチは 開始行・終了行のアンカー厳守

QGroupBox の checkable は 表示制御と意味がズレやすい

「折りたたみ = disable」ではなく
visible / height / sizePolicy で制御する

8. 到達点（結論）

Ops Overview は カード型ダッシュボードになった

状態 → 判断 → 次の行動、が一画面で読める

Condition Mining を切り離したことで
Ops の役割が「運用判断」に特化した

T-43-3 Step2-15
next_action は dict前提で扱う（kind を表示用に正規化）

Statusカードの判断は 表示専用 helper に閉じ込める
→ 今後アクション種別が増えても GUI 側だけで対応可能

「次の一手」は 今は動かさない
→ “押せそうだが押せない” ことで、次の導線を暗示するUI

T-43-3 Step2-16
1. 「次の一手」リンクの視認性を改善（hover 表示）

対象：Scheduler / Ops Overview に表示される「次の一手」リンク

内容：

通常時：テーマの palette(link) 色

hover 時：テーマ追従の palette(highlight) 色に変化

ポイント：

CSS 的な見た目変更のみ

判断ロジック・条件分岐は一切追加していない

ダーク / ライトテーマ両対応（テーマ追従）

2. next_action.reason を tooltip で表示

対象：「次の一手」リンク

内容：

hover 時に next_action.reason を 1行 tooltip として表示

仕様上の扱い：

表示のみ（解釈・加工・再判定なし）

値が空なら空のまま（フォールバック生成なし）

データ元：

既存の ops_snapshot をそのまま利用

新しい取得処理・計算ロジックは追加していない

3. 責務境界の厳守

GUI：

表示と hover/tooltip のみを担当

ログ・ファイル・計算ロジックには一切触れていない

services / core：

変更なし

結果：

GUI → facade → services → logs という既存構造を完全維持

4. 技術的トラブルとその収束

tooltip 追加時に IndentationError が一度発生

原因：setToolTip 行のインデント崩れ

対応：setText と完全に同一インデントに正規化

最終的に：

py_compile app/gui/scheduler_tab.py ✅

GUI表示も正常動作

5. Step2-16 の成果物として確定したもの

「次の一手」は
クリックできる情報 から
“理由が即座に読めるナビゲーション” に進化

ユーザーは：

クリック前に「なぜその一手なのか」を把握できる

HOLD / BLOCKED の文脈を自然に理解できる

6. 次ステップ（Step2-17）につながる重要な前提

next_action は UI上の概念として定義完了

reason は「判断の背景テキスト」として扱う方針が確定

今後は：

同じ意味論（HOLD / BLOCKED）を

資産曲線の帯表示などの可視化へ拡張できる状態

T-43-3 Step2-17
🎯 目的（Step2-17 前半）

バックテスト資産曲線に HOLD / BLOCKED を帯表示するための
services 側の返却形と安全な前提条件を確立する

✅ 達成したこと

帯表示の最小データ構造を確定

bands = [{start, end, kind(HOLD|BLOCKED), reason}]

GUI は描画のみ、判断ロジックを持たない設計を維持

KPIService に facade API を追加

load_equity_curve_with_action_bands()

equity / bands / source / counts / warnings を返す統一形

decisions.jsonl の安全な取り扱いを確立

スキーマ揺れ吸収（ts_jst → timestamp, filter_reasons → reason）

タイムゾーン不一致の解消

merge_asof での結合条件を検証

重大な設計判断を確定

❌ バックテスト期間外（未来）の decisions は 絶対に使わない

期間内 decisions が無い場合：

decisions_jsonl = None

warnings = ['decisions_jsonl_not_found']

bands = []

👉 嘘の帯を描かないことを最優先

現状の正しい挙動を確認

bands_n = 0

warnings が明示される

services / gui / core の責務境界を完全に遵守

🧠 重要な理解ポイント

今 bands が出ないのは バグではなく仕様どおり

原因は
👉「バックテスト run フォルダに、その run 専用の actions / decisions 時系列が存在しない」ため

グローバル logs/decisions_*.jsonl を使う設計は バックテスト帯表示には不適切

T-43-3 Step2-18
目的

core/backtest が唯一の正として timeline を生成

KPIService はそれを最優先で読む

GUI は背景帯を描画するだけ（ロジックを持たない）

実装で達成したこと

core/backtest 側

next_action_timeline.csv を run フォルダに出力

フォーマット：time, kind(HOLD/BLOCKED), reason

timeline は「変化点のみ」を記録

出力責務は _generate_outputs() に集約

_validate_outputs() から誤挿入コードを完全除去

timeline_rows は self._timeline_rows として安全に保持

KPIService 側

next_action_timeline.csv を 最優先で読み込む実装を確立

timeline が存在する場合：

decisions.jsonl を参照しない

decisions_jsonl_not_found 警告を出さない

timeline → GUI 用 bands（start/end/kind/reason） に変換

_bands_from_timeline() ヘルパーを追加

最終帯は equity の最終時刻まで自動で延長

counts（HOLD/BLOCKED/total）を bands から再計算

rows 未定義による UnboundLocalError を解消

動作確認結果

next_action_timeline.csv の生成を確認

KPIService の出力：

bands_n >= 1

warnings = []

bands = [{start, end, kind, reason}]

Step2-18 の設計意図どおりに動作していることを確認

T-43-3 Step2-19
目的

GUIを「描画専用」に徹し、判断ロジック（decisions）依存を完全排除

KPIService が返す payload['bands'] のみで背景帯を描画

完了内容

GUI 全体から decisions / decisions.jsonl 参照を完全削除（最終スキャン 0 件）

backtest_tab.py

plot_equity_with_markers_to_figure(..., bands=None) に対応

bands から 背景帯（axvspan）を描画

kind 色分け：HOLD=青、BLOCKED=赤（alpha 付き）

tooltip 実装：reason がある帯のみ表示（空は非表示）

tooltip のヒット判定を安定化

span に _band_x0/_band_x1（date2num）を保存

get_xy() 依存を排除

x軸引き伸ばし対策

equity 描画後の xlim を保存 → bands 描画後に復元

_load_plot は bands を引数で受け取るのみに変更

GUI から KPIService の直接呼び出しを削除（責務境界を遵守）

スモークテスト実施

equity 範囲内 bands で帯表示・色分け・tooltip を確認

x軸が潰れないことを確認

状態

Step2-19 の要件（描画専用 / bands のみ / 色分け / tooltip / 軸安定化）は すべて達成

T-43-3 Step2-20
1. Backtest実行フローの確立

BacktestTab の Backtest実行ボタンを QProcess 経由で
tools/backtest_run.py に正しく接続

必須引数 --csv（ローソク足CSV）を GUI から確実に渡すよう修正

バックテストは 正常完走（code=0） し、結果ファイルが生成されることを確認

2. 結果表示と読み込みの整理

「最新結果ロード」「結果フォルダ選択」により
既存の equity_curve.csv / metrics.json を正しく描画

equity 曲線・指標テーブル・ログ表示が連動して動作

3. 背景帯（bands）の意味の明確化

水色の帯 = HOLD（新規エントリー判断なし）

HOLD でも 既存ポジションの評価損益により資産は変動する

「取引しない = ノーポジ」ではないことを整理

4. 指定期間バックテストの考え方を整理

現在のバックテストは 連続運用型（carryあり）

指定期間内だけで完結させる セッション型（flat/close） という別モードが存在し得る

評価目的によって両者を使い分ける必要があることを合意

5. 今後の設計方針（次スレッド向け）

判断状態（HOLD/BLOCKED）と ポジション状態（LONG/SHORT/FLAT）を分けて可視化

Backtestモードの明示：

比較モード（flat + close）

運用モード（carry + keep）

UIにモード切替 or 表示ラベルを追加すると混乱が消える

【Project: python主体プログラム】

了解です。
ここで スレッド完了として要点をまとめます。

スレッド完了時：要点サマリ
できたこと（確定）

min_stats の不整合を完全解消

condition_mining_data ↔ condition_mining_facade で
total / filter_pass_count / entry_count が一致

ENTRY を含む decisions を生成・集計できる状態に到達

action 正規化（ENTRY / BLOCKED / HOLD）は data 層で安定

get_condition_candidates の TypeError を解消

profile 未対応問題を facade 側で吸収

recent 側の条件探索パイプラインは正常動作

recent.n > 0

recent.min_stats が正しく更新される

candidates_n=0 の主因が「past.n=0」であることを特定

データ不足ではなく 集計ロジックで past が弾かれている

実施内容と結果（事実ベース）

window/minutes 系の不整合を解消

override / profile / default の優先順位を
caller > store(profile) > default に統一

past_offset_minutes=0 が 1440 に化ける falsy バグを修正

recent / past の query_range が summary / enrich / ops_snapshot で一致

decisions ログ探索の不具合を修正

_iter_decision_paths() が logs/decisions/*.jsonl しか見ておらず 0 件になる問題を解消

最新 backtest の logs/backtest/**/decisions.jsonl を 優先探索するよう修正

結果：USDJPY- の past 件数が 550 → 3368 に増加

min_stats / rows の整合性修正

min_stats の二重加算を解消

include_decisions=False 経路で min_stats が 0 に戻る問題を修正

n == min_stats.total が成立

past-only fallback を明示的に実装

recent=0 & past>0 の場合に past-only で候補生成

warning: recent_empty_use_past_only を付与

探索AIが縮退せず動作することを確認

候補母集団の拡張（核心）

既存生成器が少なすぎて 候補が12件で頭打ちだった問題を解消

追加した最小ジェネレータ：

hour 単体条件（hour:h09 など）

prob_margin の分位点閾値（quantile）

結果：

candidates 12 → 54

top_k=200, max_conds=80 で安定生成

重複候補の排除

condition.id ベースで 順序維持の dedupe

ID 衝突（例: pm:ge_0.386 重複）を解消

candidates_len=54（重複なし）

デバッグ可視化の強化（opt-in）

CM_CANDIDATES_DEBUG=1 時のみ詳細出力

fallback_used, rows_used_n, top_support を表示

past-only 時の rows_used_n 二重計上を解消（3358で安定）

後方互換性

get_condition_candidates ラッパを追加

既存呼び出しは破壊なし

最終確認

py_compile：OK

candidates 生成：OK（10 / 50 / 200 すべて安定）

smoke：OK（condition_mining_ops_snapshot.json 出力）

現在の状態（結論）

探索AIは 「候補が出る」「観測できる」「拡張できる」健全な状態

recent_empty_use_past_only は データ由来の正常状態（最新 decision が無い）

コード側の詰まり・バグは解消済み

T-43-3 Step2-21
達成事項（事実ベース）
ops_snapshot（logs/condition_mining_ops_snapshot.json）に top_candidates が確実に出力されるよう修正完了。
candidates と condition_candidates を相互ミラー（既存優先・追加のみ）する処理を facade 末尾ラッパに追加。
スモーク後の確認結果：
has_candidates=True (n=7)
has_condition_candidates=True (n=7)
has_top_candidates=True (n=5)
top0 が期待どおり（id/score/support/confidence/degradation を含む）
lint / py_compile / smoke すべて 問題なし。
変更点（最小差分）
変更ファイル：app/services/condition_mining_facade.py
末尾ラッパ get_condition_mining_ops_snapshot にキー相互ミラーと top_candidates 生成を追加。
既存API・責務境界（gui/services/core）を維持。新規関数なし。
非変更（重要）
既存の候補生成・評価ロジック（score/confidence/degradation）は不変更。
スモークの成功条件・主要キー構造は維持（追加のみ）。
