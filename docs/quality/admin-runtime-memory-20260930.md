# Admin 容器内存门禁排查（2026-09-30）

冻结B1提交dde47cc的完整验证镜像在3GB/2CPU、断网限制下，于约83%被OOM终止（137）。没有pytest汇总，不能算通过。Docker随后不可用，Colima只读状态确认停止；恢复原配置环境后将重跑完整门禁，不提高资源限制。

临时工厂的同条件小实验确认两条保留链：被动BridgeWorker无条件向全局_wakers登记bound method，但未登记后台线程，stop_all无法释放；锁定FastAPI0.141.1的三个callable分类LRU以_CallIdentity强引用路由闭包，闭包保留整个应用。后者每缓存有4096上限，是大批临时工厂的延迟回收，不能称为无界生产泄漏。

修复：只在BridgeWorker真正启动时登记waker；根pytest收尾先停止全部worker，再清除三个可重新计算的FastAPI分类缓存并GC，若线程仍活着则报错。生产缓存和服务资源限制保持不变。未减少测试范围或放宽业务断言。

原回归实际为F..（被动worker释放失败）；修复后23项代表回归通过、7条第三方弃用警告、9.11秒，覆盖四套件代表、手动run_once、真实后台唤醒和重复stop，以及all工厂运行/被动 × 完成/未运行lifespan的矩阵。模块边界和diff检查通过。

同6工厂小实验线程始终1：原场景RSS增长94448KiB、traced约37.55MB并保留API/Store；修复后RSS增长7376KiB、traced末轮405345字节，每轮API/Store/bridge/SQLite连接与登记worker均归零。原始/因果对照`/private/tmp/synwevo-bridge-cache-before.jsonl`，修复后`/private/tmp/synwevo-factory-teardown-after.jsonl`，测试`/private/tmp/synwevo-factory-lifecycle-regression.log`。

这证明保留缺陷及修复效果，不证明其是OOM的唯一来源。最终结论仍取决于同3GB全量容器结果。

## 冻结 d26782a 的真实容器门禁

本次仅覆盖 `d26782a2ceb4a5cf9048e9b81be1c621d3fd58da`，不含之后的动态配置代码。通过 `git archive` 导出；归档 SHA-256 为 `d26929c26a8111026f8a6331b9c7ba6aad4d103deb63f7efda939ea2ed623156`。严格按 CI 的 `umask 077` 和 `tarfile.extractall(filter='data')` 解包，源根与 `packages/core` 目录均为 0700。归档没有真实 data、Git 工作区或未提交代码。

- runtime：`synwevo:admin-b2-runtime`，image ID `sha256:2c2117cfd3b3805cfac1dd61c0c4fdc772cfc093528829a9e9fa90d739a8efb8`。原非 root UID 1000、断网、read-only rootfs、专用 `/tmp` tmpfs 创建 accounts 工厂 smoke 通过。
- verify：`synwevo:admin-b2-verify`，image ID `sha256:f26a360320a23693acfb19ffdafddc7a6b71f4e4d9952f4851b8b32ae1b095e4`。仅从同一冻结归档和上述 runtime 派生。
- 固定容器 `synwevo-admin-b2-full-d26782a`，默认命令 `python -m pytest --tb=short`，四套件不缩范围；断网、3 GiB（3221225472 字节）、2 CPU。仅匿名测试卷，没有宿主真实数据挂载。
- 门禁**未通过**：约 35% 后同一用例超过五分钟没有进展。总观察 864.455 秒，172 次 Docker stats 采样，最大报告使用量 1964947538 字节（约 1.83 GiB）。这是采样峰值，不是内核精确高水位。`OOMKilled=false`。
- 经授权先将 pytest PID 7 的 core soft limit 从无限降到 0，再发送一次 SIGABRT 取得 faulthandler 全线程栈。退出 134 是**人为诊断终止已挂起的测试**，不是自然测试失败，更不是通过。该记录不能替代完整 pytest 汇总或声称四套件通过。

证据保留在 `/private/tmp/synwevo-admin-b2-container-full.log`、`synwevo-admin-b2-container-stats.jsonl`、`synwevo-admin-b2-container-result.json`、`synwevo-admin-b2-core-limit.json` 和 `synwevo-admin-b2-diagnostic-stop.json`。结果 JSON 已明确 `gate_result=not_passed_hang`。完整日志 SHA-256 为 `7eb7432c3d844cfa0bd4a0165043d2cc5fbb863023bcc7fb392d93b078b1863d`。容器和任务镜像保留供复核，没有清理其他资源。

