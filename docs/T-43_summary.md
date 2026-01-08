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


T-43-3 Step2-22
目的
ops_snapshot に「採択（adoption）」を services層のみ・最小差分で追加し、smoke後の JSON 反映まで 観測で確認する

■ 実施内容（事実）
logs/condition_mining_ops_snapshot.json に adoption を出力するよう、facade末尾ラッパの get_condition_mining_ops_snapshot に採択結果を付与
採択は「candidates先頭から gate を通る最初の1件」を adopted とし、落ちた候補は rejected に理由コード付きで格納
adoption の要求キー ['status','adopted','rejected','weight','confidence_cap','notes'] が すべて揃うことを確認
触ったレイヤ：services のみ
新規関数：あり（採択構築ヘルパ：_build_condition_mining_adoption）

■ 変更ファイル
app/services/condition_mining_facade.py
get_condition_mining_ops_snapshot（末尾ラッパ側：おおむね 300行目前後）
_build_condition_mining_adoption 追加（おおむね 423行目付近）
tools/condition_mining_smoke.ps1：変更なし（実行のみ）

■ 守った制約
最小差分
既存API優先使用（facade末尾ラッパでの加法）
責務境界（gui/services/core）遵守（servicesのみ）
PowerShell Here-String 前提（docstring をエスケープしない運用）

■ 挙動の変化
変わった点：logs/condition_mining_ops_snapshot.json に adoption が追加され、採択結果が JSON で観測可能になった
変わっていない点（重要）：candidates / condition_candidates / top_candidates の既存出力と smoke の流れは維持（破壊的変更なし）

■ 確認方法
実行した確認コマンド
python -m py_compile app/services/condition_mining_facade.py
python -c "import app.services.condition_mining_facade"
pwsh -File tools/condition_mining_smoke.ps1 -Symbol "USDJPY-"
logs/condition_mining_ops_snapshot.json の adoption キー確認
OKと判断できる条件
adoption が存在
adoption.status = adopted
missing_keys = []
adopted が例の通り（例：{'id':'hour:h08_15','weight':1.0,'condition_confidence':'MID','note':None}）


T-43-3 Step2-23
目的
**Condition Mining の adoption が「どこで消費されるべきか」**をコードベース全体から観測し、
一次採用の消費地点を安全に確定する。
観測結果（事実ベース）
adoption / confidence_cap / weight / notes は
condition_mining_facade.py 内で生成されるのみで、
実運用（execution / sizing / entry）では未消費だった。
ops_snapshot.json は facade 経由で確定しており、smoke も facade を参照。
消費地点の確定（設計判断）
一次採用 = guard（安全優先）
消費地点を
👉 ExecutionService.execute_entry() の「発注直前（dry_run 分岐直前）」
に固定。
実装内容（最小差分・services層のみ）
対象ファイル
app/services/execution_service.py
差し込み位置
execute_entry() 内、dry_run 判定の直前
処理内容
get_condition_mining_ops_snapshot(symbol) を呼び出し
adoption.status == "adopted" の場合のみ
decision_detail["cm_adoption"]
meta_val["cm_adoption"]
に 加法で埋め込み（既存キー破壊なし）
検証結果
py_compile / import：OK
dry_run=True で execute_entry() を1回実行
最新の logs/decisions_*.jsonl に：
meta.cm_adoption が存在
decision_detail.cm_adoption が存在
execution_service が adoption を「消費（参照）」していることを客観的に確認
重要な制約・判断
この repo には PROMOTE という action 概念が存在しない
よって Step2-23 では 挙動変更（PROMOTE→HOLD 等）は不可
本ステップでは
「消費地点の確定」と「観測可能にする」
に限定し、安全に完了とした。

