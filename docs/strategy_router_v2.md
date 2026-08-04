# Strategy Router V2

`StrategyRouterV2` 是只读、确定性、因果的研究策略路由层。总版本为
`strategy-router-v2`，定义版本为 `strategy-family-definitions-v2.1`。唯一输入是已经生成的
`MarketAnalysisContextV2` 和 `MarketStateSnapshotV2`，可选一个前序 route 用于恢复研究生命周期。

> 研究策略路由，不是实时交易建议，当前未连接Paper或实盘执行。

路由层不查询数据库、raw trades 或 raw OI，不调用 LLM、`evaluate_decision`、Paper scheduler、
risk engine 或任何订单函数。GET API 只有上游 V2 context service 执行有界读取；router 自身是纯计算。

## 周期职责

| 周期 | 职责 |
|---|---|
| 1W | 长期风险和方向背景，不触发 |
| 1D | 中期结构和方向冲突检查 |
| 4H | 策略环境、MA200 和关键位 |
| 1H | setup、回调、反弹、区间与 retest |
| 15m | 已确认的触发确认；默认执行周期 |

所有 source timestamp 必须不晚于 `as_of`，否则整个 route 被拒绝。当前版本只接受 15m 执行周期，
且只使用 confirmed candles。

## 五类路由结果

四个可研究策略族分别为 `TREND_PULLBACK`（`trend-pullback-v2`）、
`MA200_MEAN_REVERSION`（`ma200-mean-reversion-v2`）、
`BREAKOUT_CONTINUATION`（`breakout-continuation-v2`）和
`FAILED_BREAKOUT_REVERSAL`（`failed-breakout-reversal-v2`）。每族分别定义 LONG 与 SHORT，
不是由符号取反生成。`NO_TRADE` 使用独立的 `no-trade-policy-v2`，是结构化一等结果。

### 趋势回踩

LONG 要求 1D/4H 上升结构、4H 不为 `TREND_DOWN`、MA200 slope 非明显向下，1H 为正常回调，
并从上方进入 1H EMA20/MA60、4H EMA20/MA60 或突破后 retest zone。高周期压力测试阻止追多。
触碰只到 ARMED；15m confirmed reclaim、动量恢复且无反向 flow 冲突才可 `TRIGGER_READY`。
1H 结构低点失守、4H 转空或 confirmed breakdown 使其失效。

SHORT 独立要求下降环境、4H 不为 `TREND_UP`、MA200 slope 非明显向上，1H 是反弹/transition up，
并从下方测试动态压力或前跌破位。大级别支撑正上方阻止追空。触发是 15m confirmed rejection、
反弹结构失败和动量 rolling over；4H 转多、breakout 或反弹结构突破使其失效。

### MA200 均值回归

只允许 4H/1H MA200，15m MA200 不能独立建立 setup。LONG 必须从上方测试平坦/上升 MA200，
并具有 swing low、rolling low、VPVR VAL/POC 或前突破区等独立 confluence。WATCH 表示接近，
触碰且低周期降温为 ARMED。只有 confirmed reclaim，加上拒绝结构和动量从超卖恢复，才允许
`TRIGGER_READY`。所以“触碰 MA200”或“超卖”均不是买入理由，单根插针也不能确认。

SHORT 从下方测试平坦/下降 MA200，要求 swing high、VPVR VAH 或前跌破区 confluence，
confirmed rejection 与动量从超买回落。强 `TREND_UP` 加速时不能只因碰线做空。Flow 缺失降低完整度，
不伪造成强证据；confirmed MA200 break、结构极值失守或强反向趋势使 setup 失效。

### 突破延续

完整时序是 `RANGE_OR_COMPRESSION → BREAKOUT_CANDIDATE → BREAKOUT_CONFIRMED → RETEST →
CONTINUATION_TRIGGER`。首次越界绝不触发。LONG 使用已确认上边界、两次确认保持边界外、回踩不回到
原区间和 15m 再转强；SHORT 对下边界、breakdown、反抽和再次转弱做独立检查。反向关键位太近、
重新进入原区间、失败突破事件或等待过期会阻断/失效。

### 假突破反转

必须先有真实 breakout/breakdown candidate 或 confirmed 事件，再 confirmed re-entry 到原区间，最后才检查
低周期反向结构。上方失败对应 SHORT：lower high/局部下破和超买回落；下方失败对应 LONG：higher low/
局部上破和超卖恢复。单根长影线或没有前序突破事件均不能创建该策略。重新越过失败极值使其失效。

## Flow 规则

CVD、OI、Funding、Basis 只可作确认、冲突、降级和上下文。路由只表达确定性的
`PRICE_UP_OI_UP / PRICE_UP_OI_DOWN / PRICE_DOWN_OI_UP / PRICE_DOWN_OI_DOWN` 与
`CVD_CONFIRMING_PRICE / CVD_DIVERGING_PRICE`，不推断新增多空、回补或清算。stale flow 完全不计分，
partial flow 仅是弱证据，missing flow 不阻止价格策略但降低完整度。

## 评分和 evidence strength

