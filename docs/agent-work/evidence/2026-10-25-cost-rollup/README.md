# 成本汇总基准（2026-10-25，2026-10-26 按复核修订；本机隔离临时库）

由 [`scripts/bench_cost_admission.py`](../../../../scripts/bench_cost_admission.py) 生成：每次在系统临时目录新建一个隔离的 Store（拒绝 `data/`、配置的
`SYNWEVO_DATA_DIR` 与已有库），结束后删除；合成的夹具价目、用户与钱包，没有网络、密钥或真实价格。工作负载（每个规模 N）：
2034-05-01T00:00（周一）至 2034-05-20T12:00（周六）共 N 次准入，全部在当月（各文件 `history` 给出首末准入与当月条数），95% 已结算（10^-15 网格上的随机成本），
最近 24 小时 2,000 次本地停止（冻结仍在），300 个在途未知，其余为已结束的未知；2,000 个付款钱包（真实付款人占 5%），21 条路线其中一条热路线占 30%；
付款方 3,600 秒、路线 86,400 秒速率窗口开启。然后 500 次真实派发（预留、准备、在 FULL 持久性下带准入派发、结算）。
2026-10-25 的版本把日历常量写错（实际是 04-25 至 05-14，当月只有约 69%），触发器成本只量了一条批量语句、校验只量了 40 天窗口；本版修正，见下。

| Python / SQLite | N | `_counters`+`guard` p50/p95/max ms | 读取行数 | 派发 p50/p95/max ms | 触发器 µs/对：实时 p50（p95）/ 批量摊销 | 写锁额外 µs p50/p95 | 结算额外 ms (p50) | 回填 s | 每日校验 s / 峰值 MB | 两天校验 s / 峰值 MB | 全量校验 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3.12.6 / 3.54.0 | 200,000 | 1.636 / 1.855 / 2.114 | 321 (+速率桶 104) | 2.29 / 2.606 / 4.949 | 318.6（445.8）/ 65.8 | 444.2 / 784.2 | 0.337 | 6.682 | 4.969 / 111.3 | 1.781 / 108.9 | 4.882 |
| 3.12.6 / 3.54.0 | 1,000,000 | 1.282 / 1.427 / 1.551 | 324 (+速率桶 146) | 2.606 / 2.826 / 4.977 | 356.4（599.7）/ 69.4 | 538.7 / 1704.5 | 0.367 | 34.332 | 29.178 / 113.9 | 8.489 / 114.2 | 27.74 |
| 3.14.5 / 3.54.0 | 200,000 | 1.697 / 1.77 / 1.894 | 321 (+速率桶 104) | 2.362 / 2.65 / 5.035 | 314.9（435.6）/ 67.1 | 439.4 / 734.1 | 0.342 | 6.835 | 5.38 / 119.8 | 1.98 / 117.4 | 5.303 |
| 3.14.5 / 3.54.0 | 1,000,000 | 1.295 / 1.339 / 1.526 | 324 (+速率桶 146) | 2.577 / 2.758 / 6.514 | 360.3（511.6）/ 70.0 | 535.3 / 2590.7 | 0.354 | 34.327 | 29.901 / 124.6 | 9.154 / 122.2 | 28.508 |

- **触发器（实时）**：每对是一次准入 INSERT 自成一个 `BEGIN IMMEDIATE … COMMIT`、再一次用量 INSERT 自成一个（与 `Ledger.dispatch`、`settle` 一样，`synchronous=FULL`），
  每对不同的付款钱包、路线与秒；在本库（保留生产触发器）与一份删掉生产触发器的副本上交替执行，取两条语句的耗时差（写锁额外则含两次 COMMIT）。
  这才是在线写入方付出的代价。**批量摊销**是一条 `INSERT…SELECT` 写入上万行、共享热桶时的平均值，只是下限，不能当作每对的成本。