T-43-3 補助フェーズ
目的達成状況
T-43-4 には進まず、adoption 消費ラインの足元固め・観測強化のみを実施
挙動変更なし（観測・確認・文章化のみ）
確定した事実（ログ＋実体コード根拠）
adoption の生成と消費
生成点：condition_mining_facade.py
out["adoption"] = _build_condition_mining_adoption(out)
消費点：execution_service.py
**発注直前（dry_run / MT5発注の直前）**に Step2-23 ブロックとして存在
services 層の健全性
python -m py_compile app/services/**/*.py → 成功
既存 smoke（condition_mining_smoke.ps1）→ 成功
観測の安定性（決定的証拠）
最新 logs/decisions_2026-01-07.jsonl において：
"meta.cm_adoption" が存在
"decision_detail.cm_adoption" が存在
文字列ベースの2条件チェックでも 両方OK
dry_run / real 相当経路で 片肺なし
不変条件（この先も守る前提）
cm_adoption は 観測用メタ情報
この段階では：
発注条件・sizing・next_action に 一切影響しない
decisions ログでは常に：
meta.cm_adoption
decision_detail.cm_adoption
が 同時に存在すること
破壊検知ポイント（NG条件）
以下のいずれかが起きたら 即 No-Go
meta.cm_adoption が消える
decision_detail.cm_adoption が消える
片方だけ残る（片肺）
dry_run と real 相当で結果が食い違う
Go / No-Go 判定
Go（T-43-4 に進める条件）
上記2キーが decisions に安定して残る証拠がある
adoption 消費境界（発注直前）が明確に特定されている
破壊検知を1コマンドで再確認できる
No-Go
上記いずれかが満たせなくなった場合
補足仕様の確定
status=adopted 以外（none / adoption_failed）を
現時点ではログに載せない仕様のままでOK
理由：本フェーズの目的は「存在保証」であり、すでに達成済み

T-43-3-1 Step1〜Step2-0
目的（達成状況）
cm_adoption を観測メタから実挙動へ安全に接続する経路を services 層のみで構築
破壊検知可能な形で、段階的に挙動（sizing）へ影響させる基盤を完成
→ 達成
確定した事実（観測証拠ベース）
介入点の一点固定
execution_service.execute_entry() 内
Step2-23 直後〜 if dry_run: 直前 が「発注直前相当」の唯一の介入点
cm_adoption → size_decision（Step1）
adopted.condition_confidence を正しく抽出
マッピング：
HIGH → 1.20
MID → 1.00
LOW → 0.50
無し/不明 → 1.00
decision_detail.size_decision を必ず生成
フォールバック（cm_adoption_missing）を実ログで確認
order_params の生成（Step2-0）
同一介入点で order_params を必ず生成
order_params.size_multiplier == size_decision.multiplier を保証
decision_detail.order_params に保存
dry_run 戻り値 / 通常戻り値の両方に追加
致命的不具合の修正（Hotfix）
simulated-return が if dry_run: の外にあり、通常パスが死んでいた問題を修正
修正後：
dry_run=True → simulated=True で早期return
dry_run=False → 通常パスに到達（実行ログで証明）
実ログによる検証証拠
dry_run
simulated=True
order_params あり
dry_run=False
simulated キー 存在しない
order_params あり
整合性
order_params.size_multiplier == size_decision.multiplier
フォールバック
cm_adoption_missing が実ログに出現（落ちない）
変更範囲
services 層のみ
app/services/execution_service.py
既存キー削除・改名なし（追加のみ）
GUI / core 未変更

T-43-3-2 Step2-1
order_params の schema 固定を完了
ensure_order_params_schema() を 追加のみ に厳密化
既存キー・既存値の 変更／型変換は一切なし
schema_version を 固定アンカー（v1） として setdefault で付与
services 層のみで実装（責務境界を厳守）
観測 → 実装 → 再観測 を順守
観測結果（確定）
最新 logs/decisions_2026-01-07.jsonl にて確認
order_params.keys：
['mode', 'pair', 'schema_version', 'side', 'size_multiplier', 'size_reason', 'symbol']
末尾サンプルに schema_version: 1 / pair / mode を確認
dry_run / live 分岐に影響なし
変更ファイル
app/services/order_params_schema.py（新規／追加のみ）
成功条件の達成状況
✔ schema 固定アンカー（schema_version）が実ログに存在
✔ 既存キーの欠落・意味変更なし
✔ services 層のみで完結
✔ 将来の実発注ロジックと自然接続可能な形を確保

