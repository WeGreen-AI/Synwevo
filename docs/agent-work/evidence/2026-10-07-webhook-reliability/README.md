# 2026-10-07 Webhook重试与附件清理

产品提交8ba2956750c2080d300fa17523bcc391e85cae40。verification.json保留本地验证时源码哈希，source-binding.json将23份最终代码/测试与准确Git blob绑定；ci-verification.json及release-status.json记录本次CI/CD成功，production-after-deploy.json和public-after-deploy.json记录14:39Z独立上线检查。

本地验证：受影响Webhook/API管理187项、附件与升级13项、生成API契约9项通过；Python SDK45项、TypeScript SDK34项及类型通过；根目录类型/构建、前端500项和六项仓库守卫通过。无新增生产依赖。

browser-verification.json记录真实隔离服务三语言重认证→服务端受理后丢响应→重试只生成一条投递，并证明新动作生成新键；D4默认24小时、显式零、取消重置同时通过。键盘、减少动态效果及1280/390布局核对；三个确认页截图由协调者目视检查。投递worker保持停止，未向外部接收端发请求；清理记录证明浏览器和临时服务已结束。

attachment-scan-before/after.json使用合成数据、SQLite3.53.2、单进程非并发、每场景三次回滚计时。30万历史行没有过期项时，新增Store索引后的中位0.017ms；150条过期项删除100条中位0.390ms。查询计划无历史全表扫描/临时排序；不将本地数字当作生产SLO。Store升级测试确认原记录不变、重复启动、完整性与真实清理通过；既有生产备份机制在正式发布前执行。

限制：Chromium；SDK使用明确loopback故障夹具，不替代真实HTTP服务端/浏览器证据；新版SDK需支持幂等重投的服务端，旧服务端拒绝时无去键fallback；幂等默认24小时，原收据需用GET核对当前状态。正式Agent/充值开放仍受DEEPSEEK_ENABLEMENT.md中的配置与商户条件阻塞。

完整CI：5328 passed/5 skipped/242 warnings；前端500、Python SDK47及38子测试、TS SDK34，类型/构建/六守卫等通过。五服务健康、四应用OCI与8ba一致、五域页面与JS通过；备份存在不等于恢复演练。正式Agent配置推进另见../2026-10-08-agent-activation，不能用本次代码发布替代开放验收。
