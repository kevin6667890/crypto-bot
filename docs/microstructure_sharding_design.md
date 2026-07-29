# 微观结构 SQLite UTC 月分片设计与本地原型

## 结论

推荐 C：热库 + 只读冷月分片 + 中心库。当前月 raw 写入
`market_microstructure_YYYY_MM.db`；已完成月份 checkpoint 后以 immutable
方式只读；aggregates、gaps、coverage summary、checkpoints、manifest 状态放在
中心库。迟到数据写入中心 late-arrival overlay，查询时与目标冷月合并；冷库
本体永不原地修改。

本轮只实现小型本地原型，没有修改现有生产 schema，没有迁移备份数据，也没有
连接生产。原型位于 `dashboard/microstructure_sharding.py`，验证位于
`tests/test_microstructure_sharding.py`。

## 三种方案比较

| 维度 | A 单 SQLite 持续增长 | B 每月独立 SQLite | C 热库 + 冷分片（推荐） |
|---|---|---|---|
| 写入 | 最简单，单 WAL | 每月切换 writer | 当前月单 writer |
| raw | 单文件 | 每月独立 | 当前月 hot、历史 cold |
| aggregates / summary / gaps / checkpoints | 同一大库 | 容易重复或难以确定归属 | 中心库统一持有 |
| 短范围查询 | 最少协调开销 | 单月快 | 单月快 |
| 跨月查询 | SQL 最简单 | 应用层合并 | 应用层合并，中心语义稳定 |
| 备份 / 恢复 | 文件越大，窗口和失败域越大 | 可按月恢复 | cold 增量备份，hot + center 高频备份 |
| 迟到数据 | 直接写 | 要重开历史月 | 写 overlay，定期生成替换冷片 |
| 故障域 | 全历史一个文件 | 单月 | 单月 raw；中心库需独立保护 |
| 文件数量 | 最少 | 月数 | 月数 + center；查询逐片打开，峰值可控 |
| 实现复杂度 | 低 | 中 | 中高 |
| 适用性 | 小库、低增长 | raw 完全独立 | raw 高增长且汇总/状态需要连续 |

B 的主要问题不是月文件本身，而是 aggregates、coverage、gaps 和 checkpoints
如果也随月复制，会出现跨月状态归属不清；如果另外再造中心库，实际上就演化为
C。

## 离机备份证据与方法

统计来源仅为：

`offline-backup/market_microstructure.db`

- manifest 创建时间：2026-07-28T04:07:32Z。
- 文件大小：2,416,603,136 bytes（2.251 GiB）。
- page size / page count / freelist：4,096 / 589,991 / 0。
- manifest 与本地实算 SHA-256 均为
  `09c96e7a15fa9d2a55e0a394a9780242190a7229d8549d289a416a2f621965ca`。
- `PRAGMA quick_check`：`ok`。
- 所有 SQL 连接均使用 `file:...?mode=ro&immutable=1`。
- 表行数优先读取库内维护的 `table_row_counts`；source、时间覆盖和最近日增长
  才读取相应离机表/索引。未连接任何生产部署目录或生产地址。
- 当前 Python SQLite 未编译 `dbstat`，空间统计通过只读解析 sqlite_master
  root page、B-tree child page 和 overflow page 得出。589,991 个文件页中
  589,990 个被归属；剩余 1 页是 SQLite lock-byte page，不是遗漏对象。

备份内 schema 来自 `production_sha=40aa74e...`，只用于容量画像；本分支代码
基线是 `bc7da264...`。两者用途不同，未把备份 schema 写回当前代码。

## 表与索引空间

下表的“索引”包含显式时间索引及主键自动索引；MiB 为 1,048,576 bytes。

