# Decision Log Comparison Report

## Summary by Mode/Source/Profile/Timeframe/Symbol

| Mode | Source | Profile | Timeframe | Symbol | n | filter_pass_rate | entry_rate | skip_rate | blocked_rate | side_buy_rate | top_blocked_reason |
|------|--------|---------|-----------|--------|---|-------------------|------------|-----------|--------------|---------------|-------------------|
| demo | stub | unknown | unknown | USDJPY- | 180 | 0.0000 | 0.0 | 0.0 | 1.0 | unknown | adx_low |
| live | mt5 | michibiki_aggr | unknown | USDJPY- | 20 | 0.0000 | 0.0 | 1.0 | 0.0 | 1.0 | volatility |
| unknown | unknown | michibiki_aggr | unknown | USDJPY- | 37 | 0.0000 | 0.0 | 1.0 | 0.0 | 1.0 | volatility |
| unknown | unknown | unknown | unknown | USDJPY- | 480 | 0.0000 | 0.0 | 0.0 | 1.0 | unknown | adx_low |
| unknown | unknown | unknown | unknown | unknown | 3712 | 0.0000 | 0.4119 | 0.5752 | 0.0129 | 0.0011 | ai_skip |

## Live vs Backtest Comparison

同一 (profile, timeframe, symbol) で live(mt5) と backtest(backtest) を比較

### michibiki_aggr / unknown / USDJPY-

| Metric | Live (mt5) | Backtest | Δ |
|--------|------------|----------|---|
| n | 20 | - | - |
| filter_pass_rate | 0.0000 | - | - |
| entry_rate | 0.0 | - | - |
