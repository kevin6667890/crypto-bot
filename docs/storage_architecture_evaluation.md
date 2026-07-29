# Crypto-Bot 存储架构评估

评估基线：`675d334273cdb97d366d35ce74670c715114efa8`

验收分支：`agent/real-research-infrastructure-validation`

数据来源：用户提供的仓库外离机备份；仓库和本文不记录机器绝对路径。

备份生成时间：2026-07-28 04:07:32 UTC

## 执行结论

**当前不应直接迁移 PostgreSQL。** 当前 1.6 GB 内存主机不适合同时运行
Crypto-Bot、迁移任务和 PostgreSQL，也不适合在切换期承担 SQLite/PostgreSQL
双写。现在最有效的动作是：

1. 先治理 `paper_trades.db.analysis_snapshots` 的 payload、保留期与重复快照；
2. 将 SQLite 数据目录迁到独立的高耐久 SSD/NVMe 数据盘；
3. 建立文末的容量、I/O、队列、延迟和备份指标；
4. 指标持续越线后，在至少 8 GB 内存的单独主机或升级后的主机上做
   PostgreSQL 影子导入和实测，再决定切换。

PostgreSQL 的主要价值是多个并发 reader、独立 WAL/备份工具、在线维护和更清晰
的写入/查询资源隔离，而不是自动减少容量。按当前数据形态迁移，PostgreSQL
很可能因为 MVCC、WAL 和索引留下更大的磁盘足迹。月分区对范围删除、归档和
partition pruning 有价值，但只有迁移 PostgreSQL 后才值得采用。TimescaleDB
可作为以后压缩/continuous aggregate 的试验项，不应成为上线前置依赖。

## 备份分析方法与边界

- 没有连接生产；没有读取生产 DSN；没有调用策略、交易或订单 API。
- 两份备份均通过 SQLite `mode=ro&immutable=1` 打开，备份文件未写入。
- 2026-07-29 重新计算的 SHA-256 与 manifest 完全一致：
  `market_microstructure.db` 为
  `09c96e7a15fa9d2a55e0a394a9780242190a7229d8549d289a416a2f621965ca`，
  `paper_trades.db` 为
  `7fdbe32b3496e52451f338dd265c1fd3d28a487cf2bef32e2a5ae827ba83b724`。
- 两库在 `query_only=ON` 下重新执行 `quick_check` 均为 `ok`；本机只读耗时分别
  约 59.9 秒和 39.0 秒，文件大小与修改时间保持不变。manifest 记录 checkpoint
  后 WAL 为 0。
- 表/索引空间通过只读解析 SQLite 4 KiB B-tree 和 overflow page 归属计算；
  两库 freelist 均为 0。行数与日分布从备份的时间列/覆盖索引计算。
- 日增字节不是两个连续快照之差，而是“对象实际分配字节/现有行数 × 当日新增
  行数”的物理足迹估算。它适合容量规划，不等同于精确账单。
- 2026-07-25 的新行情 observation 基本为零，显示一次采集中断；成交量也高度
  波动。预测因此是观测基线，不是承诺上限。
- 没有生产全表查询。离机备份上的大表扫描仅用于本评估。

## 当前规模

所有容量均同时给出十进制 GB 口径；操作系统显示的 GiB 会更小。

| 数据库/对象 | 行数 | 表空间 | 索引空间 | 文件大小 |
| --- | ---: | ---: | ---: | ---: |
| `market_microstructure.db` | 主要 raw 6,461,357；聚合 258,436 | 1.615 GB | 0.801 GB | 2.417 GB |
| `paper_trades.db` | 主要 flow raw 2,282,226；另有研究/快照表 | 5.072 GB | 0.213 GB | 5.286 GB |
| 合计 | — | 6.687 GB | 1.015 GB | **7.702 GB（7.173 GiB）** |

两库对象页合计 7,702,044,672 字节；其余 81,920 字节为 SQLite header/schema
等页面。