| 数据 | rows | table MiB | index MiB | 合计 bytes/row |
|---|---:|---:|---:|---:|
| trades | 5,926,133 | 1,377.04 | 685.15 | 364.89 |
| OI | 73,559 | 18.03 | 8.14 | 373.02 |
| settled funding | 882 | 0.21 | 0.07 | 334.37 |
| predicted funding | 6,708 | 1.64 | 0.53 | 340.11 |
| mark | 282,826 | 64.57 | 38.55 | 382.31 |
| index | 170,236 | 36.77 | 20.72 | 354.15 |
| liquidations | 1,013 | 0.34 | 0.08 | 432.65 |
| CVD aggregates | 18,626 | 2.07 | 0.63 | 152.18 |
| OI aggregates | 26,003 | 3.33 | 0.89 | 170.12 |
| basis aggregates | 213,807 | 27.25 | 7.46 | 170.23 |

上述十项表 B-tree 合计 1,605,623,808 bytes，相关索引合计
799,260,672 bytes。剩余约 11.2 MiB 是 feature snapshots、event study、
research manifests、gaps/checkpoints 和 schema 元数据等。trades 表加索引为
2,162,360,320 bytes，占全库约 89.48%。

最重 source：

| trade source | rows | trade 占比 |
|---|---:|---:|
| OKX WS trades-all | 4,190,730 | 70.72% |
| OKX history-trades REST | 1,396,800 | 23.57% |
| migrated genuine OKX trades | 338,603 | 5.71% |

因此容量治理首先应处理 trade raw；仅拆 funding 或 liquidation 几乎不改变
总体风险。

## 日增长与 30/90/180 天容量

离机库的最近七个完整 UTC 日窗口存在两个无数据日、部分日以及历史回填突发，
不能把七日简单平均称为稳定生产速率。容量预算采用最近完整活跃日
（2026-07-27 UTC）的保守高水位：

| 数据 | rows/day | 估算 bytes/day |
|---|---:|---:|
| trades | 3,241,237 | 1,182,680,557 |
| OI | 10,040 | 3,745,138 |
| settled funding | 9 | 3,009 |
| predicted funding | 2,829 | 962,177 |
| mark | 10,040 | 3,838,357 |
| index | 10,040 | 3,555,663 |
| liquidations | 737 | 318,861 |
| CVD aggregates | 3,133 | 476,768 |
| OI aggregates | 3,673 | 624,858 |
| basis aggregates | 3,674 | 625,437 |
| **合计** | | **1,196,830,825（1.197 GB/day）** |

这是按备份中各对象实际占用 bytes/row 线性估算，包含表与索引，未假设压缩。
trade 的六个有数据日均值约 907k rows/day，明显低于 7 月 27 日高水位；在收集
断档和回填模式稳定前，应以高水位做磁盘告警，以有数据日均值做中位预算，两者
之间保留容量区间。

| 期限 | 新增（高水位） | 含当前 2.416 GB 的总量 | cold 月文件量级 |
|---|---:|---:|---:|
| 30 天 | 35.90 GB | 38.32 GB | raw 约 35.85 GB/月 |
| 90 天 | 107.71 GB | 110.13 GB | 约 3 个 cold/hot 月 |
| 180 天 | 215.43 GB | 217.85 GB | 约 6 个 cold/hot 月 |

实际 retention 会删除或归档 raw 时，总在线容量可低于线性 180 日值。中心
aggregates 的高水位约 1.73 MB/day，不应跟随 raw 月片重复。

## 推荐目录

```text
microstructure/
  shard_manifest.json
  central/
    market_microstructure_center.db
  shards/
    2026/
      07/
        market_microstructure_2026_07.db   # hot
      06/
        market_microstructure_2026_06.db   # cold, immutable
  backups/
    manifests/
```

中心库保存：

- `cvd_aggregates`、`oi_aggregates`、`basis_aggregates`；
- `collection_gaps`、`coverage_summary`、`collection_checkpoints`；
- late-arrival overlay 和 shard transition journal；
- schema/feature/source version 与校验结果。

月片保存 raw：

- trades、OI、mark、index；
- settled / predicted funding；
- liquidations。

原型用与上述七类一一对应的物理表，payload 保持 JSON，以最小 fixture 验证路由
而不复制生产数 GB schema/data。生产实现应沿用现有 typed columns，不应把
原型 JSON payload 当作生产 schema 提案。

## Manifest 与不变量

JSON manifest 含 manifest/schema version、generation、唯一 hot month、中心库
路径，以及每月 `{month,path,state}`。校验包括：