- **每日校验**按风险监控的调用方式（`mode=daily`，从上月 1 日起：这里是全部历史）在一个子进程里运行，峰值 MB 是该子进程的最大常驻内存（含解释器与导入约 100 MB）；
  两天校验同理（`mode=recent`）。月末的每日校验会读两个月的事实（按这里的密度约为本表的 3 倍）：时间随读到的事实线性增长，内存不增长（流式）。

合同 §12 目标：`_counters`+`guard` p95 ≤ 3 ms 且 1M 不超过 200k 的 1.5 倍；派发 p95 < 20 ms；触发器 ≤ 300 µs/对（实时）；结算额外 ≤ 1 ms；回填与每日校验 ≤ 15 s。
各文件的 `targets_met` 逐项给出是否达标；`query_plans` 是准入路径上每条成本语句的 EXPLAIN QUERY PLAN（全部是主键或索引 SEARCH，子查询的 SCAN 只扫描已物化的少量行）。

未达标项（任一运行）：`backfill_le_15s`、`triggers_le_300us_per_pair`、`verify_daily_le_15s`。

结论：准入读路径（`_counters`+`guard` p95 ≤ 1.9 ms，1M 不比 200k 慢）、派发 p95 与结算额外在两个规模、两个 Python 版本上都达标。未达标的两项：

1. **触发器在实时写入路径上每对 315–360 µs（p50）**，超过 300 µs 目标；2026-10-25 版报告的 66–70 µs 是一条批量语句的摊销值（下限），不是在线写入方付出的代价。
   差额几乎全在每条语句的语句日志：触发器一次改写的页超过 64 KiB 后溢出到临时文件。复核在同一机器上测得 `PRAGMA temp_store=MEMORY` 时约 96 µs/对；
   那是整个连接的设置（切换时会删除连接上已有的临时表，并改变大排序的内存行为），本次没有改，留给协调者决定。生产的 `/tmp` 是 tmpfs，溢出文件不落盘，绝对值未测。
   端到端目标不受影响：派发 p95 < 3.1 ms，结算额外 ≤ 0.37 ms。
2. **1M 时回填（约 34 s）与每日校验（约 29–30 s）超过 15 s。** 两者都不在发送路径上：回填只在首次启用、触发器版本升级或汇总表丢失时运行一次，
   持有写锁（其间打开 Store 的其他角色进程等待，不再在 30 秒后失败；运行中的写入方仍按 30 秒超时），所以随部署（先停全部角色）运行；
   校验在只读快照里运行、不占写锁，按时间流式读取，内存不随事实数增长（子进程峰值 111–125 MB，其中解释器与导入 97–107 MB；修订前 1M 时约 2.7 GB）。
   月末的每日校验读两个月事实，时间约为这里的 3 倍。

准入路径的查询计划（2026-10-26 在当前代码上重新取得：Python 3.12.6 / SQLite 3.54.0，同一脚本的 20,000 条准入小规模运行；计划不随规模变化：表上全部是索引或主键 SEARCH，SCAN 只扫描已物化的少量行）。上表数字与两个 JSON 文件（含其中的 `query_plans`）是删除运维“接受”路径（D18 第 6 条）之前测的；删除去掉了读路径上的两个子查询（时钟检查的 NOT EXISTS、当日待确认数的减法），只会更快，未重测：

