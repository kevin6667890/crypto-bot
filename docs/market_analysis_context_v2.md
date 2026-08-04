# Market Analysis Context V2

`MarketAnalysisContextV2` 是独立、只读、因果的市场事实层。版本为
`market-analysis-context-v2`，指标注册表版本为
`market-indicator-registry-v2`。它不调用策略、评分、风控或订单代码，也不输出方向、
策略名称、入场、止损、止盈或盈利概率。

## 确认 K 线与时间戳

支持 `15m`、`1H`、`4H`、`1D`、`1W`。所有对外时间戳均为 Unix 秒；微观结构库的
Unix 毫秒只在读取边界转换。K 线仅当 `confirmed=true` 且
`candle_close_ts <= as_of` 时可用。`ts` 表示 UTC 开盘时刻，
`candle_close_ts = ts + timeframe_seconds`。读取高周期时使用同一个 `as_of`，所以正在
形成的高周期 K 线不会进入计算。每个指标的 `source_timestamp` 是产生该值的已确认
K 线收盘时间；已确认 swing 的时间戳是右侧两根 K 线完成、该 pivot 得到确认的时间。

## 指标定义

统一入口复用现有 `discovery_features.build_features` 的 EMA、SMA、RSI、Wilder ATR、
Bollinger 和 volume ratio 实现，并在同一注册表中派生其余字段，不建立第二套基础算法。

- 趋势：EMA20、MA60、MA200；4 根已确认 K 线跨度的百分比 slope；收盘价到均线的有符号
  百分比距离；均线排列；2x2 已确认 fractal swing；不含当前 K 线的过去 20 根 rolling
  high/low 距离。
- 动量：RSI14、Stoch RSI、14 根价格动量百分比、14 根收益正负计数差除以 14 的
  persistence。
- 波动：ATR14、`ATR / close * 100`、20 根总体标准差的 Bollinger(2σ)、带宽
  `(upper-lower)/mid*100`、20 根对数收益总体标准差乘 `sqrt(20)*100`、最近最多 100 个
  带宽样本的 expansion percentile 与其补数 compression percentile。
- 成交：当前 volume、前 20 根（不含当前）均量、二者之比；body、upper wick、lower
  wick 各自占整根 high-low range 的百分比。零 range 返回 `null`。
- 微观结构：CVD current/change/slope，OI current/absolute/percentage change，settled 与
  predicted funding，basis value/percentage，以及复用现有 `ohlcv_uniform_range_v1` 的
  VPVR POC/VAH/VAL。

缺少数据、预热不足或分母为零时返回 `value=null`、`available=false`，绝不补零。

## Stoch RSI

版本：`stoch-rsi-v2-rsi14-stoch14-k3-d3`。默认参数为 RSI period 14、Stoch lookback
14、K smoothing 3、D smoothing 3：

```text
raw_t = 100 * (RSI_t - min(RSI[t-13:t])) /
              (max(RSI[t-13:t]) - min(RSI[t-13:t]))
K_t   = SMA(raw, 3)
D_t   = SMA(K, 3)
```

窗口只含当前及更早的已确认 K 线。任何窗口预热不足或含 `null` 均返回 `null`；RSI 窗口
最大值等于最小值时分母未定义，raw、K、D 按依赖关系返回 `null`，不产生 NaN，也不做
买卖解释。

## UTC 周线

版本：`utc-monday-weekly-v1`。只从已确认 1D K 线聚合，周起点固定为星期一
00:00:00 UTC，周终点为下一个星期一 00:00:00 UTC。必须恰好存在周一至周日七根连续
日线且周终点 `<= as_of` 才生成 `confirmed=true` 的周线。未结束周不会作为周 K 线
返回，浏览器或服务器本地时区不会改变结果。

## 数据质量

每个 `IndicatorValueV2` 包含 `value`、`source_timestamp`、`available`、`stale`、
`partial`、`warmup_complete` 和 `calculation_version`。周期与整体质量状态为：

- `AVAILABLE`：所选确认数据可用且未发现缺口；
- `STALE`：最新源时间超过对应周期两倍，flow 超过 180 秒；
- `PARTIAL`：覆盖不完整或范围内有 gap；
- `MISSING`：没有可用确认数据。

相邻 K 线开盘时间超过一个周期会形成 `gaps`，并传播到指标、周期和整体质量。CVD、OI、
Funding、Basis、VPVR 分别保留自己的 source timestamp 和状态。

## 关键位置与 confluence

候选位置包括各周期已确认 swing high/low、EMA20、MA60、MA200、Bollinger upper/lower、
VPVR POC/VAH/VAL，以及基于当前数量级确定的相邻整数心理关口。touch 定义为最近最多
100 根确认 K 线的 high-low 范围（外加固定 `0.10%` 容差）覆盖该位置。

confluence 版本为 `market-level-confluence-v2`。候选按价格、类型、周期稳定排序；与当前
zone 算术中心距离不超过固定 `0.25%` 时合并，zone 值为成员算术均值，来源去重排序。
算法只报告重合来源、距离和 touches，不报告“强支撑”或价格必然行为。

## 价格、OI 与 CVD 组合事实

默认窗口为 4 根 15m 等价的 3600 秒。价格与 OI 或 CVD 的变化符号只映射为
`PRICE_UP_OI_UP` 等八种事实状态；任一输入不可用即为 `INSUFFICIENT_DATA`。结果持久化
观察窗口、起止时间、各变化和质量。它不推断新增多头/空头、回补或清算身份。

## API 与性能边界

`GET /api/market/context?instrument=ETH-USDT-SWAP&as_of=<unix-seconds>&execution_timeframe=15m`
只读现有 `historical_candles` / `market_candles` 与微观结构聚合。每个 SQL 均包含
instrument、开始/结束时间和 `LIMIT`；默认 K 线窗口为 15m/1H/4H 各 512 根、1D 1500
根（用于生成最多约 214 根确认周线），flow 为一小时、funding 为七天。查询不执行
COUNT/MIN/MAX，不加载全历史，不回填、不 maintenance、不写数据库。缓存 TTL 5 秒，键
包含 instrument、精确 as_of、execution timeframe 和 context 版本。相关复合索引与
`EXPLAIN QUERY PLAN` 测试保证关键查询不做全表扫描；fixture 目标小于 500ms。

## 与旧策略隔离及下一阶段

`strategy_rules`、`decision_engine`、`LIVE_STRATEGY_VERSION`、paper decision、collector、
实时聚合、CVD UTC reset、OI 连续性、订单与保留策略均不接入本对象。下一阶段的市场状态
引擎应只消费本对象的可用事实及质量字段，先按 source timestamp 做 as-of 校验，再将状态
分类与策略路由作为独立、版本化的解释层实现。
