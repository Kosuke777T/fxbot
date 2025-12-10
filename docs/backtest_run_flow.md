# BacktestRun Flow 分解（T-30 STEP2）

## 1. main → run_backtest / run_wfo の全体構造
    main のコールグラフはこうでした：

    main -> ArgumentParser, Path, _build_period_tag, _mirror_latest_run, _normalize_dates_from_args, ..., run_backtest, run_wfo, ...

    ここから読み取れるトップレベルのフローはほぼこうです：

    引数パース

    ArgumentParser / add_argument / parse_args

    ここで

    バックテスト期間（from / to）

    モード（単純 BT か WFO か）

    データ置き場（base_dir など）
    を決めている。

    パスと期間の正規化

    Path, resolve, mkdir

    _normalize_dates_from_args
    → Timestamp, to_datetime, _norm, strftime などを呼んでいるので、
    → CLI 引数の文字列を「きれいな日付」に正規化し、内部フォーマット（pd.Timestamp）にしている。

    期間タグの作成

    _build_period_tag
    → 期間（例: 2024-01-01_2024-03-31）からディレクトリ名用のタグを作る役。

    本体の実行

    モードに応じて どちらか を呼ぶ：

    run_backtest(...)

    run_wfo(...)

    最新実行結果のミラー

    _mirror_latest_run(period_dir, base_dir)

    glob / read_bytes / write_bytes などを使っているので、

    backtest/YYYYMMDD_.../ の成果物を

    backtest/latest/ みたいな場所にコピーしているイメージ。

    最後にパスを print

    print(str(p), flush=True)
    → 実行した結果ディレクトリを標準出力に出して、他ツールから使いやすくしている。

    ここまでが「BacktestRun ランチャー」としての全体像です。

## 2. run_backtest の詳細フロー
    コールグラフを見ると：

    run_backtest -> RuntimeError, compute_monthly_returns, dumps, metrics_from_equity, mkdir, print, read_csv, slice_period, to_csv, to_equity, trade_metrics, trades_from_buyhold, update, write_text

    ざっくりこういう流れになっています：

    (1) データ読み込み & 期間スライス

    read_csv
    → MT5 などから作った CSV（ヒストリカルOHLCV + 何かしらのシグナル列 or トレード情報）を読む。

    slice_period
    → 読み込んだ DataFrame を、指定された期間だけに絞る。

    (2) トレード列の生成（Buy&Hold 基準）

    trades_from_buyhold

    DataFrame, Timestamp, int, float などを使っているので、

    指定期間を「1エントリー1エグジット」の Buy&Hold トレード列 に変換している。

    これが「ベンチマーク（何もせず持ちっぱなし）」のトレード。

    (3) エクイティカーブの構築

    to_equity

    内部では DataFrame, pct_change, cumprod, fillna を使うので、

    価格なりトレード結果なりから 口座残高の推移（equity曲線） を作っている。

    ※ equity_from_trades / equity_from_trade_df も同じグループの関数ですが、
    run_backtest からは to_equity と trades_from_buyhold だけが直接呼ばれているのがポイントです。
    → pure backtest モードでは「シグナル生成」はやっていない（シグナルはCSV側に既にあるか、もしくは単純な Buy&Hold のみ）。

    (4) 評価指標（KPI）の計算

    trade_metrics

    内部で _max_consecutive（連勝・連敗カウント）を使っている。

    勝率、平均損益、最大連敗 など「トレード単位の統計」を出す係。

    metrics_from_equity

    _dd_duration_max（ドローダウン期間）/ pct_change / std / sqrt / mean などを使っている。

    最大DD, シャープレシオっぽい指標, RSI的なものはなく、純粋に「エクイティ曲線から取れる統計」。

    ここまでで、

    「トレード一覧」

    「エクイティカーブ」

    「KPI一式」

    が揃う。

    (5) 月次リターン CSV の作成

    compute_monthly_returns

    さらに中で calc_dd, groupby, first, last, min, cummax, read_csv, to_csv などを呼んでいる。

    エクイティ or 日次損益を月単位に集計して、

    monthly_returns.csv（公式フォーマット）を吐いている。

    monthly_returns_from_equity

    より「生の equity → 月次集計」をやる低レイヤ関数。

    compute_monthly_returns は「既存の equity CSV を読み直して再集計」という用途もできるっぽい名前。

    (6) 結果の保存

    mkdir, to_csv, write_text, dumps, update, print

    metrics.json 的な JSON

    equity.csv

    trades.csv

    monthly_returns.csv
    などを「期間ディレクトリ」配下に書き出し。