- canonical `YYYY_MM` 和 canonical 相对路径；
- checksum、唯一月份、唯一 hot、hot 必须为最新月；
- 月份连续，无静默空洞；
- 文件存在、`PRAGMA user_version` 和七张 raw 表存在；
- 路径 resolve 后不能逃出根目录。

manifest 使用同目录临时文件，`flush + fsync + os.replace` 原子发布。查询的逻辑
计划以 `(manifest generation, table, UTC month tuple)` 缓存；切月后清空。
SQLite prepared statement cache仍由连接自身管理，原型没有把 connection 长期
常驻。

## 写入路由、月切换和迟到数据

### 写入

1. ingestion boundary 把 epoch 明确规范为毫秒。
2. `datetime.fromtimestamp(ts/1000, timezone.utc)` 计算 `YYYY_MM`，不读本地
   timezone。
3. index instrument 去掉 `-SWAP`；其他 raw 使用 canonical swap instrument。
4. hot month 执行 `INSERT OR IGNORE`；semantic uniqueness key 保证重放幂等。
5. 未注册月份明确抛 `MissingShardError`，禁止偷偷写当前库。

### 月切换

切换仅允许 `next_month(current_hot)`：

1. 中心 transition journal 写 `PREPARING`。
2. 暂停 writer、提交当前 batch。
3. 对旧 hot 执行 `wal_checkpoint(TRUNCATE)`；busy 则终止切换。
4. 创建下一月文件、schema、索引和 schema version。
5. 原子发布新 generation：旧月 `cold`，新月 `hot`。
6. journal 写 `COMPLETE`，恢复 writer。

checkpoint 是必要条件：`immutable=1` 按设计忽略 WAL；未 checkpoint 就封存会
让 cold reader 看不到 WAL 中已提交行。故障恢复读取 transition journal：
manifest 未发布则删除/隔离空的新片并重试；manifest 已发布则验证两片后补记
`COMPLETE`。所有判断只依赖 manifest 和 UTC，不依赖机器时区。

### 迟到数据

不直接重开 cold 文件。先用 cold 的 primary key 只读查重；新 row 写中心
`late_arrivals(table,target_month,...)`。查询 overlay 优先于同 key base。
维护窗口可把 base + overlay 生成新的临时冷片，校验后通过 manifest generation
和原子 rename 替换；旧片保留到备份确认，绝不原地改写。overlay 超过单月 raw
的 0.5% 或查询放大明显时触发 compaction。

## 跨月查询语义

范围统一为半开区间 `[start_ms,end_ms)`：

1. 列出所有相交 UTC 月；任一月未注册或文件缺失即明确失败。
2. cold 用 `mode=ro&immutable=1`，hot 用 `mode=ro`。
3. 每片执行相同 instrument/source/timestamp 条件；cold 同时查询目标月 overlay。
4. 以 uniqueness key 去重，overlay 可覆盖 base。
5. 全局按 `(timestamp_ms, uniqueness_key)` 排序。
6. cursor 包含 version、query fingerprint、最后 timestamp/key；换过滤条件复用
   cursor 会报错。
7. 每片最多取 `page_size + 1`，合并后产生下一 cursor；分页结果与整段查询一致。

原型逐片顺序打开并立即关闭，查询时同时打开的 shard 文件峰值为 1；加中心
overlay 查询时仍不需要 `ATTACH` 所有月份。服务进程的常驻文件数是 hot writer
及中心库各自的 db/WAL/SHM，加当前短读 1 个 cold db；若改成连接池，必须设置
LRU 上限，不能按历史月数无限增长。

索引按查询形态分别建立，避免可选前缀破坏 timestamp range：

- `(timestamp_ms, uniqueness_key)`；
- `(instrument, timestamp_ms, uniqueness_key)`；
- `(source, timestamp_ms, uniqueness_key)`；
- 中心 overlay 在 `(table_name,target_month,...)` 后建立对应三种 range 索引。

生产 typed schema 可酌情增加 `(instrument,source,timestamp,key)`，但不能用它
替代无 instrument/source 的时间索引。索引增加会扩大写放大；应以实际查询日志
决定是否保留 source-only 索引。

