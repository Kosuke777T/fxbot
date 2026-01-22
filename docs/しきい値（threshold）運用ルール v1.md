✅ ミチビキ：しきい値（threshold）運用ルール v1
0) 前提

weekly_retrain.py の optimize_threshold() は

fold別・全体（fold=-1）を評価し

min_trades 未満は eligible=False で除外する（ノイズ遮断）

1) 採用する best_thr（本番で使う閾値）

本番採用閾値 = 全体（fold=-1）の eligible の中で total 最大の thr

つまりログのこれ：

[THR][run_id=xxxx] grid_results=...

[THR][run_id=xxxx] best_thr=...

この best_thr を そのまま採用。
fold別の best_thr は 観測用（参考） に留める。

2) min_trades（ノイズ除外の下限）

固定値：min_trades = 500（当面これで固定）

理由：

0件〜数十件みたいな「極端なthr」で勝って見える現象を切る

今のデータ量（2万/fold）に対して十分小さく、実運用を邪魔しない

3) 例外ルール（安全側のガード）
3-1) eligibleが全滅したら？

全体（fold=-1）で eligibleが1つも無い場合：

best_thr = 0.50（安全デフォルト）

ログに WARN: no eligible thr, fallback=0.50 を出す
（※この状況は基本起きないはず。起きたら設定/データ異常）

3-2) best_thr が前回から大きく飛んだら？

abs(best_thr - prev_best_thr) >= 0.10 のとき：

採用はする（ルールはブレさせない）

ただしログに WARN: best_thr jump prev=... now=... を出す
（相場レジーム変化 or 特徴量/ラベルの異常の早期発見）

4) “安定度” の運用判定（売買ON/OFFには使わない）

売買のON/OFFを threshold に混ぜると事故るので、これは監視用。

fold_best_thr の mode率 を出す

mode_ratio = (最頻thrの出現数 / fold数)

目安：

mode_ratio >= 0.75 → 安定

mode_ratio == 0.50 → 不安定（観測強化）

mode_ratio < 0.50 → 何か変（データ or 学習が変）

※ ただし 採用thrは常に全体best。ここは絶対に崩さない。

5) 保存と参照（運用で迷わないための「唯一の正」）

run_id単位でこれだけ見ればOKにする：

ログ：logs/weekly_retrain_YYYYMMDD_HHMMSS.log

[THR][run_id=...] best_thr=...

CSV：logs/retrain/thr_grid_{run_id}.csv

後から「なぜそれになったか」を解析する用

運用者向け：今日の判断

今回の run では、全体（fold=-1）で
best_thr = 0.45 が total 最大なので、

✅ 本番採用thrは 0.45

fold1が0.50で勝ってても、運用ルール上は揺らさない。