- `SELECT 1 FROM model_cost_rollup_faults INDEXED BY model_cost_rollup_faults_open …` — SEARCH model_cost_rollup_faults USING INDEX model_cost_rollup_faults_open (environment=? AND provider=? AND account_ref=?)
- `SELECT 1 FROM model_cost_admissions INDEXED BY model_cost_admissions_scope WHERE…` — SEARCH model_cost_admissions USING COVERING INDEX model_cost_admissions_scope (environment=? AND provider=? AND account_ref=? AND admitted_at>?)
- `SELECT start,unit,den,n_adm,n_res,n_cross,n_exc,bound_hi,bound_lo,rbound_hi,rbou…` — SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>?)
- `SELECT i.admitted_at,a.unit,u.attempt_key IS NOT NULL AS has_usage,u.cost_n,u.co…` — SEARCH i USING COVERING INDEX model_cost_exceptions_scope (environment=? AND provider=? AND account_ref=? AND admitted_at>?); SEARCH a USING INDEX sqlite_autoindex_model_cost_admissions_1 (attempt_key=?) LEFT-JOIN; SEARCH u USING INDEX sqlite_autoindex_billing_usage_1 (attempt_key=?) LEFT-JOIN
- `WITH o AS MATERIALIZED (SELECT attempt_key,MAX(fe) AS fe,MAX(hd) AS hd,MAX(hc) A…` — MATERIALIZE y; MATERIALIZE x; MATERIALIZE o; CO-ROUTINE (subquery-2); COMPOUND QUERY; LEFT-MOST SUBQUERY; SEARCH model_cost_inflight USING COVERING INDEX model_cost_inflight_open (environment=? AND provider=? AND account_ref=? AND expires_at>?); UNION ALL; SEARCH model_cost_hold USING INDEX model_cost_hold_open (environment=? AND provider=? AND account_ref=? AND deadline>?); SCAN (subquery-2); USE TEMP B-TREE FOR GROUP BY; SCAN o; SEARCH a USING INDEX sqlite_autoindex_model_cost_admissions_1 (attempt_key=?) LEFT-JOIN; SEARCH c USING INDEX sqlite_autoindex_billing_calls_1 (id=?) LEFT-JOIN; SEARCH e USING COVERING INDEX sqlite_autoindex_model_cost_endings_1 (attempt_key=?) LEFT-JOIN; SEARCH u USING COVERING INDEX sqlite_autoindex_billing_usage_1 (attempt_key=?) LEFT-JOIN; SCAN x; SCAN y
- `SELECT COUNT(*) FROM model_cost_pending INDEXED BY model_cost_pending_scope WHER…` — SEARCH model_cost_pending USING COVERING INDEX model_cost_pending_scope (environment=? AND provider=? AND account_ref=? AND admitted_at>?)
- `SELECT unit,den,COUNT(*) AS n,SUM(n_adm) AS n_adm,SUM(n_res) AS n_res,SUM(n_cros…` — CO-ROUTINE (subquery-3); COMPOUND QUERY; LEFT-MOST SUBQUERY; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>?); SCAN (subquery-3); USE TEMP B-TREE FOR GROUP BY
- `SELECT unit,den,COUNT(*) AS n,SUM(n_adm) AS n_adm,SUM(n_res) AS n_res,SUM(n_cros…` — CO-ROUTINE (subquery-3); COMPOUND QUERY; LEFT-MOST SUBQUERY; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>?); SCAN (subquery-3); USE TEMP B-TREE FOR GROUP BY
- `SELECT a.unit,a.admitted_at,a.reserved_units,u.attempt_key IS NOT NULL AS has_us…` — SEARCH i USING COVERING INDEX model_cost_exceptions_scope (environment=? AND provider=? AND account_ref=? AND admitted_at>?); SEARCH a USING INDEX sqlite_autoindex_model_cost_admissions_1 (attempt_key=?); SEARCH att USING INDEX sqlite_autoindex_billing_attempts_1 (id=?); SEARCH c USING INDEX sqlite_autoindex_billing_calls_1 (id=?); SEARCH acc USING INDEX sqlite_autoindex_billing_accounts_1 (id=?); SEARCH u USING INDEX sqlite_autoindex_billing_usage_1 (attempt_key=?) LEFT-JOIN
- `SELECT unit,den,COUNT(*) AS n,SUM(n_adm) AS n_adm,SUM(n_res) AS n_res,SUM(n_cros…` — CO-ROUTINE (subquery-3); COMPOUND QUERY; LEFT-MOST SUBQUERY; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>?); SCAN (subquery-3); USE TEMP B-TREE FOR GROUP BY

重现：`python scripts/bench_cost_admission.py --out <文件>`（默认 200k 与 1M；`--sizes`、`--work` 可调）。