## CVD、OI、funding 和 instrument 语义

- CVD 从严格排序 trades 的真实 side/notional 得出；UTC day 用
  `timestamp_ms // 86_400_000`，每日首条前累计归零。跨月午夜也执行同一规则。
- OI 是绝对值序列，不在 shard 边界归零、不前向填补缺口。
- settled funding 按实际 funding event timestamp 查询；predicted funding 与
  settled 分表，不把 provisional 值当作已结算事件。
- index `BTC-USDT` 与 swap `BTC-USDT-SWAP` 在入口规范化映射；存储仍保留各自
  正确 canonical instrument。
- gaps 仍是数据，不用零值补齐。中心 coverage summary 按 base + overlay 的
  min/max/count 更新，并记录生成时的 manifest generation。

专项 fixture 已验证：UTC 路由、跨月严格排序、同 timestamp 的确定性 tie-break、
分页无重复/遗漏、instrument/source filter、缺片报错、manifest 校验、UTC 午夜
CVD 从 `[10,7]` 重置为 `[-2,3]`、OI `[100,105]` 连续、funding event、index/swap
映射、迟到 overlay、hot/cold 边界和重复写幂等。

## 本地性能

### 离机单库只读扫描

在本机、immutable、OS cache 状态未强制清空的条件下：

- 5.93M trades 按 instrument 做 count/min/max：13.05 s；
- trades 按 source 聚合：35.79 s；
- 最近七日 trades 分日计数：23.05 s；
- 小表的同类查询通常为 8 ms 到 1.8 s。

这说明当前最大成本来自 trade B-tree，而不是 funding/liquidation。

### 小型模拟

使用 6 个 UTC 月、每月 5,000 trades、总计 30,000 rows 的临时 fixture；单库和
月片具有 timestamp range index，读取并 JSON decode 全结果：

| 查询 | 单库 | 6 月分片 |
|---|---:|---:|
| 6 月首次整段 | 824 ms | 1,383 ms |
| 6 月随后整段 | 729 ms | 1,476 ms |
| 单月 warm | 87 ms | 103 ms |
| 6 月 cursor 分页（30 × 1,000） | — | 6,700 ms |

这是 Python 原型开关连接和对象构造的端到端时间，不是生产吞吐承诺。跨 6 月
整段约有 2 倍协调开销，单月接近单库。逻辑 query-plan cache 在该运行中
2 misses / 31 hits；它消除了重复 manifest/path 规划，但相对文件打开、SQL 和
JSON decode 的影响很小。OS cache 让单库第二次快约 12%；分片结果在本次运行
落入噪声范围，不能据此宣称 cache 加速。

生产优化顺序：

1. API 默认限制查询月份和 page size；
2. 每片 SQL limit pushdown 与 k-way merge；
3. 仅对最近 1–3 月设置有界 read-connection LRU；
4. aggregates 服务长范围图表，避免扫 180 天 raw；
5. 用真实 typed row 重新 benchmark，记录 p50/p95、cold/warm cache 和并发 reader。

## 备份、恢复与校验

### 备份

- cold：封存时 `quick_check`、row count/min/max、文件 SHA-256；内容不再变，
  只上传一次并保留 manifest generation。
- hot：使用 SQLite online backup API，或短暂停 writer 后 checkpoint 再复制；
  不能只复制 `.db` 而遗漏有效 WAL。
- center：与 hot 一起做一致性备份；其中含 checkpoint、overlay 和 manifest
  transition 状态。
- 备份 catalog 记录 center/hot/cold hashes、schema versions、UTC range 和
  generation。上传成功前不删除旧文件。

### 恢复

1. 先恢复到新目录，绝不覆盖唯一副本。
2. 校验所有 SHA、`quick_check`、schema version 和 canonical filename。
3. 恢复 center，再恢复 manifest 引用的 cold/hot。
4. 对每月/每表校验 count、min/max、uniqueness key 重复数；校验相邻月
   `[month_start,next_month_start)` 无 overlap/omission。
5. overlay 与 base 合并校验；恢复 checkpoints 不得超过已恢复 raw high-water。
6. shadow query 对照后再原子切换配置路径。