最大的对象如下：

| 对象 | 行数 | 表 | 相关索引 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| `analysis_snapshots` | 23,391 | 4.111 GB | 无 | **4.111 GB** |
| `trade_flow_observations` | 5,926,133 | 1.444 GB | 0.718 GB | **2.162 GB** |
| `decision_signal_runs` | 75,340 | 0.457 GB | 0.013 GB | 约 0.470 GB |
| `decision_signals` | 36,428 | 0.239 GB | 约 0.007 GB | 约 0.245 GB |
| `decision_evaluations` | 11,895 | 0.114 GB | 约 0.001 GB | 约 0.115 GB |
| `flow_price_buckets` | 1,656,207 | 0.069 GB | 0.130 GB | 0.199 GB |

`analysis_snapshots` 平均约 176 KB/行，是当前第一容量问题。先将其 payload
拆分、压缩、去重或设定明确保留期，收益大于立即更换数据库。

### 每日新增

以 2026-07-23 至 2026-07-27 五个完整 UTC 日为窗口：

- canonical microstructure raw observation 平均 **1,085,984 行/日**，中位数
  548,347 行/日；范围从中断日的 9 行到 3,274,932 行。
- 把 flow buckets、快照、decision、聚合等实际新增对象计入容量模型后，平均
  **1,377,261 行/日**，约 15.9 行/秒；中位数 929,740 行/日。
- 估算物理增长平均 **897.7 MB/日（856 MiB/日）**，中位数
  757.5 MB/日；五日范围 543.6–1,552.3 MB/日。
- `analysis_snapshots` 在完整日通常新增约 2,500–2,800 行，单独贡献约
  0.44–0.50 GB/日。
- 备份中成交行同一个 `ingested_at_ms` 的最大组为 **311 行**；当前 live
  collector 配置的事务 batch 上限是 **300 行/150 ms**。备份最后一个 writer
  checkpoint 是 batch 1、queue 0、write latency 27 ms。311 是历史/并行摄取的
  同毫秒代理，不应解释为事务边界的精确审计值。

### 容量预测

假设不调整保留期、不压缩 payload、不 reclaim 空间，并保持五日平均数据组合：

| 时间点 | 预测总容量 |
| --- | ---: |
| 当前 | 7.70 GB |
| +30 天 | **34.63 GB** |
| +90 天 | **88.50 GB** |
| +180 天 | **169.29 GB** |
| +365 天 | **335.37 GB** |

模型未计 PostgreSQL MVCC/WAL、临时排序、备份副本和 20–30% 运维余量。硬件
不能只按 335 GB 配置。

若系统盘仅有 40 GB 可用于这两库，按五日平均 897.7 MB/日，扣除当前
7.70 GB 后约 36 天写满；按观测高位 1.552 GB/日约 21 天写满。若必须保留
30% 空闲，安全窗口仅约 **13–23 天**。因此独立数据盘属于近期容量前置条件，
而不是等数据库迁移时再处理的优化项。

### 查询并发需求

当前 API 是无硬性线程上限的 `ThreadingHTTPServer`，同时还有 dashboard
healthcheck、collector healthcheck、前端轮询、scheduler 和研究任务。因此需求
不是“固定一个 reader”。近期按 **4 个持续 reader、8 个短时并发 reader** 做
容量规划，压测必须覆盖 16 并发的保护情景。若持续达到 8 reader 且 p95 越线，
就是 PostgreSQL 的明确迁移信号。

## 本机小 fixture 基准

在本 worktree 创建并自动删除临时 SQLite，写入 30,000 个 observation，事务
batch 300。硬件、缓存和 9.7 MB fixture 与生产数据规模不同，结果只能说明原型
路径有效，不能代替目标盘压测。