完成度固定拆分 Environment 25、Structure 25、Setup 20、Trigger 20、Data quality 10。
`TRIGGER_READY` 要达到版本化总分阈值、Trigger 最低分、所有 blocking gate 和有效 geometry。
score 只表示规则完成程度。`evidence_strength` 是可用证据的一致比例与条件完整度的确定性组合。
二者都不是预期收益、胜率或盈利概率。

## Geometry 与最小结构 R

`StrategyGeometryV2` 包含 setup zone、trigger boundary、confirmation rule、invalidation reference、
stop/target reference types、最大等待/持有 bars、minimum structural R、entry timing，以及 Phase 4 才定义的
intrabar/gap policy placeholders。允许的 stop reference 是 confirmed swing、MA zone opposite boundary、
breakout/retest boundary、ATR buffer 和 failed breakout extreme；target 使用 opposite range boundary、prior swing、
VPVR POC/VAH/VAL、next confirmed support/resistance，fixed R 只作基准实验。

结构空间以 trigger-to-invalidation 为风险距离，trigger-to-nearest-opposing-level 为可用空间。
默认最小结构 R 为 1.25。风险距离非正、对向位置太近、两个位置中间或缺少 target-side structure 时，
geometry 为 invalid，并返回 `INVALID_GEOMETRY`/`NO_TRADE`；不会为了产生候选强行使用固定 ATR。

## 生命周期与身份

统一状态为 `INELIGIBLE / WATCH / ARMED / TRIGGER_READY / TRIGGERED_RESEARCH_ONLY /
INVALIDATED / EXPIRED / COOLDOWN_RESEARCH_ONLY`。`StrategyLifecycleV2` 是纯 reducer，按当前 confirmed time、
candidate 和可恢复的前序 candidate 推进；同 setup 从 TRIGGER_READY 只记录一次研究 trigger，再进入 cooldown，
过期后必须形成新 setup。状态不跨 instrument、family 或 direction 共享。

每个 candidate 有 family/setup/evaluation 三层 SHA-256 identity。输入覆盖 family、direction、独立 strategy version、
definitions/parameter versions、完整配置 hash、instrument、四类周期职责、source candle timestamps、level identity、
setup start 和 trigger timestamp。不同方向、族、MA200 周期或突破边界不会共享 identity；新 identity 不含旧
`LIVE_STRATEGY_VERSION`。相同输入重复计算完全一致。

## NO_TRADE 政策

原因码包括 `INSUFFICIENT_DATA`、`STALE_EXECUTION_DATA`、`HTF_CONFLICT`、`MID_RANGE_NOISE`、
`NO_STRUCTURAL_LEVEL`、`NO_CONFIRMATION`、`TOO_CLOSE_TO_OPPOSING_LEVEL`、`VOLATILITY_TOO_LOW`、
`VOLATILITY_TOO_HIGH`、`EXTENDED_FROM_STRUCTURE`、`BREAKOUT_NOT_CONFIRMED`、
`MA200_TOUCH_WITHOUT_RECLAIM`、`FLOW_CONFLICT`、`SETUP_EXPIRED`、`DUPLICATE_SETUP`、
`INVALID_GEOMETRY` 和 `NO_STRATEGY_MATCH`。每项都返回 timeframe、evidence、source timestamp、是否临时以及
解除条件。不交易是正常策略结果，不是系统故障。

## 候选选择

所有八个 family/direction evaluation 都保留；达到最低适用性的候选进入 alternatives。primary 的事件优先级是
数据/NO_TRADE gate 后的 failed breakout、confirmed breakout retest、关键 MA200、trend pullback、普通 WATCH。
同阶段再比较完成度、完整度、geometry、对向空间、freshness 与稳定 identity。方向冲突的多个
`TRIGGER_READY` 不会强选 primary。任何历史或未来回测绩效都不参与路由排序。

## API 与旧系统隔离

`GET /api/strategy/route` 接受必填 `instrument`，可选 `as_of`、`previous_as_of` 和默认 `15m` 的
`execution_timeframe`。最多比较一个前序状态。开发 fixture 接口
`POST /api/strategy/route/evaluate` 默认关闭，只有显式设置 `ENABLE_STRATEGY_ROUTER_FIXTURE_API` 才可用。
两者都不写库、不回填、不启动回测、不创建订单。

旧 `evaluate_decision`、`StrategyParameters`、`minimum_score`、`LIVE_STRATEGY_VERSION`、Paper scheduler、
collector、risk、CVD/OI 算法和首页 AI 简报保持原样，新 router 不被旧 Paper 调用。

## Phase 4 回测契约

`StrategyBacktestSpecificationV2` 按 family/direction 导出 required timeframes、feature/state versions、
小而有经济意义的 parameter ranges、next-confirmed-open entry timing、stop/exit、maximum wait/hold、rearm、
cost/intrabar/benchmark placeholders、asset/timeframe/data-quality 和 setup identity rules。
每个方向每族 8 个组合；按四个族原始参数网格计 32 个，若将方向分别计为独立规格则共 64 个 evaluation
组合，均低于每族 24 和总计 96 的上限。本阶段只序列化规格，不运行正式回测。

Phase 4 唯一建议：冻结本版本定义后，分别对四个策略族执行因果事件回放，先验证 identity、时序、成本和
intrabar policy，再做开发/验证划分；holdout/OOT 在最终锁定前保持不可见。
