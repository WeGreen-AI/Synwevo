# Admin 容器内存门禁排查（2026-09-30）

冻结B1提交dde47cc的完整验证镜像在3GB/2CPU、断网限制下，于约83%被OOM终止（137）。没有pytest汇总，不能算通过。Docker随后不可用，Colima只读状态确认停止；恢复原配置环境后将重跑完整门禁，不提高资源限制。

临时工厂的同条件小实验确认两条保留链：被动BridgeWorker无条件向全局_wakers登记bound method，但未登记后台线程，stop_all无法释放；锁定FastAPI0.141.1的三个callable分类LRU以_CallIdentity强引用路由闭包，闭包保留整个应用。后者每缓存有4096上限，是大批临时工厂的延迟回收，不能称为无界生产泄漏。

修复：只在BridgeWorker真正启动时登记waker；根pytest收尾先停止全部worker，再清除三个可重新计算的FastAPI分类缓存并GC，若线程仍活着则报错。生产缓存和服务资源限制保持不变。未减少测试范围或放宽业务断言。

原回归实际为F..（被动worker释放失败）；修复后23项代表回归通过、7条第三方弃用警告、9.11秒，覆盖四套件代表、手动run_once、真实后台唤醒和重复stop，以及all工厂运行/被动 × 完成/未运行lifespan的矩阵。模块边界和diff检查通过。

同6工厂小实验线程始终1：原场景RSS增长94448KiB、traced约37.55MB并保留API/Store；修复后RSS增长7376KiB、traced末轮405345字节，每轮API/Store/bridge/SQLite连接与登记worker均归零。原始/因果对照`/private/tmp/synwevo-bridge-cache-before.jsonl`，修复后`/private/tmp/synwevo-factory-teardown-after.jsonl`，测试`/private/tmp/synwevo-factory-lifecycle-regression.log`。

这证明保留缺陷及修复效果，不证明其是OOM的唯一来源。最终结论仍取决于同3GB全量容器结果。