T-43-3-3 Step2-2
目的の達成状況
order_params schema 再観測の運用固定を実現
schema_version をアンカーにした OK / NG 判定を終了コードで即時返す仕組みを確立
人手レビュー不要・観測のみ（read-only） の運用ガードが成立
実装内容（スコープ厳守）
tools 配下に新規2ファイルのみ追加
tools/reobserve_order_params_schema.py
tools/reobserve_order_params_schema.ps1
services / core / gui は 一切未変更
ログは 読み取り専用（書き込み・改変なし）
再観測ツールの仕様ポイント
最新 logs/decisions_*.jsonl を 自動検出
order_params の取得において
トップレベル / decision_detail.order_params の両方を観測対象
観測結果として以下を必ず出力：
order_params 行数
keys の union 一覧
schema_version 欠落行の明示
末尾 N 件（tail）のサンプル表示
判定結果：
OK：exit code 0
NG：exit code 2 ＋ 失敗理由を明示
動作確認（(1) 通常：最新自動検出）
実行コマンド：
pwsh -NoProfile -ExecutionPolicy Bypass -File tools/reobserve_order_params_schema.ps1 -Tail 5
観測結果（要点）：
最新ログ：logs/decisions_2026-01-07.jsonl
観測行数：order_params.rows > 0
keys_union：['mode', 'pair', 'schema_version', 'side', 'size_multiplier', 'size_reason', 'symbol']
schema_version 欠落行あり → [NG]
終了コード：2
→ ツールは仕様通り正しく異常を検知
判断
Step2-2 の目的（再観測基盤の確立）は 完了
残課題は「過去ログ混在で常時NGになる問題」への対処であり、
これは 次ステップ：(2) 判定スコープ追加 に切り出すのが適切

T-43-3-4 Step2-2-1
目的
order_params schema 再観測において、
過去ログ混在で常時 NG になる問題を回避しつつ、
将来の破壊は観測だけで即 NG にできる運用を確立する。
実施内容（確定観測ベース）
変更範囲を tools のみに限定
tools/reobserve_order_params_schema.py
tools/reobserve_order_params_schema.ps1
read-only 厳守（ログ書き込みなし）
Python / PS1 両方に --scope {all|tail} を追加（既定 all）
判定対象と観測統計を明確に分離：
rows_all：全対象行（統計用・常に表示）
rows_scope：判定対象行
all → 全行
tail → 末尾 N 件（--tail）
OK/NG 判定は rows_scope のみ
全体欠落数・比率は scope に関わらず必ず表示
NG 時は「満たされなかった条件」を明示し exit code=2
OK 時は exit code=0
py_compile 実行済み・構文エラーなし
動作確認結果（Cursor 実行）
all（既定）
判定対象：全14行
schema_version 欠落あり → NG
終了コード：2
tail（末尾5件）
判定対象：末尾5行
末尾にも欠落あり → NG
終了コード：2
併せて全体統計（14行中13欠落）を表示
※ 末尾 N 件がすべて schema_version を持つ状態では
　-Scope tail は exit 0 になる設計。
成功条件チェック
tools のみ最小改修：✅
既存 all 挙動維持：✅
判定スコープ追加（tail）：✅
全体欠落の事実を非隠蔽：✅
NG 理由明示＋終了コード安定：✅
CI / ローカルで同一結果：✅

目的
order_params の schema_version が付与される正規生成経路を
コード変更なし（read-only）・推測なしで観測により確定する。
実施内容（観測のみ）
最新 logs/decisions_2026-01-07.jsonl を行単位で集計し、
schema_version の 有無／位置（nested/top/none） を分類。
同一ファイル内で schema_version が混在している事実を確定。
grep とコード読取により、
decision ログの writer 経路と order_params 生成箇所を現行コード上で特定。
確定した事実
schema_version 欠落は すべて decision_detail.order_params（nested）。
writer は 別系統ではなく単一：
ExecutionService → DecisionsLogger.log() → execution_stub._write_decision_log()。
decision_detail["order_params"] を代入しているのは execution_service.py のみ。
現行コードには order_params 構築直後に ensure_order_params_schema() を通す実装が存在。
欠落行（13行）は、schema_version 付与前の旧実行由来の行が同日ファイルに混在しているものと、ログ＋grepから確定。
正規生成経路（1行定義）
ExecutionService.execute_entry() が order_params を確定直後に ensure_order_params_schema() を通し、decision_detail["order_params"] として設定した上で、DecisionsLogger.log() → _write_decision_log() により logs/decisions_YYYY-MM-DD.jsonl に追記する経路。
設計判断
欠落自体は レガシー混在としては仕様。
ただし運用ゲートが reobserve --scope=tail のため、
今後の tail に欠落が混入する場合は設計矛盾＝バグとして services 側の生成責務で扱う。


