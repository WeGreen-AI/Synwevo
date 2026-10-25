# 成本汇总基准（2026-10-25，本机隔离临时库）

由 [`scripts/bench_cost_admission.py`](../../../../scripts/bench_cost_admission.py) 生成：每次在系统临时目录新建一个隔离的 Store（拒绝 `data/`、配置的
`SYNWEVO_DATA_DIR` 与已有库），结束后删除；合成的夹具价目、用户与钱包，没有网络、密钥或真实价格。工作负载（每个规模 N）：
2034-05-01 至 2034-05-20T12:00（周六）共 N 次准入，95% 已结算（10^-15 网格上的随机成本），最近 24 小时 2,000 次本地停止（冻结仍在），
300 个在途未知，其余为已结束的未知；2,000 个付款钱包（真实付款人占 5%），21 条路线其中一条热路线占 30%；付款方 3,600 秒、路线 86,400 秒速率窗口开启。
然后 500 次真实派发（预留、准备、在 FULL 持久性下带准入派发、结算）。它取代 2026-10-01 的快照测量（10,000 条上限下 P95 30.6 ms）。

| Python / SQLite | N | `_counters`+`guard` p50/p95/max ms | 读取行数 | 派发 p50/p95/max ms | 触发器 µs/对 | 结算额外 ms (p50) | 回填 s | 40 天校验 s | 两天校验 s | 全量校验 s |
|---|---|---|---|---|---|---|---|---|---|---|
| 3.12.6 / 3.54.0 | 200,000 | 1.642 / 1.784 / 1.819 | 315 (+速率桶 104) | 2.269 / 2.432 / 4.969 | 66.4 | 0.385 | 6.748 | 4.141 | 1.432 | 4.294 |
| 3.12.6 / 3.54.0 | 1,000,000 | 1.206 / 1.253 / 1.407 | 318 (+速率桶 146) | 2.591 / 2.885 / 5.001 | 69.1 | 0.324 | 36.713 | 24.411 | 7.175 | 26.265 |
| 3.14.5 / 3.54.0 | 200,000 | 1.719 / 1.825 / 1.944 | 315 (+速率桶 104) | 2.517 / 2.759 / 7.54 | 67.0 | 0.341 | 6.793 | 4.879 | 1.678 | 4.861 |
| 3.14.5 / 3.54.0 | 1,000,000 | 1.241 / 1.291 / 1.496 | 318 (+速率桶 146) | 2.624 / 3.036 / 4.877 | 69.6 | 0.378 | 35.609 | 27.043 | 8.076 | 28.531 |

合同 §12 目标：`_counters`+`guard` p95 ≤ 3 ms 且 1M 不超过 200k 的 1.5 倍；派发 p95 < 20 ms；触发器 ≤ 300 µs/对；结算额外 ≤ 1 ms；回填与 40 天校验 ≤ 15 s。
各文件的 `targets_met` 逐项给出是否达标；`query_plans` 是准入路径上每条成本语句的 EXPLAIN QUERY PLAN（全部是主键或索引 SEARCH，子查询的 SCAN 只扫描已物化的少量行）。

结论：200k 时全部目标达标。1M 时准入读路径仍在目标内（且不随规模变慢），回填与 40 天校验随事实数线性增长、超过 15 s（见上表），
它们不在发送路径上：回填只在首次启用或汇总表丢失时运行一次，校验在只读快照里运行、不占写锁。重建（命令行）持有写锁，时长约为回填加全量校验。

准入路径的查询计划（Python 3.12、200k）：

