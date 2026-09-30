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
| 发布镜像 | 固定 `b8ee007` 从锁文件构建成功（本地 Linux arm64），非 root、只读根目录、无网络冒烟通过；全量套件收集 2 865 项，执行到约 72% 时 Colima 在 15:58 收到 SIGTERM，容器退出 125，验证未完成。GitHub 的 Linux amd64 发布门槛仍需工作流实际运行 |

可重复入口：`tests/integration/test_workspace_directory_writes.py`、`test_workspace_contract.py`、
`test_api_copy_requests.py`，以及 `tests/integration/sdk_acceptance/workspace_run.py`。
SDK 命令与实跑参数见两个 SDK 的 README。

临时证据（不提交夹具数据或凭据）：

- `/private/tmp/synwevo-api-platform-20260930/`：全量回归、镜像日志、浏览器结果与已遮挡截图。
- `/private/tmp/synwevo-api-sdk-python-20260930-r3/acceptance.json`：Docs SDK 实跑。
- `/private/tmp/synwevo-workspace-sdk-20260930-r3/acceptance.json`、`operation-coverage.json`：目录双 SDK 实跑。
- `/private/tmp/synwevo-openapi-review.wcrehf/instance-report.json`：响应实例验证。

## API-7 任务契约与 SDK 续批

9 个现有任务操作（提交、项目任务列表、详情、逐项结果、运行记录、事件、取消、重试、产物）
新增明确响应模型，不改变任务调度逻辑。输出保留历史 null、稀疏计数/选项、仅重放时出现的标记，
并区分任务详情与列表计数、逐项产物与平铺产物。未知内部顶层字段被过滤，公开的可扩展 JSON 诊断仍保留。
Python 的公开 TypedDict 和 TypeScript 声明与此对应，SDK 运行时仍返回原字典/对象。

| 检查 | 结果与证据 |
|---|---|
| 合同兼容 | `test_api_job_models.py` 用真实 HTTP 检查历史 null、缺省字段、取消状态、重放、分页、产物及内部字段过滤。主代理发现显式 null 计数会被初版整数模型拒绝，先复现失败，再同时修正服务端与双 SDK 类型；最终模型/契约/管理目录三语言 13 项通过 |
| 真实失败重试 | `tests/integration/sdk_acceptance/job_types_run.py` 从安装包运行 SDK，对冻结后的合成模板暂时移开 blob，11 项真实生成失败；按原摘要恢复后，同一任务 run 2 重试成功，11 份 DOCX 的内容及 SHA-256 均核对；一个跳过项保持原样，冻结快照与数据库一致。脚本 finally 恢复夹具文件，不使用生产内容或伪造生成器 |
| 全部任务操作 | 专项 HTTP 观测覆盖全部 9 个任务操作，包括 offset/cursor、幂等重放、取消、事件及两种产物形状；本次共观测 18 个成功操作，是独立子集，不能替换前批联合 56 个成功操作统计 |
| SDK | 已安装 Python SDK 27/27 单元测试、mypy 1.18.2 strict；TypeScript 10/10 与严格类型检查。主代理的 TS 回环接收测试首次受沙箱 EPERM 阻止，获准启动本机回环测试服务后重跑 10/10 |
| OpenAPI | 当前 Docs 与目录导出均通过 openapi-spec-validator 0.9.0；任务契约采集 61 个真实响应，覆盖 9 个成功操作及 403/404/409；另行独立 HTTP 审查 23 个响应。最终 null 计数兼容修正后的契约再次通过规范校验。Docs 未约束的 2xx JSON schema 由 73 降至 64，尚未全量类型化 |
| 共享文件接纳 | 镜像中的 2 个恶意 ZIP 性能用例在 tracemalloc 下分别约 6.36/8.07 秒，超过原有 3 秒预算；以 C 层签名计数及正则预筛减去普通头的 Python 分配，保持 700 000 头拒绝上限与原有标记/清单判定。同一旧镜像挂载当前实现后完整加密文件套件 45 passed / 1 skipped；新增全部 256 种低位标记组合及成员导入/Message 接纳回归共 7 项在主机通过，未放宽时间或内存预算 |
| 验证镜像依赖 | `test_file_events::test_the_sdk_knows_the_server_catalogue` 暴露 SDK 只装在 CI 主机、未装进隔离验证镜像。`Dockerfile.verify` 补装仓库内真实 SDK，生产镜像不变；重建尝试时 Docker 已停止，尚不能宣称此修复通过容器验收 |

续批证据：`/private/tmp/synwevo-api7-jobs-sdk-20260930-r3/acceptance.json` 与 `sdk/responses.json`；
主验证目录里的 `api7-sdk-*.log`、`api7-ts-root-recheck.log`、`job-contract-final.log`、
`encrypted-optimized-container.log`、`encrypted-cross-app-local.log`、`docker-pytest-b8ee007.log`。
主机真实表格客户端套件另有 98 passed / 6 warnings，见 `sheet-clients-final.log`。

继续验证需先恢复原 Colima profile（不重建数据），从最终提交归档构建 runtime 与 verify 两个镜像，
再运行 `.github/workflows/deploy.yml` 的容器全量检查和发布门槛。当前已询问运行环境恢复，未收到答复；
没有将中断后的剩余用例视为通过，也没有自动重启可能由其他工作停止的环境。

## 资源列表游标续批

