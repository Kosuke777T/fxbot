スレッド目的

Visualize タブを「実行バックテスト直前レベルの可視化UI」に進化させる
（ローソク足＋LightGBM＋無限スクロール＋軽量パン＋N変更）

完了したこと（観測で確定）
1. 可視化タブ基盤

PyQt6 + matplotlib（FigureCanvasQTAgg）流儀を維持

VisualizeTab を新規タブとして統合

上段：OHLCローソク足

下段：LightGBM prob_buy + threshold + crossing marker

2. ドラッグ操作（最軽量）

ドラッグ中は Python 側の処理ゼロ

motion_notify_event は即 return

描画なし・計算なし

release 時のみ

xdata を拾って xlim 確定

軽量モード解除後に refresh()

3. 軽量化対策

ローソク body / wick をドラッグ開始時に非表示

drag 中は完全に axes だけ移動

release → フル再描画

4. 無限スクロール（左方向）

get_recent_ohlcv(until=...) を services に追加

release 時のみ

user_xlim.left < cache最古 を検出

過去OHLCを prepend

キャッシュ方式で過去検証が可能に

5. Ctrl+ホイール（表示本数 N 変更）

N変更時に最新へジャンプする問題を解消

現在の表示中心を固定したままスケール

OHLCキャッシュが十分ある場合は再取得しない

_user_xlim と無限スクロール仕様が整合

6. 不具合修正

N変更時にローソク足が消える問題 → 修正済み

過去表示中に Ctrl+ホイールで最新へ飛ぶ問題 → 修正済み

現在の到達点（設計的評価）

TradingView に近い UX

「時間を自由に行き来できる検証ビュー」が完成

仮想実行バックテストを載せる 土台は完成