| 项目 | 结果 |
| --- | ---: |
| SQLite batch 写 | 38,235 rows/s |
| 1 reader，范围查询 1,000 行 | p50 72.3 ms；p95 122.9 ms |
| 4 readers | p50 105.0 ms；p95 179.5 ms |
| 8 readers | p50 234.2 ms；p95 309.4 ms |

没有 PostgreSQL/Docker/Podman/psycopg 环境，所以未伪造 PostgreSQL 对比数字。
PostgreSQL 集成测试安全 skip。迁移决策前应在目标硬件按 7 天真实分布复放，
测 COPY、steady write、8/16 reader、checkpoint、VACUUM 和备份恢复。

### PostgreSQL 容量与资源预算（非实测）

当前 SQLite 对象页为 7.70 GB。由于 PostgreSQL tuple/header、MVCC、free-space
map、索引 fill factor 和膨胀，未压缩稳态数据库应先按 SQLite 的
**1.2–1.8 倍，即约 9.2–13.9 GB** 预算。首次 COPY/建索引期间还应另外预留：

- 8–16 GB 导入 WAL（取决于 checkpoint、full-page write、归档与压缩）；
- 7.7–15.4 GB staging、排序和校验工作区；
- 至少一份 9–14 GB 可恢复备份。

因此当前数据量的 PostgreSQL 影子 PoC 也应准备 **至少 40–50 GB 独立可用空间**；
生产按一年线性模型、30% 空闲和一份备份配置时，仍建议 1 TB NVMe。以上是容量
边界，不是本机 PostgreSQL benchmark；Docker/Podman 不可用，本轮没有启动容器。

当前观测写入平均约 16 rows/s，事务上限 300 rows/150 ms；读并发按 4 个持续、
8 个短时、16 个保护峰值规划。SQLite checkpoint 目标 32 MiB、WAL 上限
128 MiB；一致备份实际暂停到传输完成约 58 分钟、到 manifest 完成约 61 分钟。
PostgreSQL PoC 必须分别测 steady WAL、checkpoint 长尾、autovacuum、备份和恢复，
不能从平均写 QPS推断维护窗口。

## 方案比较

| 维度 | 优化 SQLite | SQLite + 独立数据盘 | SQLite 月度分片 | PostgreSQL 单机 | PostgreSQL 月分区 | TimescaleDB（可选） |
| --- | --- | --- | --- | --- | --- | --- |
| 写吞吐 | 单 writer 很强；300 行 batch 合适 | fsync 抖动更低 | hot 月仍是单 writer | 多 session；hot index/WAL 仍会竞争 | 与单机相近 | 与 PG 相近；压缩增加 CPU |
| 批量写 | `executemany` + 单事务 | 同左 | 当前月事务简单；跨文件不原子 | COPY/staging merge 最强 | COPY 前须预建目标月 | COPY + hypertable API |
| 并发 reader | WAL 可并发读；单文件 I/O 争用 | I/O 改善，锁语义不变 | 月间隔离，但 fan-out 占连接/文件句柄 | MVCC、连接池和查询治理更好 | 加 partition pruning | 同 PG，时间工具更丰富 |
| WAL | 32 MiB checkpoint、128 MiB limit | WAL 与系统盘隔离 | hot 月有 WAL；cold immutable | 归档/PITR 成熟，写放大更高 | 小月索引不消除 WAL | 后台任务也产 WAL |
| 范围查询 | `(instrument,time)` index 有效 | 同左，随机读更快 | 按月 fan-out、归并 cursor | B-tree/BRIN，planner 更强 | 时间条件 prune 月份 | 自动 chunk pruning |
| 聚合 | 大聚合会抢 writer I/O | 降低 I/O 争用，CPU 不变 | 中心 aggregate 避免扫描全部 cold 月 | parallel aggregate/materialized view | 月局部聚合清楚 | continuous aggregate 可选 |
| 备份/恢复 | 整文件；暂停窗口较长 | 复制更快，仍是整文件 | hot 小、cold 可增量；manifest 更复杂 | basebackup、WAL、逻辑导出、PITR | 分区归档灵活 | 还需验证扩展版本 |
| 运维复杂度 | 最低 | 低 | 中：切月、manifest、late overlay、句柄 | 中高：角色、VACUUM、连接、WAL | 高：再加分区维护 | 最高 |
| 内存/CPU | 最适合小主机 | 基本不变 | fan-out 与 merge 增加少量资源 | 建议 8–16 GB；维护增加 CPU | catalog/pruning 增加开销 | 后台 worker/压缩开销最高 |
| 磁盘 | 当前 7.7 GB；payload 是根因 | 首选 1 TB；数据/备份分离 | 不减少总量，只缩小故障域 | heap+index+WAL 通常更大 | 删除整月快，长期治理较好 | 压缩可能回收历史空间 |
| 故障恢复 | 单文件损坏影响大 | 盘故障仍需异机备份 | 单月边界更小；中心 manifest 是新风险 | crash recovery/PITR 完整 | 单月重建边界清楚 | 依赖 PG + 扩展流程 |
| 迁移/回滚 | 无迁移；最低 | 路径切换风险低，可快速回退 | 需 shadow build；保留旧库可回退 | 类型/时序/UPSERT 风险高；回滚中高 | 再加路由、唯一约束；回滚高 | 扩展锁定；回滚最高 |

