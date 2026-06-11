# 回测结果与策略演进 / Backtest Results & Strategy Evolution

## 1. 策略发现过程简述 / Discovery Process

本项目的最终策略（EMA20 回踩 + 多周期结构评分 + 3R/1R 保本）并非一蹴而就，而是经过了三个阶段的迭代：

The final strategy (EMA20 pullback + multi-timeframe structure score + 3R target / 1R break-even) was arrived at through three iterative stages:

### 阶段一：突破 / 区间入场（Breakout & Range Entry）

最初的版本在突破或区间边界处直接入场（不等待回调）。代表性测试为 `trades_Range_ZLEMA_2R.csv`（基于 ZLEMA 的区间策略）：交易频率极高（444 笔），但信号质量参差不齐，PF 仅 1.55，胜率 37.2%——典型的"过度交易"问题：很多突破是假突破，入场即被打止损。

The earliest version entered directly on breakouts or range-boundary breaks, without waiting for a retracement. The representative test `trades_Range_ZLEMA_2R.csv` (a ZLEMA-based range strategy) traded extremely often (444 trades) but signal quality was inconsistent — PF only 1.55, win rate 37.2%. This is a classic "overtrading" symptom: many breakouts were false breaks that immediately hit stop-loss.

### 阶段二：回踩入场探索（Pullback Entry Exploration）

为了解决"追高被套"的问题，开始尝试在信号触发后**等待价格回踩**再入场，并测试不同的止盈倍数（R 值）、止损方式和趋势过滤器：

To solve the "chasing and getting trapped" problem, the strategy was changed to **wait for a pullback** after a signal before entering, while testing different take-profit multiples (R), stop-loss methods, and trend filters:

| 测试 / Test | 交易数 / Trades | PF | 胜率 / Win rate | 说明 / Notes |
|---|---|---|---|---|
| `trades_Pullback_1.5R.csv` | 44 | 1.72 | 61.4% | 1.5R 止盈，胜率高但盈亏比偏低 / 1.5R TP, high win rate but low payoff ratio |
| `trades_Pullback_2.0R.csv` | 44 | 1.94 | 56.8% | 2.0R 止盈，PF 改善 / 2.0R TP, PF improves |
| `trades_Pullback_2.0R_BE.csv` | 44 | 3.04 | 45.5% | 加入 1R 保本后 PF 显著提升 / Adding 1R break-even significantly boosts PF |
| `trades_PB_ZLEMA_2R_BE1.csv` | 328 | 1.52 | 35.1% | 加入 ZLEMA 过滤后信号过多，质量被稀释 / ZLEMA filter loosens conditions, too many low-quality signals |
| `trades_PB_EMA50_2R_BE1.csv` | 16 | 0.77 | 25.0% | EMA50 过滤过严，样本太少且 PF<1 / EMA50 filter too strict, too few samples and PF<1 |
| `trades_PB20_Trail_BE1.csv` | 42 | 1.09 | 47.6% | EMA20 + 移动止损，效果一般 / EMA20 + trailing stop, mediocre |

这一阶段的关键发现：**1R 保本机制能大幅提升 PF**（`Pullback_2.0R` → `Pullback_2.0R_BE`，PF 从 1.94 提升到 3.04），但过滤条件的"松紧"需要谨慎权衡——太松（ZLEMA）信号质量差，太严（EMA50）样本量不足。

Key finding: **the 1R break-even mechanism dramatically improves PF** (`Pullback_2.0R` → `Pullback_2.0R_BE`, PF rises from 1.94 to 3.04). However, filter strictness must be carefully balanced — too loose (ZLEMA) yields low-quality signals, too strict (EMA50) yields too few samples.

### 阶段三：最终方案（Final Strategy）

在阶段二的基础上，将过滤条件替换为 **4H 趋势 + 1H 过滤 + 15m 结构评分（≥70 分）**，入场仍为 EMA20 回踩（±0.3%），止盈 3R，1R 触发保本。在此框架下，进一步比较了不同的止损方式：

Building on stage two, the filter was replaced with **4H trend + 1H filter + 15m structure score (≥70)**, while keeping EMA20 pullback entry (±0.3%), 3R take-profit, and 1R break-even. Within this framework, different stop-loss methods were compared:

