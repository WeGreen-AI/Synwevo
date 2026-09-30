# API-5b 与 API-7：目录写入、复制授权与契约

本记录区分本地验收与线上发布。实现设计与升级/回滚约束见
[目录写入](../platform/api-directory-writes.md)，当前能力地图见
[API 覆盖表](../platform/api-coverage.md)。所有实跑使用隔离临时数据、合成账户与 file 邮件出站箱。
未使用生产 Cookie、真实成员或业务文档。

## 本批行为

- Accounts 独立提供 `/api/workspace/v1` 的 3 个读操作和 5 个写操作。写入仅接受当前有效、空间所有者明确批准的服务账户及 `workspace.members.write`，只能处理 viewer/editor；成员变更需传刚读取的 `expected_role`。
- 新邀请保留调用应用和原审批编号。邮件、通知、网页显示应用操作者；撤回原审批或停用客户端后邀请不可接受，重新批准不会复活旧邀请。浏览器也不可将机器邀请提升为 owner/admin。
- 邀请创建/重发与幂等结果、出站邮件、通知、审计同事务；撤销/改角色/移除复用现有治理层。撤权安全日志在数据库提交前 fsync；其恢复语义是保守撤权，不宣称文件与数据库跨存储原子提交。
- 复制申请仅使用本人 OAuth 委托，审批留在真实网页。执行时重读申请人权限、两侧应用资格与原委托；撤回或停用客户端可阻止已获批、未执行的申请。执行租约与结果提交受版本保护；遗留 running 状态先核对已有副本，避免重复复制，歧义状态保留数据并明确失败。
- 目录与复制路由有显式请求/响应模型，拒绝未知写入字段。目录 OpenAPI 可由独立 Accounts CLI 导出，不初始化 Store；管理员契约页聚合两份契约，不暴露内部账户、管理或 OIDC 路由。
- 两种 SDK 新增独立 `WorkspaceClient`，默认 Accounts 正式地址，使用专门环境变量及目录受众；复用传输实现，未继承 Docs 业务方法。没有发布公共软件包。

## 可复核验收

| 检查 | 结果与证据 |
|---|---|
| Python SDK 与 CLI | 新装包单元测试 25 项通过；SDK/CLI 各生成 12 份上传模板 DOCX 及 24 份内置产物（12 DOCX + 12 XLSX），另核对 4 个内置与 3 个上传模板预览；复制 6 操作实跑 |
| TypeScript SDK 与 CLI | 单元测试 10 项通过，严格类型检查通过；SDK/CLI 各生成 12 份上传模板 DOCX；内置流程核对 12 DOCX + 12 XLSX 与 2 预览；复制 6 操作实跑 |
| 双 SDK 目录实跑 | 真正独立的 Accounts 服务；管理员申请 → 所有者批准 → 签发；两种 SDK 均跑通 8 操作，36 次 HTTP 调用、4 封 file 出站邮件、10 条机器写入审计；旧角色 409、重发冷却 429/Retry-After 60 均断言 |
| 契约覆盖 | Docs：100 操作、98 SDK 映射、64 CLI 映射、56 种成功操作观测；目录：8/8 双 SDK 映射及实际观测。映射与实跑为不同指标 |
| OpenAPI 审查 | 两份 3.1.0 文档通过 openapi-spec-validator 0.9.0；42 个集成用例采集 170 个真实响应（含 3 个 RFC 9457），覆盖 57 组操作/状态，全部符合 schema |
| 管理台真实浏览器 | 真实登录与重新验证；申请 → 390px 手机批准 → 手机签发 → 当前令牌调用 200 → 所有者撤回 → 同一令牌 401；简繁英目录页各 8 操作，scope 正确；无横向溢出和页面脚本错误；签发截图遮挡秘密 |
| 基础检查 | 边界、品牌、设计、组件名、22 个 Logo 衍生文件、四外壳 typecheck/build、前端测试及 8 项 infra 单元测试通过 |
| 首轮全量 Python | 完整执行后 4 项失败：3 个 Push 回环测试和 1 个运行期间改动造成的目录导出漂移；目录当前代码重跑 3/3 通过。Push 原样复现 3 failed / 1 passed，仅隔离代理后 4/4 通过；将 NO_PROXY 限定在测试 fixture 后再次 4/4 通过，未改变生产网络行为。不能称首轮全量通过 |
| 扫描退出竞争 | 首轮日志暴露晚到 Timer 向已关闭解析池提交任务；3 个确定性用例修复前全部失败、修复后通过；扫描/源生命周期/进程清理共 18 项通过，验证时线程异常警告按 error 处理。修复单独提交为 `b431b54` |
| 发布镜像 | 已有候选镜像从锁文件构建成功（本地 Linux arm64）；最终固定源码镜像的隔离全量验证待执行。GitHub 的 Linux amd64 发布门槛仍需工作流实际运行 |

可重复入口：`tests/integration/test_workspace_directory_writes.py`、`test_workspace_contract.py`、
`test_api_copy_requests.py`，以及 `tests/integration/sdk_acceptance/workspace_run.py`。
SDK 命令与实跑参数见两个 SDK 的 README。

临时证据（不提交夹具数据或凭据）：

- `/private/tmp/synwevo-api-platform-20260930/`：全量回归、镜像日志、浏览器结果与已遮挡截图。
- `/private/tmp/synwevo-api-sdk-python-20260930-r3/acceptance.json`：Docs SDK 实跑。
- `/private/tmp/synwevo-workspace-sdk-20260930-r3/acceptance.json`、`operation-coverage.json`：目录双 SDK 实跑。
- `/private/tmp/synwevo-openapi-review.wcrehf/instance-report.json`：响应实例验证。

## 发布与尚未闭环的范围

2026-09-30 只读核对生产容器：accounts、docs、portal、messages 均运行
`aadcb9e839c3c13386f1831a942e8449852dab15`，均 healthy。五个正式入口用 curl 读取 `/readyz`
均为 ready（Python urllib 初次请求为 403，未以此推断应用宕机）。本批仍未部署。
[Actions 36569205283](https://github.com/WeGreen-AI/Synwevo/actions/runs/36569205283) 的 verify
因账户账单/支出限制未启动，deploy 跳过。保持现有发布门槛，未通过手工部署绕过它。

剩余 API-5 评论/提及、通知、Message 机器身份，以及 API-7 旧响应模型、兼容差异视图继续在能力表跟踪。
API-3 管理机器身份依赖管理员专项 B1-2；本批不伪造该依赖，也不赋予业务服务账户平台管理权。
