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

## 后续完整门禁

先完成 Linux 的协同整份测试及全部直接使用上述 double 的适用文件回归，再由根代理审阅并提交。以包含这些修复的新完整 commit SHA 重新 `git archive`，复刻 0700 私有解包，制作按该 SHA 命名的独立 runtime/verify 镜像；不使用未提交工作树、不把 d26782a 标签当新源码。先跑同样 UID 1000/断网/read-only runtime 工厂 smoke，再在同 3 GiB/2 CPU、断网条件下运行默认完整四套件。记录新 image IDs、真实 pytest 汇总/skip 原因、退出状态、运行时间及采样内存。此次挂起记录保留，只有新冻结完整门禁实际完成才能更新为通过。

Linux精确回归已完成：在冻结d26782a镜像中只覆盖上述5个修复后的测试文件，11个适用文件共188 passed、1 skipped、2 warnings，152.70秒、退出0。包括完整71项表格协同、两模块scanner double生命周期、扫描/补扫/加密/文件事件、消息附件与跨模块共享字节/加密成员导入。唯一skip为未提供真实`CLAMD_TEST_ADDRESS`；显式double不能替代真实扫描器验证。日志`/private/tmp/synwevo-admin-b2-fixture-after.log`。模块边界521个Python/254个前端文件及diff检查通过；根代理独立审查保留原协议断言和有界线程清理。该覆盖挂载只用于定位和窄回归，仍需新提交冻结完整门禁。