## 栈定位与测试夹具修复

主线程栈停在 `test_sheet_live.py:2014` 的 `a.ack(n2)`，经 `SheetPeer.wait`/`receive_bytes` 等指定序号。原测试先把 n2 入队给并发 ASGI loop，然后才推进假时钟；如果服务端先处理，`collab_sheet_room.py` 冷却期分支会在解析消息之前正确返回 `n=None`。测试忽略这个合法回复并永久等待 n2。单节点隔离诊断为 1 passed / 1.19 秒；同冻结镜像整份原 `test_sheet_live.py` 为 70 passed / 52.70 秒，符合调度竞态而非稳定业务失败。两次诊断都保留协议断言，使用单独 1 GiB / 1 CPU 容器和外层超时，不能当完整门禁。

修复相邻 busy/rate-limit 两个用例：在发送 n2 之前推进假时钟，确保它表达原本要求的“等待结束后到达”。原 `held`、重发和最终内容断言全部保留。新增独立早到帧回归，直接验证冷却期回复 `n=None`、不改变内容、等待后后续序号仍被 held 拒绝、按序重发后最后内容正确。没有修改服务端限流或放宽断言。

全线程栈还显示 **59 个**线程停在 `apps/docs/tests/clamd_double.py:57` 的 `socket.accept()`。原 fixture 只从另一线程 close 监听 socket；Linux 不保证这会唤醒阻塞 accept，线程仍强持整个 double。新增回归在 macOS 原实现通过、在冻结 Linux 镜像的原实现明确失败（`close left the scanner fixture blocked in accept()`），证实平台差异。

Docs 与 Message 各有一份独立 scanner double（遵守应用测试边界），现同步修复：同锁注册连接/handler；关闭时先置 stop，shutdown 监听 socket 与活连接，再在总 2 秒内 join；残留线程明确报错。协议判定、EICAR、加密内容、复扫语义保持不变。每模块新增真实 PING、六次生命周期弱引用回收和半帧阻塞连接关闭回归。这里只修明确标识的测试 double，没有改真实扫描器或生产服务。

## 修复后的 Linux 精确回归

以原 `synwevo:admin-b2-verify` 创建独立容器 `synwevo-admin-b2-fixture-after-d26782a`，仅复制此次五个测试文件（两个模块的 double 与生命周期测试、Docs 的 sheet-live 测试），没有复制当前工作树产品代码。断网、3 GiB/2 CPU、300 秒外层上限，pytest 每用例 45 秒 faulthandler 仅作挂起诊断。**188 passed, 1 skipped, 2 warnings，152.70 秒，退出 0**。

覆盖文件：

```text
apps/docs/tests/test_sheet_live.py
apps/docs/tests/test_clamd_double_lifecycle.py
apps/docs/tests/test_malware_scan.py
apps/docs/tests/test_rescan.py
apps/docs/tests/test_encrypted_files.py
apps/docs/tests/test_file_events.py
apps/messages/tests/test_clamd_double_lifecycle.py
apps/messages/tests/test_attachment_rescan.py
apps/messages/tests/test_encrypted_attachments.py
tests/integration/test_rescan_shared_bytes.py
tests/integration/test_encrypted_member_import.py
```

唯一 skip：`apps/docs/tests/test_encrypted_files.py:417`，未配置 `CLAMD_TEST_ADDRESS`（要求启用 `AlertEncryptedArchive yes` 的真实 clamd）。该真实引擎验收未执行；其余 scanner-double 用例仅证明真实 socket 协议、调用链与治理语义，不冒充真实引擎判定。两条警告为锁定 Starlette/AnyIO 的已知弃用提示。日志 `/private/tmp/synwevo-admin-b2-fixture-after.log`。

原 Linux double 的新增生命周期回归先失败，日志 `/private/tmp/synwevo-admin-b2-clamd-before.log`；修复后两模块均通过六次创建/关闭/GC 及半帧连接关闭回归。模块边界检查覆盖 521 Python、254 前端文件通过，diff whitespace 检查通过。此结果是指定文件的回归，仍不是新冻结完整门禁。

## 冻结 b348fb6 的完整门禁

