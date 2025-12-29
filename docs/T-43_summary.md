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
