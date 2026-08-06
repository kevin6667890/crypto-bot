# Golden fixtures and evaluation

All fixtures are synthetic. Dates and numbers exist only to exercise the contract and must never be presented as current ETH market data.

## Golden ETH breakout

`golden_eth_breakout_context_v1.json` encodes a compression between 1845 and 1890, confirmed upside breakout of 1890, impulse near 1928, falling OI and expanded volume with positive CVD during the impulse, then a pullback near 1900 with modest OI recovery. The five timeframe objects express 15m pullback, stronger 1H, 4H confirmation in development, 1D repair and weekly long-MA pressure.

The expected semantic assertions are in `golden_eth_breakout_expectations_v1.json`. A valid report need not match wording, but it must say that short covering was one primary driver, active buying cannot be excluded, breakout already occurred, current state is retest/pullback validation, 1885–1892 is defense, 1928 is continuation confirmation, daily/weekly pressure remains, a failure path exists, and this is not evidence for declaring a long-term bull market.

## Counterexamples

1. `order_flow_new_longs_v1.json`: price up, OI up, CVD up → `NEW_LONGS_DOMINANT`.
2. `order_flow_short_cover_weak_cvd_v1.json`: price up, OI down, weak CVD → `SHORT_COVERING_DOMINANT`, with insufficient active-buy confirmation.
3. `order_flow_new_shorts_v1.json`: price down, OI up, CVD down → `NEW_SHORTS_DOMINANT`.
4. `order_flow_long_liquidation_v1.json`: price down, OI down, CVD down → `LONG_LIQUIDATION_DOMINANT` (liquidation evidence can strengthen but not be invented).
5. `order_flow_gap_insufficient_v1.json`: CVD and OI gaps → `INSUFFICIENT_EVIDENCE`, null change values and `INSUFFICIENT` confidence.

## Anti-pattern failures

Without nearby cited numbers, timestamps and source quality, the following fail: “多空博弈激烈”, “关注支撑压力”, “走势有不确定性”, “指标可能上涨”, “控制风险”, “成交量有所变化”, “OI下降”, “多头增强”, “空头减弱”, or the tautology “可能延续也可能回调”.

It also fails when the synthesis and timeframe sections repeat one claim; every timeframe says “偏多但注意风险”; confirmation and unconfirmed claims conflict without a timeframe distinction; or paraphrases repeat the same fact without adding evidence.

## Automated metrics

| Metric | Formula | Initial pass threshold |
|---|---|---|
| numeric grounding ratio | numeric tokens whose citation resolves to an equal context numeric value / all report numeric tokens excluding section ordinals | ≥ 0.98; 1.00 required for price/size/rate claims |
| repeated claim ratio | duplicate normalized claim fingerprints beyond first occurrence / all claim fingerprints | ≤ 0.15 |
| unsupported claim count | factual claims with no resolving context/macro citation or listed unsupported status | 0 |
| contradiction count | mutually exclusive claims over same instrument/timeframe/time without an explicit scope explanation | 0 |
| invalidation coverage | directional conclusions and scenarios with explicit invalidation / all such conclusions | 1.00 |
| data-quality disclosure | material partial/stale/missing/gap conditions mentioned / material conditions in context | 1.00 |
| scenario completeness | branches containing trigger, confirmation, path, target, invalidation and volume/CVD/OI checks / three branches | 1.00 |

Additional gates: required FULL section coverage 1.00; all five timeframe names exactly once; qualitative likelihood only; no exact probability; position-specific quantities forbidden for `source=NONE`; no `UNKNOWN` promotion; and no current/open candle used as confirmation.

Evaluation compares semantic claim IDs and context pointers, not exact prose. Historical replay in AI-5 freezes `decision_time`, source versions, context hash, prompt version and model response so changes can be attributed to a specific layer.