目的
境界時刻（schema_version 初付与）以降に、schema_version 欠落が再発していないかを
read-only 観測のみで最終確定する。
前提（確定事項）
schema_version は
ExecutionService → DecisionsLogger → _write_decision_log
の単一正規経路で付与される設計。
同一 decisions_YYYY-MM-DD.jsonl 内の欠落は
境界以前のレガシー run 混在が原因。
reobserve --scope=tail は運用健全性チェックとして使用可能。
観測結果（read-only）
対象ログ：decisions_2026-01-07.jsonl
境界時刻（初付与）
line_no = 26
timestamp = 2026-01-07T17:58:55+09:00
reobserve --scope=tail（200/500/1000）
境界以前の欠落混在により 全て NG（想定通り）
boundary-filtered（境界以降限定）再チェック
tail=200/500/1000 すべて欠落 0 / exit=0
推測なしの結論（断言）
2026-01-07T17:58:55+09:00 以降に生成された decisions では
schema_version 欠落は再発していない。
services 修正は不要。
運用上の注意（設計判断）
生の reobserve --scope=tail は、レガシー混在ログでは常時NGになり得る。
運用ゲートとしては boundary-filtered 判定が有効。
境界以降の行数が増えたタイミングで、同じ観測を再実行すれば信頼度がさらに上がる。

◆T-43-4　Step0
■ 目的
Condition Mining の候補を TOP10 + description付きで smoke 出力から観測できるようにし、欠落時は exit!=0 で検知可能にする（基盤は壊さない）
■ 実施内容（事実）
services：候補カードに トップレベル id/description/tags を setdefault/追加のみでミラー（既存の condition:{id,description,tags} は維持）
tools：smoke に TOP10表示 と description 欠落検知（非0終了） を追加
触ったレイヤ：services / tools（gui/core は未変更）
新規関数：あり（最小：id から description を作る補助のため）
■ 変更ファイル
app/services/condition_mining_candidates.py（候補dict生成点：cards.append({...}) 付近）
tools/condition_mining_smoke.ps1（snapshot生成後に TOP10 表示/欠落検知を追記）
■ 守った制約
最小差分（追加のみ、既存キー/構造は維持）
既存API優先（get_condition_mining_ops_snapshot() の経路を利用）
責務境界（gui/services/core）遵守（GUI改修なし）
logs の削除・加工なし
PowerShell 7 / Here-String 前提
■ 挙動の変化
変わった点：smoke 出力だけで TOP10が description 付きで観測でき、欠落があれば即 [NG] で終了
変わっていない点（重要）：condition_mining_ops_snapshot の必須キー互換・候補の既存ネスト構造・decisions の schema_version 付与経路
■ 確認方法
python -m py_compile app/services/condition_mining_candidates.py
pwsh -File tools/condition_mining_smoke.ps1 -Symbol "USDJPY-"
schema_version gate（boundary-filtered）再観測：tail=200/500/1000 で missing=0
OK条件：smoke に TOP10行が id | description | ... で並び、最後に [OK]／ゲート missing=0

T-43-4 Step1
目的：ops_snapshot に top_candidates を 常設キーとして追加し、snapshot 単体で上位候補（top_k件）を観測できる状態を作る（順序維持・再ソートなし、追加のみ）。
観測で確定した実装点：app/services/condition_mining_facade.py の末尾に ops_snapshot の末尾ラッパが存在し、ここで candidates 注入、candidates/condition_candidates ミラー、top_candidates 付与、adoption 付与を行っていた。
condition_mining_facade
最小パッチ（facade側・追加のみ）
out.setdefault("top_k", ...) を追加し、top_candidates の根拠値を同梱（互換加法）。
top_candidates を candidates[:top_k] に変更（候補の順序を尊重・再ソートなし）。
top_candidates の要素を {id, description, score, support, condition_confidence, degradation, tags} に正規化（値は候補トップレベル優先、無ければ condition.* から安全に取得）。
動作確認（観測）
py_compile：condition_mining_facade.py OK、services全体OK（PowerShell互換の -c 方式で実行）。
smoke：tools/condition_mining_smoke.ps1 OK（Step0 の TOP10表示/欠落検知も維持）。
snapshot：logs/condition_mining_ops_snapshot.json に top_k と top_candidates が常設、top_candidates_n == top_k == 10、先頭要素に必要キーが揃うことを確認。
schema_version gate：boundary-filtered（tail=200/500/1000）で missing=0 維持。
変更ファイル：app/services/condition_mining_facade.py（末尾ラッパの top_candidates 生成ブロック周辺）
condition_mining_facade
守った制約：既存API優先／新規関数最小／責務境界維持（GUI無改修）／ログ削除・加工なし／闇リファクタなし。
