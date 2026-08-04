# Market State Engine V2

`MarketStateEngineV2` 是只读、确定性、因果的市场状态解释层。引擎版本为
`market-state-engine-v2`，规则版本为 `market-state-definitions-v2.1`。唯一事实输入是
`MarketAnalysisContextV2`；引擎自身不查询 raw 表、不重新计算技术指标、不写数据库、不调用
旧决策引擎、订单逻辑或 LLM。

> 市场状态识别，不是交易信号。

## 周期职责

| 周期 | 职责 | 约束 |
|---|---|---|
| 1W | 长期结构背景 | 只使用已确认 UTC 周线，不参与低周期即时触发 |
| 1D | 中期方向和结构风险背景 | 判断反弹、回调及结构是否受损 |
| 4H | 主要市场环境 | 识别趋势、区间、均线测试及结构转换 |
| 1H | setup context | 识别延续、修复、回撤、破位及动量释放 |
| 15m | trigger context | 识别短线确认、拒绝、极端动量和噪声；不能推翻 1D/4H |

每个周期保留自己的唯一主状态。跨周期结果不是五周期平均或多数投票。

## 周期主状态

周期状态固定为：`TREND_UP`、`TREND_DOWN`、`RANGE_LOW_VOLATILITY`、
`RANGE_HIGH_VOLATILITY`、`TRANSITION_UP`、`TRANSITION_DOWN`、
`TRANSITION_MIXED`、`UNKNOWN`。

趋势评分同时读取价格相对 EMA20/MA60/MA200 的位置、均线排列、三条均线斜率、
rolling high/low 结构、price momentum、momentum persistence，并把 ATR%、realized
volatility 和因果 compression/expansion percentile 作为环境证据。规则要求趋势证据分至少
55、方向差至少 24；因此单独位于 MA200 一侧不能形成趋势。Stoch RSI 不参与主趋势评分。
分位数完全来自 Context V2 已生成的截至当时滚动窗口，状态层不会在全数据集上重算分位数。

总状态固定为：`HTF_UPTREND_CONTINUATION`、`HTF_DOWNTREND_CONTINUATION`、
`HTF_UPTREND_PULLBACK`、`HTF_DOWNTREND_BOUNCE`、`MAJOR_SUPPORT_TEST`、
`MAJOR_RESISTANCE_TEST`、`RANGE_ROTATION`、`BREAKOUT_DEVELOPING`、
`BREAKDOWN_DEVELOPING`、`FAILED_BREAKOUT_DEVELOPING`、`VOLATILITY_TRANSITION`、
`NO_CLEAR_STATE`、`INSUFFICIENT_DATA`。

## Overlay 定义

Overlay 只描述可并存事实，不改变主状态的含义：

- 趋势/回调：`PULLBACK_TO_EMA20`、`PULLBACK_TO_MA60`、`TESTING_MA200`、
  `RECLAIMING_EMA20/MA60/MA200`、`TREND_ACCELERATION`、`TREND_DECELERATION`。
- 动量：`MOMENTUM_OVERBOUGHT`、`MOMENTUM_OVERSOLD`、`MOMENTUM_RECOVERING`、
  `MOMENTUM_ROLLING_OVER`、`MOMENTUM_DIVERGENCE_CANDIDATE`（保留版本化语义）。
- 波动：`VOLATILITY_COMPRESSION`、`COMPRESSION_RELEASE_CANDIDATE`、
  `VOLATILITY_EXPANSION`、`VOLATILITY_NORMAL`、`EXPANSION_EXHAUSTION_CANDIDATE`、
  `EXHAUSTION_CANDIDATE`。
- 结构：`BREAKOUT_CANDIDATE`、`BREAKDOWN_CANDIDATE`、`BREAKOUT_CONFIRMED`、
  `BREAKDOWN_CONFIRMED`、`FAILED_BREAKOUT_CANDIDATE`、`FAILED_BREAKDOWN_CANDIDATE`、
  `RANGE_BOUNDARY_TEST`、`MID_RANGE_NOISE`（保留版本化语义）。
- flow：四种 price/OI 组合、`CVD_CONFIRMING_PRICE`、`CVD_DIVERGING_PRICE`、
  `FLOW_UNAVAILABLE`、`FLOW_PARTIAL`、`FLOW_STALE`。

Flow 只提供确认、冲突或不可用信息。它不被解释成新增方向仓、回补或清算完成。

## MA200 测试

每个周期独立计算 Context 已提供的 `close_distance_to_ma200`。接近阈值为
`max(0.10%, ATR% × 0.25)`，确认缓冲为 `max(0.15%, ATR% × 0.35)`。输出区分
`TESTING_MA200_FROM_ABOVE`、`TESTING_MA200_FROM_BELOW`、
`MA200_RECLAIM_CANDIDATE`、`MA200_BREAKDOWN_CANDIDATE` 和上影拒绝条件下的
`MA200_REJECTION_CANDIDATE`。

判断同时保留 MA200 slope、confirmed candle、数据质量和 confluence sources；levels 中合并的
swing low、VPVR VAL/POC 等仍可通过 confluence sources 识别。接触本身不会被称为反弹。
只有 compare 中后续确认快照维持、重入或重新站回，才升级序列阶段。

## 支撑压力互动