上述Linux回归完成后，经根代理独立审查已提交b348fb6。以该完整commit SHA重新 `git archive`，复刻 0700 私有解包，制作按该 SHA 命名的独立 runtime/verify 镜像；不使用未提交工作树、不把 d26782a 标签当新源码。先跑同样 UID 1000/断网/read-only runtime 工厂 smoke，再在同 3 GiB/2 CPU、断网条件下运行默认完整四套件。记录新 image IDs、真实 pytest 汇总/skip 原因、退出状态、运行时间及采样内存。此次挂起记录保留，只有新冻结完整门禁实际完成才能更新为通过。


本轮新冻结运行已自然结束，**门禁未通过**；没有OOM，也没有重现旧表格永久等待。此结果只覆盖`b348fb6e171d835614e1de961d42ff95dacdd48e`（包含B3及扫描/协同夹具修复），不包含B4接任与后续两项测试修复。

| 证据 | 实际结果 |
|---|---|
| 源归档 | SHA-256 `860582b08de9a754803de129a81cac51c8685380eb1ea9b03ad145e9857f0333`；私有0700解包；没有未提交代码或真实data |
| runtime | `synwevo:admin-b3-runtime-b348fb6`；`sha256:687a68f16fd67289f82895ed2550b3952946a0d9be44ba2bff478b20794b0cc6`；UID1000/断网/read-only rootfs工厂smoke通过 |
| verify | `synwevo:admin-b3-verify-b348fb6`；`sha256:de70b4cb28827d8e180947153f28a2487c86a728de1b31ee39b51abd84e35a0e` |
| 容器 | `synwevo-admin-b3-full-b348fb6`；ID `9a36c2c89237c411ad6f3051c227721ebedaf6de21506020f451d12574753476`；3GiB、2CPU、断网；只有匿名fixture卷 |
| 默认四套件 | `python -m pytest --tb=short`；**3091 passed、4 failed、5 skipped、236 warnings，2350.26秒**；自然退出1 |
| 运行/资源 | 容器实际2355.473222秒，`OOMKilled=false`；469次采样峰值2145336164字节（约1.998GiB），不是内核精确高水位 |
| 原始日志 | `/private/tmp/synwevo-admin-b3-b348fb6-container-full.log`；SHA-256 `d351e1301c6ac54fb5238e41d602763fa5f46550b9ab7eb4e1566b1a31e5e1f7` |
| 结构化记录 | 同前缀`container-result.json`及`container-stats.jsonl`；result保留commit、image、container、实际资源限制、退出状态与日志哈希 |

四个失败均为`test_factory_memory_lifecycle.py::test_test_teardown_releases_completed_factories`原四参数节点。API/Store/bridge弱引用已回收、waker检查通过，失败在进程级`workers.live()==[]`：该registry按契约包含仍被外部持有的已停止worker，本冻结仍登记131个前序对象；主机关联全套为9个。本轮没有逐一追查这些前序对象的外部持有链。不能将这些四项报告成“B4业务失败”，也不能把原运行改写成通过。

后续提交`496c1e2`只修测试归属：保留原对象弱引用/waker检查，对本次全部登记worker逐一检查回收，线程须退出，registry不得多出前序集合之外的对象；加入前序真实工厂停止后仍被持有、随后释放也完全回收的场景。8个矩阵节点通过。未修改产品worker registry或放宽资源限制。另一个主机全套中的限流精确计数假设由`ff715a1`固定测试模块私有时钟，13项通过；本冻结容器没有出现该限流失败。

**仍未解决或未验证：**本次默认输出没有`-rs`，仅有5 skipped汇总，未逐项取得跳过原因；不据此声称真实扫描器或其他可选外部依赖已验证。另有一个`PytestUnhandledThreadExceptionWarning`：捕获于`apps/docs/tests/test_engine_formula_edges.py::test_numeric_edge_cases[=POWER(-8,1/3)-#NUM!]`时，`services.py:457`的Timer `fire`向已shutdown的`parse_pool`提交任务，抛出`cannot schedule new futures after shutdown`。捕获节点不证明它创建了Timer；本轮没有单独复现或修复此退出竞态，不将其当作普通第三方弃用警告。

用户要求完成本轮后停止goal。已收取该自然结束结果并保存后续有限修复；不再开新完整容器。当前提交尚未完成修复后的完整门禁，后续如恢复工作，应先处理Timer退出竞态并按现有CI/CD重新验证。所有镜像、已退出容器和日志保留供核对，未停止其他工作负载。