### 方案判断

1. **优化 SQLite：继续使用。** 近期数据写入平均仅约 16 rows/s，远低于
   fixture batch 能力。容量增长和 payload 设计，而非平均写 QPS，才是当前
   首要问题。
2. **SQLite + 独立数据盘：立即优先。** 它直接缩短 fsync/备份时间，隔离系统盘
   I/O，迁移风险和回滚成本最低。升级前先验证挂载失败时服务拒绝回落到系统盘，
   避免静默写错路径。
3. **SQLite 月度分片：先准备、达到量化阈值后实施。** 当前 microstructure
   单库仅 2.416 GB，分片不会修复 payload 重复或降低总容量；触发条件和恢复流程
   见 `microstructure_sharding_design.md`。
4. **PostgreSQL 单机：保留为下一阶段。** 当并发 reader、备份窗口或维护锁
   成为主要矛盾时价值明显；当前不能部署在 1.6 GB 主机。
5. **PostgreSQL 月分区：若迁移则采用。** 对时间范围、90/180 日 retention、
   detach/archive/drop 有意义。预建当前月和下月；缺分区应显式失败，不设
   DEFAULT 分区隐藏路由错误。
6. **TimescaleDB：只做可选 PoC。** 只有压缩率或 continuous aggregate 的实测
   收益覆盖扩展运维成本时采用；基础 schema 和 adapter 不依赖它。

## 推荐硬件

近期 SQLite 主机：

- 最低 4 GB RAM；建议 **8 GB RAM**；
- 2–4 vCPU；
- **1 TB 高耐久 SSD/NVMe** 数据盘，持续可用空间不低于 500 GB；
- 数据盘与离机备份目标分离，启用磁盘 SMART、容量、延迟和挂载监控；
- 预留至少 30% 空间，并另外保留一份完整备份和恢复工作空间。

PostgreSQL 目标：

- 建议 8 GB RAM 起步，生产与迁移并行时 16 GB 更稳妥；
- 4 vCPU；
- 1 TB NVMe，若 WAL/备份同机则应更大或将 WAL/备份放独立可靠设备；
- 有断电保护/可靠 flush 语义，不能用不保证 fsync 的廉价网络盘；
- PostgreSQL 内存参数要按容器/主机实际上限设置，不能套用大内存默认模板。

## 迁移触发条件

先治理 retention/payload 并升级数据盘；此后任一关键条件持续出现，启动
PostgreSQL 影子 PoC。两项或以上同时越线，进入迁移排期。