- `SELECT 1 FROM model_cost_rollup_faults INDEXED BY model_cost_rollup_faults_open …` — SEARCH model_cost_rollup_faults USING INDEX model_cost_rollup_faults_open (environment=? AND provider=? AND account_ref=?)
- `SELECT 1 FROM model_cost_admissions INDEXED BY model_cost_admissions_scope WHERE…` — SEARCH model_cost_admissions USING COVERING INDEX model_cost_admissions_scope (environment=? AND provider=? AND account_ref=? AND admitted_at>?)
- `SELECT start,unit,den,n_adm,n_res,n_cross,n_exc,bound_hi,bound_lo,rbound_hi,rbou…` — SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>?)
- `SELECT i.attempt_key,i.admitted_at,a.unit,u.attempt_key AS usage_key,u.cost_n,u.…` — SEARCH i USING COVERING INDEX model_cost_exceptions_scope (environment=? AND provider=? AND account_ref=? AND admitted_at>?); SEARCH a USING INDEX sqlite_autoindex_model_cost_admissions_1 (attempt_key=?) LEFT-JOIN; SEARCH u USING INDEX sqlite_autoindex_billing_usage_1 (attempt_key=?) LEFT-JOIN
- `WITH o AS MATERIALIZED (SELECT attempt_key,MAX(fe) AS fe,MAX(hd) AS hd,MAX(hc) A…` — MATERIALIZE y; MATERIALIZE x; MATERIALIZE o; CO-ROUTINE (subquery-2); COMPOUND QUERY; LEFT-MOST SUBQUERY; SEARCH model_cost_inflight USING COVERING INDEX model_cost_inflight_open (environment=? AND provider=? AND account_ref=? AND expires_at>?); UNION ALL; SEARCH model_cost_hold USING INDEX model_cost_hold_open (environment=? AND provider=? AND account_ref=? AND deadline>?); SCAN (subquery-2); USE TEMP B-TREE FOR GROUP BY; SCAN o; SEARCH a USING INDEX sqlite_autoindex_model_cost_admissions_1 (attempt_key=?) LEFT-JOIN; SEARCH c USING INDEX sqlite_autoindex_billing_calls_1 (id=?) LEFT-JOIN; SEARCH e USING COVERING INDEX sqlite_autoindex_model_cost_endings_1 (attempt_key=?) LEFT-JOIN; SEARCH u USING COVERING INDEX sqlite_autoindex_billing_usage_1 (attempt_key=?) LEFT-JOIN; SCAN x; SCAN y
- `SELECT COUNT(*) FROM model_cost_pending INDEXED BY model_cost_pending_scope WHER…` — SEARCH model_cost_pending USING COVERING INDEX model_cost_pending_scope (environment=? AND provider=? AND account_ref=? AND admitted_at>?)
- `SELECT unit,den,COUNT(*) AS n,SUM(n_adm) AS n_adm,SUM(n_res) AS n_res,SUM(n_cros…` — CO-ROUTINE (subquery-3); COMPOUND QUERY; LEFT-MOST SUBQUERY; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>?); SCAN (subquery-3); USE TEMP B-TREE FOR GROUP BY
- `SELECT unit,den,COUNT(*) AS n,SUM(n_adm) AS n_adm,SUM(n_res) AS n_res,SUM(n_cros…` — CO-ROUTINE (subquery-3); COMPOUND QUERY; LEFT-MOST SUBQUERY; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>?); SCAN (subquery-3); USE TEMP B-TREE FOR GROUP BY
- `SELECT a.unit,a.admitted_at,a.reserved_units,u.attempt_key AS usage_key,u.cost_n…` — SEARCH i USING COVERING INDEX model_cost_exceptions_scope (environment=? AND provider=? AND account_ref=? AND admitted_at>?); SEARCH a USING INDEX sqlite_autoindex_model_cost_admissions_1 (attempt_key=?); SEARCH att USING INDEX sqlite_autoindex_billing_attempts_1 (id=?); SEARCH c USING INDEX sqlite_autoindex_billing_calls_1 (id=?); SEARCH acc USING INDEX sqlite_autoindex_billing_accounts_1 (id=?); SEARCH u USING INDEX sqlite_autoindex_billing_usage_1 (attempt_key=?) LEFT-JOIN
- `SELECT unit,den,COUNT(*) AS n,SUM(n_adm) AS n_adm,SUM(n_res) AS n_res,SUM(n_cros…` — CO-ROUTINE (subquery-3); COMPOUND QUERY; LEFT-MOST SUBQUERY; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>? AND start<?); UNION ALL; SEARCH model_cost_buckets USING PRIMARY KEY (environment=? AND provider=? AND account_ref=? AND dim=? AND member=? AND span=? AND start>?); SCAN (subquery-3); USE TEMP B-TREE FOR GROUP BY

重现：`python scripts/bench_cost_admission.py --out <文件>`（默认 200k 与 1M；`--sizes`、`--work` 可调）。