`LevelInteractionV2` 以 ATR 缩放区域输出 `APPROACHING`、`TOUCHING`、
`INSIDE_ZONE`、`BROKEN`、`RECLAIMED`、`RETESTING`、`REJECTED` 或 `UNKNOWN`，并保留
边界、距离、接近方向、触碰次数、source timestamps、volume ratio、flow 质量、当前阶段和
失效原因。只有 context 标记为 confirmed 的周期才允许 `BROKEN`；未收盘周期或影线事实不能
形成确认。

## 突破、跌破和假突破

单快照的候选要求：已确认收盘越过 level 的 ATR 确认缓冲、历史 touch_count 表明曾接近边界，
并且 volume ratio ≥ 1.20 或 expansion percentile ≥ 70。方向不由压缩本身决定。

`compare` 最多读取两个 context。前一快照为候选，后一确认快照继续处于边界外，才输出
`BREAKOUT_CONFIRMED` 或 `BREAKDOWN_CONFIRMED` 并记录 confirmation timestamp。若后一确认
快照重新进入原区间，则输出 `FAILED_BREAKOUT_CANDIDATE` 或
`FAILED_BREAKDOWN_CANDIDATE`、reclaim timestamp 和相应状态转换。单根影线不能构成假突破。

## 压缩、扩张和动量

compression percentile ≥ 70 产生 `VOLATILITY_COMPRESSION`。expansion percentile ≥ 70
产生 `VOLATILITY_EXPANSION`；同时 volume ratio ≥ 1.20 时产生
`COMPRESSION_RELEASE_CANDIDATE`。扩张时 persistence 绝对值 < 0.15 只产生衰竭候选。

RSI、Stoch RSI K/D、price momentum 和 persistence 形成 `OVERSOLD`、`OVERBOUGHT`、
`RECOVERING_FROM_OVERSOLD`、`ROLLING_OVER_FROM_OVERBOUGHT`、`NEUTRAL` 或
`UNAVAILABLE`。这些始终是带周期的 overlay；超买不等于顶部，超卖不等于底部，15m 动量
不能覆盖 4H/1D 结构。

## 跨周期对齐与总状态优先级

对齐状态为 `ALIGNED_UP`、`ALIGNED_DOWN`、`HIGHER_UP_LOWER_PULLBACK`、
`HIGHER_DOWN_LOWER_BOUNCE`、`HIGHER_MIXED_LOWER_UP`、
`HIGHER_MIXED_LOWER_DOWN`、`CONFLICTED` 或 `INSUFFICIENT_DATA`。结果列出支持、冲突和
缺失周期，并区分正常回调和反趋势低周期移动。

合成优先级：数据质量门禁 → 1D/4H 结构 → 4H 波动环境 → 1H setup → 15m trigger →
flow 确认/冲突。关键大级别 level test 和已发展的 boundary sequence 在方向合成前显式保留。
例如 1D/4H 上行而 1H 下行时得到 `HTF_UPTREND_PULLBACK`，不会被合成为下行趋势。

## 数据质量降级

- execution timeframe 缺失或未确认：整体 `INSUFFICIENT_DATA`。
- 1D 和 4H 都缺失：不输出高周期趋势。
- 周线缺失：记录 limitation，但不阻断 15m/1H/4H。
- CVD/OI 缺失：价格结构仍可用，输出 `FLOW_UNAVAILABLE`。
- partial flow 不提供强确认；stale flow 被排除并输出 `FLOW_STALE`。
- 任一 source/candle/window timestamp 晚于 `as_of`：拒绝请求。
- 所有缺失、过期、部分、gap 和预热不足信息都进入 quality、evidence 或 limitations。

## Evidence strength 和解释证据

`evidence_strength` 只衡量当前证据的一致程度和完整程度。计算由方向证据一致率（55%）和
可用证据完整率（45%）组成，范围 0–100。它不是成功率、方向概率或任何结果预测。

每条 `StateEvidenceV2` 带 code、timeframe、value、weight、source timestamp、quality 和
`supporting/conflicting/unavailable` 分类。正反证和不可用证据同时返回；flow partial/stale 不会
伪装成强支持。

## 状态转换

`StateTransitionV2` 记录 from/to、transition timestamp、trigger evidence、source candle
timestamps、confirmation status 和 invalidation reason。compare 支持趋势到相反 transition、
range 到发展中的边界突破、候选到趋势或失败候选、回调到恢复、关键支撑/压力测试到结构越界、
压缩到扩张。相同两个已确认 context 重复计算，序列和 JSON 身份一致。

## API、性能和隔离

- `GET /api/market/state?instrument=...&as_of=...&execution_timeframe=15m`
- `GET /api/market/state/compare?instrument=...&previous_as_of=...&current_as_of=...`

state endpoint 读取一次 Context V2 后执行纯函数。compare 恰好读取两个有界 context；不读取
无界历史。纯状态计算性能目标低于 50ms，完整 API 目标低于 600ms。引擎没有 reader 或数据库
依赖，因此不会产生额外 raw 查询或 N+1。

旧 `market_regime.py`、`evaluate_decision`、策略版本、参数、risk、Paper、collector、实时聚合、
CVD/OI 计算和 AI brief 均保持隔离。开发页通过现有路由懒加载，不替换首页 AI 简报。

下一阶段的策略路由只能显式读取版本匹配的 snapshot code、周期状态、quality、overlays 和
limitations，并在独立策略层定义消费规则；本阶段状态值本身不携带执行动作或风控参数。