| 指标 | 触发值 |
| --- | --- |
| SQLite 容量 | 单个 hot DB ≥50 GB，或总量 ≥80 GB；或按 90 天预测会超过数据盘 70% |
| 日增长 | 7 日平均 ≥1.0 GB/日；治理 `analysis_snapshots` 后仍 ≥0.5 GB/日 |
| sustained iowait | ≥10% 持续 15 分钟，或 ≥20% 持续 5 分钟，每日重复 |
| health/coverage | p95 ≥500 ms 持续 15 分钟，或 p99 ≥1 s |
| writer queue | p95 ≥1,000 或连续 15 分钟 ≥2,000（当前 hard max 20,000） |
| writer latency | 300-row transaction p95 ≥250 ms，或 source lag ≥30 s |
| 并发查询 | 持续 ≥8 reader 或峰值 ≥16，且 p95 SLO 越线 |
| 维护 | prune/checkpoint/integrity 影响 live write ≥5 分钟，或全维护 ≥30 分钟 |
| WAL | SQLite WAL 重复达到 128 MiB limit、checkpoint busy，或异常增长 ≥1 GiB |
| 备份窗口 | 一致备份 ≥60 分钟，或超过规定 RPO/RTO |

本次 manifest 从 pause 03:06:14 到最后文件传完 04:04:32 约 58 分钟，到
manifest 完成约 61 分钟，已经位于备份窗口触发边缘。独立盘和备份方式优化应
优先验证。

## PostgreSQL 原型

原型文件：

- `sql/postgres/storage_schema.sql`：raw observation、CVD/OI aggregate schema；
- `sql/postgres/monthly_partitions.sql`：allow-list 月分区函数；
- `dashboard/postgres_storage.py`：字段映射、UPSERT、COPY/临时 staging merge、
  coverage、范围查询和 keyset cursor。

### 幂等与分区约束

PostgreSQL 要求 partitioned table 的 UNIQUE/PRIMARY KEY 包含 partition key。
原型因此使用 `(observed_at, uniqueness_key)`。Crypto-Bot 的
`uniqueness_key` 由稳定 source identity 生成，同一事件的 timestamp 也稳定；
两者共同实现 partition-safe 幂等。离线校验仍必须检测“同 uniqueness_key、
不同 source_ts_ms”，这种异常不能被跨分区唯一约束自动阻止。

COPY 不直接写目标表：adapter 先在进程内按同一 key 去重，再 COPY 到 transaction
local staging table，最后 `INSERT ... ON CONFLICT DO NOTHING`。中断时整个事务
回滚。

### timestamp 边界

- SQLite 的 source/ingest 时间以整数 epoch 毫秒为事实源，PostgreSQL 同时保留
  `source_ts_ms bigint`/`ingested_at_ms bigint` 和派生的 UTC `timestamptz`。
- 转换只接受整数，不接受隐式秒/毫秒猜测；负毫秒也正确向下分解。
- `timestamptz` 必须 timezone-aware，写入前统一 UTC；月分区边界显式用 UTC，
  不依赖 session timezone。
- PostgreSQL `timestamptz` 精度高于毫秒，但 adapter 回转时截到毫秒。校验以
  bigint 完全相等为准，不能只比较格式化字符串。
- 范围统一使用 `[start, end)`；分页顺序是
  `(observed_at ASC, uniqueness_key ASC)`，cursor 使用相同二元组。

## 最小迁移路径

1. **建立 schema**：在隔离的非生产 PostgreSQL 建 schema；预建历史覆盖的所有
   月和未来一个月；固定 UTC、角色、连接池、WAL/备份策略。
2. **历史离线导入**：从新的只读 SQLite 快照按时间和唯一键 keyset 分页，
   COPY staging 后 merge；先导 raw，再导 aggregates/research。导入不连生产。
