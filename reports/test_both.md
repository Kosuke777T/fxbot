# Decision Log Comparison Report

## Summary by Mode/Source/Profile/Timeframe/Symbol

| Mode | Source | Profile | Timeframe | Symbol | n | filter_pass_rate | entry_rate | skip_rate | blocked_rate | side_buy_rate | top_blocked_reason |
|------|--------|---------|-----------|--------|---|-------------------|------------|-----------|--------------|---------------|-------------------|
| backtest | backtest | michibiki_std | unknown | USDJPY- | 320 | 1.0000 | 1.0 | 0.0 | 0.0 | 1.0 | - |
| demo | stub | unknown | unknown | USDJPY- | 210 | 0.0000 | 0.0 | 0.0 | 1.0 | unknown | adx_low |
| live | mt5 | michibiki_aggr | unknown | USDJPY- | 22 | 0.0000 | 0.0 | 1.0 | 0.0 | 1.0 | volatility |
| unknown | unknown | michibiki_aggr | unknown | USDJPY- | 37 | 0.0000 | 0.0 | 1.0 | 0.0 | 1.0 | volatility |
| unknown | unknown | unknown | unknown | USDJPY- | 480 | 0.0000 | 0.0 | 0.0 | 1.0 | unknown | adx_low |
| unknown | unknown | unknown | unknown | unknown | 406817 | 0.0000 | 0.0038 | 0.9961 | 0.0001 | 0.0 | atr |

## Live vs Backtest Comparison

同一 (profile, timeframe, symbol) で live(mt5) と backtest(backtest) を比較

### michibiki_aggr / unknown / USDJPY-

| Metric | Live (mt5) | Backtest | Δ |
|--------|------------|----------|---|
| n | 22 | - | - |
| filter_pass_rate | 0.0000 | - | - |
| entry_rate | 0.0 | - | - |

### michibiki_std / unknown / USDJPY-

| Metric | Live (mt5) | Backtest | Δ |
|--------|------------|----------|---|
| n | - | 320 | - |
| filter_pass_rate | - | 1.0000 | - |
| entry_rate | - | 1.0 | - |
