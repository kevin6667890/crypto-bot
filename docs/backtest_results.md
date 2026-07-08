# Backtest Results and Strategy Evolution

This document summarizes how the final ETH/USDT strategy was selected. The final setup was not chosen from a single backtest. It came from repeated iteration across entry logic, stop behavior, trend filters, and sample robustness.

## 1. Discovery Process

### Stage 1: Breakout and Range Entry

The earliest versions entered directly on breakout or range-boundary signals. This created too many low-quality trades. A representative test, `trades_Range_ZLEMA_2R.csv`, produced 444 trades with a 1.55 profit factor and 37.2% win rate.

The key issue was overtrading. Many breakouts were false moves, and the strategy often entered after price had already extended. Fees and stop-outs consumed most of the edge.

### Stage 2: Pullback Entry Exploration

The next iteration waited for price to pull back after the initial signal. This reduced chasing behavior and improved signal quality.

| Test | Trades | PF | Win Rate | Notes |
| --- | ---: | ---: | ---: | --- |
| `trades_Pullback_1.5R.csv` | 44 | 1.72 | 61.4% | High win rate, but payoff ratio was too low |
| `trades_Pullback_2.0R.csv` | 44 | 1.94 | 56.8% | Better payoff profile |
| `trades_Pullback_2.0R_BE.csv` | 44 | 3.04 | 45.5% | 1R break-even stop sharply improved PF |
| `trades_PB_ZLEMA_2R_BE1.csv` | 328 | 1.52 | 35.1% | Filter became too loose and admitted weak signals |
| `trades_PB_EMA50_2R_BE1.csv` | 16 | 0.77 | 25.0% | Filter became too strict and sample size collapsed |
| `trades_PB20_Trail_BE1.csv` | 42 | 1.09 | 47.6% | Trailing stop underperformed |

The most important finding from this stage was that the 1R break-even mechanism had the highest optimization value. It prevented profitable trades from reverting into full losses without heavily reducing upside.

### Stage 3: Final Strategy

The final framework uses a multi-timeframe structure filter:

- 4H trend context
- 1H confirmation filter
- 15m structure score
- Signal threshold >= 70
- EMA20 pullback entry
- 3R take-profit
- 1R break-even stop
- Structure-based stop loss

Stop-loss variants were compared inside this final framework:

| Test | Trades | PF | Win Rate | Notes |
| --- | ---: | ---: | ---: | --- |
| `trades_SL_Original.csv` | 68 | 2.60 | 33.8% | Structure-based stop, selected final version |
| `trades_SL_Fixed_1.5x.csv` | 66 | 2.06 | 28.8% | Fixed ATR stop reduced quality |
| `trades_SL_ATR_Protect.csv` | 68 | 2.61 | 33.8% | Similar to original, but more complex |
| `trades_Trend_EMA20_3R.csv` | 68 | 2.60 | 33.8% | Confirmed final parameter set |

`SL_Original` was selected because it matched the ATR-protected version almost exactly while keeping the implementation simpler.

## 2. Stage Comparison

| Stage | Representative Test | Trades | PF | Win Rate |
| --- | --- | ---: | ---: | ---: |
| Breakout / range entry | `trades_Range_ZLEMA_2R.csv` | 444 | 1.55 | 37.2% |
| Pullback entry, best variant | `trades_Pullback_2.0R_BE.csv` | 44 | 3.04 | 45.5% |
| Final validated strategy | `trades_SL_Original.csv` | 68 | 2.60 | 33.8% |

Stage 2 had a higher profit factor, but its sample size was smaller and it did not include the full multi-timeframe scoring model. The final strategy has a lower but more robust profit factor across a broader two-year validation window.

## 3. Key Conclusions

1. **Avoid chasing breakouts.** Direct breakout entries created too many false-break stop-outs.
2. **The 1R break-even rule was the strongest optimization.** It reduced the worst path of trades that first moved in favor and then reversed.
3. **Filters must balance quality and sample size.** ZLEMA was too loose, EMA50 was too strict, and the 4H/1H/15m scoring system gave the best balance.
4. **Stop-loss style had limited marginal impact after filtering.** Structure-based and ATR-protected stops performed almost identically.
5. **The final result is a low win-rate, high payoff-ratio strategy.** This is consistent with a pullback trend-following design using a 3R target.

## 4. Final Metrics

| Metric | Value |
| --- | ---: |
| Profit Factor | 2.60 |
| Annualized Return | +46.43% |
| Max Drawdown | 4.14% |
| Win Rate | 33.8% |
| Trades | 68 |
| Backtest Window | 2 years |
