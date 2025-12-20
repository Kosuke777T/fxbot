# Decision Log Comparison Report

**Coverage:** 2024-07-08 13:25:00 ～ 2025-12-20T22:38:42+09:00

## Summary by Mode/Source/Profile/Timeframe/Symbol

| Mode | Source | Profile | Timeframe | Symbol | n | filter_pass_rate | entry_rate | skip_rate | blocked_rate | side_buy_rate | top_blocked_reasons | min_ts | max_ts |
|------|--------|---------|-----------|--------|---|-------------------|------------|-----------|--------------|---------------|-------------------|--------|--------|
| backtest | backtest | michibiki_std | unknown | USDJPY- | 320 | 1.0000 | 1.0 | 0.0 | 0.0 | 1.0 | - | 2025-12-01 10:45:00 | 2025-12-02 00:00:00 |
| demo | stub | unknown | unknown | USDJPY- | 240 | 0.0000 | 0.0 | 0.0 | 1.0 | unknown | adx_low(33.3%) / time_window(33.3%) / volatility(33.3%) | 2025-12-20T21:30:28+09:00 | 2025-12-20T22:38:29+09:00 |
| live | mt5 | michibiki_aggr | unknown | USDJPY- | 24 | 0.0000 | 0.0 | 1.0 | 0.0 | 1.0 | - | 2025-12-20T21:30:40+09:00 | 2025-12-20T22:38:42+09:00 |
| unknown | unknown | michibiki_aggr | unknown | USDJPY- | 37 | 0.0000 | 0.0 | 1.0 | 0.0 | 1.0 | - | 2025-12-20T20:40:19+09:00 | 2025-12-20T21:25:29+09:00 |
| unknown | unknown | unknown | unknown | USDJPY- | 480 | 0.0000 | 0.0 | 0.0 | 1.0 | unknown | adx_low(33.3%) / time_window(33.3%) / volatility(33.3%) | 2025-12-20T20:20:21+09:00 | 2025-12-20T21:25:20+09:00 |
| unknown | unknown | unknown | unknown | unknown | 406817 | 0.0000 | 0.0038 | 0.9961 | 0.0001 | 0.0 | atr(42.0%) / volatility(42.0%) / time_window(15.8%) | 2024-07-08 13:25:00 | 2025-12-13 08:50:00 |

## Live vs Backtest Comparison

同一 (profile, timeframe, symbol) で live(mt5) と backtest(backtest) を比較

## Unmatched or Unknown (Comparison Not Available)

以下のグループは profile/timeframe/symbol に unknown が含まれるため、比較をスキップします。

### michibiki_aggr / unknown / USDJPY- (比較不能)

**JOIN KEY:** symbol=USDJPY- profile=michibiki_aggr timeframe=unknown

**Live (mt5):** n=24, filter_pass_rate=0.0000

### michibiki_std / unknown / USDJPY- (比較不能)

**JOIN KEY:** symbol=USDJPY- profile=michibiki_std timeframe=unknown

**Backtest:** n=320, filter_pass_rate=1.0000

## Delta Ranking (Top N)

比較可能なペア（JOIN KEY が揃って unknown なし）のみを対象とした差分ランキング（Top 10）

**No comparable pairs found.**

## Alerts

**PASS:** すべての閾値チェックを通過しました。