资源列表游标续批（在任务契约提交 `4b74796` 后）：原游标的篡改位置、跨工作空间复用两用例均以
200 响应复现失败，原查询仍有租户过滤，未据此声称泄露其他空间数据。新实现使用现有根密钥派生的
独立 HMAC 签名，24 小时有效，绑定空间、列表、对象与筛选，组织共享模板额外绑定组织。
分页与任务模型 16 项通过，包含独立 Python 进程启动应用后续读、凭据轮换后续读、撤销后拒绝、过期、
旧格式、异常输入和跨对象范围；组织模板另验证同组织不同空间与筛选不可混用。
管理台契约说明同步简繁英，OpenAPI 3.1.0 再次校验通过。组织模板/契约/三语言复核 11 项通过，
账户前端 typecheck、设计与组件名检查通过。
已安装 SDK 在 `/private/tmp/synwevo-api7-cursor-sdk-20260930/acceptance.json` 记录新的真实分页与
11 个失败项恢复、产物读回验收。`cursor-before.log`、`cursor-after.log`、`cursor-contract.log`、
`cursor-sdk.log` 在主验证目录；SDK 日志中的合成 blob 缺失异常是主动故障场景，随后成功恢复。
升级/回滚需要重开旧资源列表游标，offset 兼容保留；不会静默重新迭代而重复处理。事件 feed 和 Webhook
投递的长期检查点没有改成短期列表游标。此批仍未经过完整容器回归或部署。

## 断点上传契约续批

5 个上传操作共用明确的 `UploadView`，保留 open/complete/consumed/aborted/expired、显式 null 的
sha256/purpose、完整收到字节数和每次都有的 upload_url。Python SDK 导出 `Upload`、`UploadStatus`、
`UploadPurpose`，6 个方法返回该静态类型；TypeScript 修正 upload_url 的必填性。没有更改状态机、
扫描/文件接纳规则、SQL、迁移或上传重试行为。

实际响应审查另外发现此前未写进契约的 409、410、413、416，已按各路由真实路径补齐，复用统一错误信封
及 RFC 9457 响应。最终 134 个 HTTP 响应覆盖 23 组操作/状态、5 个上传状态及两种错误媒体格式，
全部符合 OpenAPI 3.1.0。包括故意注入存储故障的 500，并未将其误记为成功上传。
当前仍有 59 条成功 JSON 响应未明确建模，未声称全部契约已完成。

上传/耐久/重放/契约共 33 项通过，另外 1 项三语言目录检查发现新增 HTTP 默认文案缺译，补齐后该项复核通过。
已安装 Python SDK 27/27、mypy strict、TypeScript strict 与账户前端 typecheck 通过。
真实 SDK 续验改为 2 KiB 分片上传来源和模板，另建部分上传并取消，覆盖全部 5 个上传操作；随后
同一真实任务的 11 个失败项恢复与 11 份产物内容/摘要验证继续通过。

证据：主验证目录 `uploads-schema-report.json`、`uploads-final.log`、`uploads-i18n-final.log`、
`uploads-sdk-unit.log`、`uploads-sdk-mypy.log`、`uploads-sdk-typescript.log`；真实 SDK 结果在
`/private/tmp/synwevo-api7-uploads-sdk-20260930/acceptance.json`。SDK 上传状态和产物响应只包含合成夹具。

## 固定提交主机全量回归

固定 `14b9811`（目录、复制、任务模型、签名游标、上传契约及验证修复已提交），
全程没有改动源码或导出的契约。命令：

```sh
.venv/bin/python -m pytest -o addopts='--import-mode=importlib' -q --tb=short
npm run typecheck
npm run build
.venv/bin/python scripts/check_boundaries.py
.venv/bin/python scripts/check_brand.py
.venv/bin/python scripts/check_design.py
.venv/bin/python scripts/check_component_names.py
.venv/bin/python scripts/brand_assets.py --check
```

主机 Python 3.14：**2 973 passed / 2 skipped / 239 warnings，1111.67 秒**，退出码 0。
警告为 SWIG、Pillow 像素读取、AnyIO 别名及测试客户端逐请求 Cookie 的弃用提示；没有将警告隐藏或声称零警告。
四个前端的类型检查与生产构建、五项仓库检查全部退出 0。证据在主验证目录
`pytest-final-14b9811.log`、`final-typecheck.log`、`final-build.log`、`final-boundaries.log`、
`final-brand.log`、`final-design.log`、`final-components.log`、`final-logo.log`。
这确认本机固定提交的完整回归，不替代生产 Python 3.12、Linux 镜像全量验证或 GitHub CI。

## 发布与尚未闭环的范围

2026-09-30 只读核对生产容器：accounts、docs、portal、messages 均运行
`aadcb9e839c3c13386f1831a942e8449852dab15`，均 healthy。五个正式入口用 curl 读取 `/readyz`
均为 ready（Python urllib 初次请求为 403，未以此推断应用宕机）。本批仍未部署。
[Actions 36569205283](https://github.com/WeGreen-AI/Synwevo/actions/runs/36569205283) 的 verify
因账户账单/支出限制未启动，deploy 跳过。保持现有发布门槛，未通过手工部署绕过它。

剩余 API-5 评论/提及、通知、Message 机器身份，以及 API-7 旧响应模型、兼容差异视图继续在能力表跟踪。
API-3 管理机器身份依赖管理员专项 B1-2；本批不伪造该依赖，也不赋予业务服务账户平台管理权。