## 3. run_wfo の詳細フロー
    run_wfo のコールグラフ：

    run_wfo -> DataFrame, RuntimeError, Series, _one, astype, build_features, dumps, equity_from_trade_df, equity_from_trades, int, isinstance, len, load_active_model, max, metrics_from_equity, mkdir, monthly_returns_from_equity, predict_signals, print, read_csv, read_text, reset_index, slice_period, sum, to_csv, to_datetime, to_equity, trade_metrics, trades_from_buyhold, trades_from_signals, update, write_text

    こちらは AIモデル＋WFO再学習 を含む高機能モード。

    (1) データ・モデルの準備

    read_csv：ロウデータを読み込み。

    build_features：特徴量生成（インジケータやラグ特徴量）。

    load_active_model / read_text：active_model.json などから、現在使うべき学習済みモデルをロード。

    to_datetime, slice_period：期間ごとに WFO 窓を切る。

    (2) 各フォールド（期間）を _one が担当

    _one のコールグラフ：

    _one -> DataFrame, Series, astype, build_features, equity_from_trade_df, equity_from_trades, int, isinstance, len, load_active_model, metrics_from_equity, monthly_returns_from_equity, predict_signals, print, reset_index, sum, to_csv, to_datetime, to_equity, trade_metrics, trades_from_buyhold, trades_from_signals, update, write_text

    これが 「1フォールド分のミニBacktest」 の役割です。

    中では：

    build_features：該当期間の特徴量を作る。

    load_active_model：その期間で使うモデルをロード。

    predict_signals：特徴量から CALL/PUT/NO-TRADE のシグナル列を作る。

    trades_from_signals：シグナル列 → トレード列。

    equity_from_trades / equity_from_trade_df / to_equity：エクイティカーブ生成。

    trade_metrics / metrics_from_equity：そのフォールド単体のKPIを算出。

    monthly_returns_from_equity：フォールド毎の月次リターンもつくる（必要に応じて）。

    to_csv / write_text / update：中間・最終成果物を書き出し・集約。

    (3) 全フォールドの集約

    run_wfo では、複数フォールドについて _one を回して、

    全期間のトレードを連結

    全期間のエクイティを再計算

    metrics_from_equity / monthly_returns_from_equity でもう一度「全体のKPI」を出す

    最終的な monthly_returns.csv / metrics.json などを保存

    という構造になっているはずです。

## 4. 補助関数（Peripherals）の役割まとめ
    ざっくりジャンル分けすると：

    期間・日付周り

    _norm, _normalize_dates_from_args, _build_period_tag, slice_period

    トレード生成

    trades_from_signal_series（内部で呼ばれていて、エントリー/エグジットを組み立てる本体）

    trades_from_signals（trades_from_signal_series のラッパー）

    trades_from_buyhold（ベンチマーク用）

    エクイティカーブ

    equity_from_trades

    equity_from_trade_df

    equity_from_bnh

    to_equity（一番外側の「とりあえず equity にする便利関数」）

    KPI & DD

    trade_metrics（トレード単位）

    metrics_from_equity（エクイティ単位）

    _max_consecutive（最大連勝/連敗）

    _dd_duration_max（DD期間）

    _month_dd, calc_dd（月次DD）

    月次集計

    monthly_returns_from_equity

    compute_monthly_returns

    実行後処理

    _mirror_latest_run

    この作りになっているので、T-30 のゴールである：

    StrategyBase → Filter → SimulatedExecution の統一

    monthly_returns.csv（公式形式）を必ず出力

    バックテスト版 decisions.jsonl も出力

    に対しては、

    シグナル〜トレード〜エクイティ〜KPI〜月次リターン はすでに BacktestRun にほぼ実装済み。

    decisions.jsonl の出力 だけがまだ組み込まれていない（＝これからやる T-30 のメイン作業）。

    という立ち位置だと整理できます。

## 5. 今後の T-30 でこの仕様がどう活きるか