| 测试 / Test | 交易数 / Trades | PF | 胜率 / Win rate | 说明 / Notes |
|---|---|---|---|---|
| `trades_SL_Original.csv` | 68 | 2.60 | 33.8% | 原始结构性止损（最终采用）/ Structure-based SL (final choice) |
| `trades_SL_Fixed_1.5x.csv` | 66 | 2.06 | 28.8% | 固定 1.5x ATR 止损，PF 更低 / Fixed 1.5x ATR SL, lower PF |
| `trades_SL_ATR_Protect.csv` | 68 | 2.61 | 33.8% | ATR 保护性止损，PF 接近原始方案 / ATR-protected SL, near-identical to original |
| `trades_Trend_EMA20_3R.csv` | 68 | 2.60 | 33.8% | 与原始止损结果一致，确认最终参数 / Matches original SL, confirms final parameters |

最终选用 `SL_Original`（结构性止损）：与 `SL_ATR_Protect` 表现几乎一致（PF 2.60 vs 2.61），但实现更简单、依赖更少，因此作为最终方案。

`SL_Original` (structure-based SL) was chosen as the final method: it performs almost identically to `SL_ATR_Protect` (PF 2.60 vs 2.61) but is simpler and has fewer dependencies.

---

## 2. 各阶段回测对比汇总 / Stage Comparison Summary

| 阶段 / Stage | 代表测试 / Representative test | 交易数 / Trades | PF | 胜率 / Win rate |
|---|---|---|---|---|
| 1. 突破/区间入场 / Breakout & range entry | `trades_Range_ZLEMA_2R.csv` | 444 | 1.55 | 37.2% |
| 2. 回踩入场（最佳）/ Pullback entry (best) | `trades_Pullback_2.0R_BE.csv` | 44 | 3.04 | 45.5% |
| 3. 最终方案 / Final strategy | `trades_SL_Original.csv` | 68 | 2.60 | 33.8% |

> 说明：阶段二的 PF 看似更高（3.04），但样本量小（44 笔）且未引入完整的多周期结构评分过滤；阶段三在引入更严格的 4H/1H/15m 评分体系后，交易数增加到 68 笔且 PF 依然稳定在 2.6 以上，结果更稳健。
>
> Note: Stage 2's PF appears higher (3.04), but the sample size is small (44 trades) and lacks the full multi-timeframe structure-score filter. Stage 3 introduces the stricter 4H/1H/15m scoring system, increasing the trade count to 68 while keeping PF stable above 2.6 — a more robust result.

---

## 3. 关键结论 / Key Conclusions

1. **不要追价入场**：直接在突破/区间边界入场会导致大量假突破止损，PF 显著低于回踩入场。
   **Do not chase entries**: entering directly on breakouts/range edges leads to many false-break stop-outs, with significantly lower PF than pullback entries.

2. **1R 保本是性价比最高的优化**：在几乎不损失盈利空间的情况下，大幅降低了"已盈利又回吐为亏损"的交易比例。
   **1R break-even is the highest-leverage optimization**: it dramatically reduces the share of trades that go from profitable back to loss, with minimal cost to upside.

3. **过滤条件需要"恰到好处"**：过滤太松（如仅用 ZLEMA）会稀释信号质量；过滤太严（如叠加 EMA50）会导致样本不足、统计意义不强。多周期结构评分（4H+1H+15m，≥70 分）是当前找到的较优平衡点。
   **Filters need to be "just right"**: too loose (e.g., ZLEMA only) dilutes signal quality; too strict (e.g., adding EMA50) yields too few samples to be statistically meaningful. The multi-timeframe structure score (4H+1H+15m, ≥70) is the best balance found so far.

4. **止损方式的边际影响有限**：在已经过滤掉低质量信号的前提下，结构性止损与 ATR 保护性止损表现几乎一致（PF 2.60 vs 2.61），优先选择更简单的实现。
   **Stop-loss method has limited marginal impact**: once low-quality signals are filtered out, structure-based and ATR-protected stop-losses perform almost identically (PF 2.60 vs 2.61) — prefer the simpler implementation.

5. **最终结果**：2 年回测（2024–2026），68 笔交易，PF 2.60，年化 +46.43%，最大回撤 4.14%，胜率 33.8%——低胜率、高盈亏比的趋势跟随特征明显，符合 EMA20 回踩 + 3R 目标的预期。
   **Final result**: 2-year backtest (2024–2026), 68 trades, PF 2.60, annualized +46.43%, max drawdown 4.14%, win rate 33.8% — a low win-rate, high payoff-ratio trend-following profile, consistent with the EMA20 pullback + 3R design.