## 灰度迁移、停机与双写

本轮不执行迁移。建议阶段：

1. **观测**：先稳定 trade 收集断档/回填模式，记录 14–30 个完整 UTC 日。
2. **离线构建**：从一致性 snapshot 在独立目录按 timestamp bounded batch 写
   月片；aggregates/gaps/summary/checkpoints 写 center。旧库保持权威。
3. **校验**：逐表/月 count、min/max、key hash 分桶、随机 range、CVD/OI/funding
   语义和缺口清单。
4. **shadow read**：线上请求仍返回旧库结果，旁路抽样比较新路由，不影响策略/
   订单。
5. **tail catch-up**：优先用暂停前后的 collector durable queue/事件日志重放，
   避免长期双写。
6. **cutover**：UTC 月边界暂停 collector，提交 batch、checkpoint、重放 tail、
   复核 high-water，发布 manifest/config，恢复 writer。
7. **观察**：旧单库只读保留至少一个完整月和两个成功备份周期。

基于备份传输记录，2.416 GB 复制耗时约 176.7 s（约 13.7 MB/s）；只读 hash +
quick check 本机约 77 s。按分片 INSERT、索引构建和双份校验 3–6 倍 I/O 放大，
全停机迁移保守估计 15–30 分钟。采用预构建 + tail catch-up 时，目标业务停写
窗口为 2–5 分钟；必须在 staging 用同磁盘、真实 row 宽复测后才能承诺。

双写风险包括：

- 旧库成功、新 hot 失败形成分叉；
- 月边界两个 writer 对 UTC month 认识不一致；
- 两边 uniqueness/version 不一致；
- 重试顺序改变 checkpoint，造成“数据未落盘但 cursor 已前进”；
- 双倍 WAL/索引 I/O 反过来扩大采集延迟。

如果必须双写，使用同一个 normalized event envelope 和稳定 uniqueness key，
checkpoint 只在两边确认后推进，记录逐边状态并支持幂等补偿；不能用跨两个
SQLite 文件的“伪事务”宣称原子性。更安全的是短暂停写 + durable tail replay。

## 回滚

- cutover 前旧单库不修改、不删除。
- writer 配置保留一键回旧库，但回滚前先暂停新 writer。
- 把新 hot/overlay 在 cutover 后接收的 events 按稳定 key 导出为 bounded tail，
  幂等 replay 到旧库；校验 count/high-water 后切读。
- 回滚不把新中心 checkpoint 直接覆盖旧 checkpoint；checkpoint 必须由旧库已
  验证 high-water 重建。
- 新目录整体隔离并保留供审计；不要在失败现场原地“修”cold。
- 若仅 query router 故障，可先让长范围查询退回中心 aggregates，raw 写入 hot
  不变，降低回滚面。

## 何时值得实施

满足以下任一项应进入生产化验证：

- 30–90 天容量超过单机备份/恢复窗口或磁盘告警预算；
- WAL checkpoint、vacuum、quick_check 已明显影响 collector；
- 大多数 raw 查询集中于当前月/最近 1–3 月；
- 需要按月不可变备份、局部恢复或归档；
- 当前高水位约 1.2 GB/day 持续一至两周。

当前备份中 trades 已占约 89.5%，且保守线性 90 日约 110 GB，架构方向值得
实施；但应先解释 7 月 27 日突增是稳定 live rate、回填，还是重复采集，避免用
分片掩盖 ingestion 缺陷。

## 何时不应该实施

- 库长期只有几 GB、增长低且现有备份/恢复 SLA 充足；
- 主要请求必须频繁扫描 90–180 天 raw，且 aggregates 无法替代；
- 团队没有 manifest、cold hash、overlay compaction 和恢复演练能力；
- 数据唯一性/时间单位尚未稳定；
- 期望分片自动修复缺口、重复或错误 CVD 语义；
- 只是为了绕过一个缺失索引或未解决的采集风暴。

分片缩小故障域和维护窗口，但不会降低 raw 总写入量，也不会替代 retention、
聚合、正确索引和采集质量治理。
