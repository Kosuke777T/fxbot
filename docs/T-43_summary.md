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

