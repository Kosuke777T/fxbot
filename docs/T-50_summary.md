了解。
このスレッドは 完了 としてクローズします。
以下、①完了サマリ（要点） → ②次スレッドに張り付ける指示 の順で表示します。

🧾 スレッド完了時：要点サマリ（T-50）
スレッド目的

VirtualBT が「動かない／エントリーしない」問題について、
推測せず・観測で確定し、修正に入れる状態まで持っていく。

結論（最重要）

バックテストは正常に動作している

エントリーは実際に発生している（trades=1）

「エントリーが発生しない」は 体感上の誤認
→ 実態は “極端に取引頻度が低い戦略”

観測で確定した事実（OBSERVED）

ENTRY 発生：

bar_index=268 / 2025-12-24 00:00:00

action=ENTRY / side=BUY

prob_buy=0.51048 > threshold=0.5

filter_pass=true（filter_level=3）

position: flat → open（成功）

metrics.json：

trades=1

output_ok=true

trades.csv / decisions.jsonl に記録あり

否定できた仮説（すべて観測でREJECT）

❌ モデル未ロード

❌ prob が 0 / NaN

❌ threshold 判定バグ

❌ filter 常時 reject

❌ ENTRY ロジック不達

❌ BacktestEngine / VirtualBT の不具合

問題の正体

バグではなく設計結果。

threshold=0.5

filter_level=3

prob 分布が 0.48〜0.52 に集中

→ 月1回程度しか刺さらない戦略になっている。

技術的評価

実装健全性：◎

設計整合性：◎

実運用頻度：△（研究段階）

👉 「直すフェーズ」ではなく「調整フェーズ」へ移行可能。

このスレッドで到達した状態

VirtualBT は 信頼できる評価基盤として成立

ENTRY〜決済〜成果物出力まで 因果が一気通貫

T-50-2（調整）に進む前提条件はすべて満たした