3. **校验**：逐表总数、逐月/逐 instrument 数量、min/max、NULL 数、唯一键冲突、
   随机 hash sample、CVD/OI 数值与 coverage summary 对比；执行 restore 演练。
4. **捕获增量**：优先短暂停写并导入 snapshot 后增量。若业务要求双写，使用
   持久 outbox/单调 sequence，由两个 sink 各自确认，不在请求线程内做无日志的
   两次独立写。
5. **短暂停机切换**：暂停 collector/writer，等待 queue=0 和 SQLite checkpoint，
   导入最后增量，重复校验，把 PostgreSQL writer 打开，再切 read path。
6. **回滚窗口**：至少 24 小时，建议 72 小时。保留 SQLite 文件、schema 和
   checkpoint，不 vacuum/drop；若 PostgreSQL 已接收独有新写，继续 outbox
   secondary write 或生成可验证的反向增量。
7. **停止旧写入**：观察窗口、restore 演练、业务对账和 SLO 均通过后，停止
   SQLite secondary writer；仍保留只读快照到 retention 到期。
8. **最终确认**：逐月 row/hash/coverage 对账、备份可恢复、告警有效、无生产
   DSN 泄漏，然后才归档旧库。

### 双写风险

- 两次独立 commit 没有原子性：A 成功/B 失败会分叉；
- 网络重试会改变到达顺序，跨月边界尤其容易路由到不同 partition；
- `ON CONFLICT DO UPDATE` 会让晚到旧值覆盖新值；raw observation 应使用
  `DO NOTHING`，可变状态必须带 source sequence/version 比较；
- timestamp 正规化差异会绕过组合唯一键；
- queue restart 若只记录内存 cursor，会丢失或重复。

推荐 outbox sequence + sink acknowledgement。停机切换比无 outbox 的“简单双写”
更安全。

### 迁移与停机时间估算

当前 7.7 GB 在目标 NVMe 上，历史 COPY、建索引和校验的初始预算为
**30–90 分钟后台窗口**；在当前 1.6 GB 主机或慢盘上应预算 **1–3 小时**，且
不建议执行。历史导入可在服务运行时从一致离机快照完成。

最终停机目标 **5–15 分钟**：停 writer、排空 queue、导入末段、校验 high-water、
切连接并做 smoke test。没有可重放增量/outbox 时，为降低数据分叉风险应预算
**30–60 分钟**，不要用未经实测的 5 分钟承诺。

### 回滚

在 24–72 小时窗口内保持 SQLite 快照和 secondary sink：

1. 阻止新写，记录 PostgreSQL high-water；
2. 排空 outbox，比较两个 sink 的唯一键和 source timestamp；
3. 将 PostgreSQL 独有且已验证的行按 sequence 反向补到 SQLite；
4. 切回 SQLite read/write；
5. PostgreSQL 保持只读用于根因分析，不 drop partition/volume；
6. 对外恢复前再次检查 coverage、queue 和最新 source timestamp。

如果没有 outbox 或反向同步，回滚只能回到切换时快照，会丢失切换后的新数据；
这是切换前的阻断条件。

## 测试状态

`tests/test_postgres_storage_adapter.py` 覆盖：

1. SQLite/PostgreSQL 字段映射；
2. epoch 毫秒与 aware UTC timestamp 一致；
3. UPSERT 幂等；
4. UTC 月分区路由；
5. 范围查询排序；
6. cursor 无重叠/缺口分页；
7. coverage summary；
8. duplicate 不增加 staging 行；
9. missing/NULL；
10. CVD/OI 独立字段与单位；
11. 无 PostgreSQL 环境安全 skip；
12. 无默认连接、不读生产 `DATABASE_URL`；
13. 不调用策略或订单 API。

本机没有 Docker、Podman、psycopg 或显式 test DSN，未安装服务，PostgreSQL
fixture 集成测试按设计 skip。SQL 与 adapter 均已提交供有隔离环境时运行。
