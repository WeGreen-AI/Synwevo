# 产品行为研究与采用记录

## 账户、组织与管理员 API 台（2026-09-25）

以下为本轮重新查询的官方说明，证据类型均为“官方文档研究”，没有登录竞品实测。
选择的是与现有模型直接有关的行为；实现存在、自动化验收和浏览器验收分开记录在 handoff。

| 来源 | 当前差距与采用理由 | 适配实现与验收入口 |
|---|---|---|
| [Notion 创建与切换空间](https://www.notion.com/help/create-delete-and-switch-workspaces) | 一人多空间需要明确个人/团队归属与退出后影响。旧模型仅有 workspace，缺组织治理。 | 新增独立组织关系，保留 workspace tenant；组织成员不自动读所有空间。个人初始化幂等、创建组织事务化；`test_organizations.py`、`test_public_onboarding.py`、`test_workspace_launch.py`。不复制套餐或域名自动加入。 |
| [Linear 邀请成员](https://linear.app/docs/invite-members) | 目标邮箱、角色与加入位置必须明确，发送/接受状态不能混淆。 | 指定邮箱邀请、组织策略、最小角色、三语言邮件、重发/撤销/拒绝、错误账户拒绝；`test_invites.py`、`test_organization_extras.py`。不采用永久通用链接。 |
| [Microsoft 组织切换](https://support.microsoft.com/en-us/accounts-billing/work-school/switch-organizations-in-your-work-or-school-account-portal) | 门户切换应同步页面作用域，但跨标签页不能把写入目标偷偷改掉。 | 统一选择器、每标签页 workspace 绑定、SSO 携带经服务器验证的目标、拒绝无权空间，保留全局账户；`test_workspace_launch.py` 与实际浏览器检查。不复制其 home organization 账户归属模型。 |
| [Stripe 密钥](https://docs.stripe.com/keys) | 管理员控制台不能成为全空间文档后门，长期秘密不能反复显示。 | `api_platform.manage` 与空间所有者批准分离；一次性秘密、受限资源/scope、轮换与撤销，真实请求追踪；`test_api_platform.py`、`test_api_platform_races.py`、`test_api_platform_pipeline.py`。Webhook 签名秘密与 API 凭据用途分开。 |
| [OWASP 邮箱验证](https://cheatsheetseries.owasp.org/cheatsheets/Email_Validation_and_Verification_Cheat_Sheet.html)、[密码恢复](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html) | 邮箱归一化、待验证权限和一次性链接要防止预先抢注、并发消费与令牌泄漏。 | 保留邮箱点/+、验证前无业务资源、令牌 fragment、可信基础 URL、公开请求反枚举与限流；激活时重新确定口令并失效旧凭据；`test_registration_activation.py`、`test_account_link_privacy.py`。安全修复须以测试通过为准。 |
| [WCAG 2.2](https://www.w3.org/TR/WCAG22/) | 认证表单、菜单、反馈、三语言长文案和窄屏需要键盘/焦点与布局验证。 | 共用组件和 `prefers-reduced-motion`，保留自动填充/粘贴、真实 loading/error 状态、文档 lang 与语言参数；三语言浏览器脚本核验。自动化检查不等于完整 AA 认证。 |

## 上一轮文档与自动化研究（历史）

查询日期：2026-09-24。以下均为官方文档证据，不是对第三方产品的实际试用；没有向第三方上传文档、数据库或截图。旧工作流的研究结果只作为线索，本表引用重新打开的来源。实现与验收分开记录；完整本轮状态见 [handoff](../quality/handoff.md)。

| 方向与来源 | 官方行为与项目差距 | 适配决定 | 实现与验收 |
|---|---|---|---|
| 文档生成：[Plumsail 模板测试](https://plumsail.com/docs/documents/v1.x/user-guide/processes/test-template.html) | 可用表单/JSON 测试数据，预览或下载真实输出，并显示定位错误。Synwevo 原机器预览只返回结构化预览，自动化缺少正式引擎产生的可下载样本。 | 新增原生文件预览；复用 `Views.preview`、`fill_checked` 和正式输出读回校验。阻断错误不交付文件，不采纳忽略错误后留下空值的行为。 | `render_project_preview`；`test_api_preview.py` 比较 UI/API 字节及正式 DOCX 解包内容（只排除 core 元数据），覆盖版本冲突、渲染中撤权、失败输出不入 blob。仅合成样本；复杂真实客户模板仍未验收。 |
| 平台管理：[Notion 角色](https://www.notion.com/help/whos-who-in-a-workspace) | 区分工作空间所有者、成员管理权限及成员；移除成员结束访问。项目需让平台管理员、空间角色、应用资格和导出权限分别生效。 | 保留既有 workspace ID 与角色模型；不复制收费、访客或组织模型。每次业务调用重验成员、空间状态、应用资格及操作权限，危险变更受近期重认证约束。 | `test_gate_access.py`、`test_gate_roles.py`、`test_members_access.py`；账户/门户 UI 使用真实成员接口和逐项结果。大列表与浏览器权限变更仍按当前验收记录核实，不以代码存在算完成。 |
| 操作交互：[Linear 选择与批量操作](https://linear.app/docs/select-issues) | 高亮与选择有区别，支持键盘/Shift 连续选择、Esc 清除及对选中对象执行操作。项目的表格选择与命令动作需保持上下文、权限和焦点一致。 | 采用可见选择、键盘替代和范围选择；按真实当前对象提供动作。中文输入组合与表单输入不触发全局快捷键，不引入技术管理入口。 | Docs 前端 `gridsel`、工作台/选择器及命令菜单；需结合键盘/窄屏/权限浏览器记录评定，构建通过不能替代交互验收。 |
| API：[Stripe 幂等请求](https://docs.stripe.com/api/idempotent_requests) | 同键重试返回同一操作；参数不一致拒绝。项目任务重试选择扩大后必须进入持久指纹，不能把旧键用来恢复不同样本集合。 | `failed_only:true` 纳入指纹；默认值保留历史指纹以兼容旧客户端。明确支持幂等的操作，不承诺所有 POST 都支持。密钥/Webhook 等不支持的操作明确拒绝该请求头。 | `test_retry_failed_only.py`：成功/失败/取消混合批次仅重做失败项，保留成功产物及执行次数；同键不同选择 409。`test_api_contract_idempotency.py` 验证实际操作。未声称采用 Stripe 的全部缓存与有效期语义。 |
| 自动化：[n8n HTTP Request](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/) | 标准节点支持独立凭据、multipart 二进制文件、文件响应、分页；cURL 导入默认会把值变成字符串。原验收不能以导出 JSON 代替运行。 | 使用 n8n 自有凭据与二进制机制；重试参数保留 JSON 布尔类型，错误字符串拒绝而不静默转换。下载文件逐份核验内容与摘要。 | 本机隔离 n8n 2.40.5 / Node 24 已跑通生成轮询链路，12 份合成 DOCX 及 ZIP 验证；服务授权及签名接收工作流另记实际结果。Make 云端未运行，不建立隧道。 |

安全协议补充：[RFC 9700 §4.14.2](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.14.2) 的刷新轮换/重放防护用于核对现有令牌族。Synwevo 已把消费旧刷新令牌和发行继任令牌放进同一事务；新增发行失败回滚与并发撤销回归，修改前失败、修改后通过。此处是适用控制的实现证据，不是认证声明。

上一轮未扩展收费、签署、应用市场或任意远程 URL 文档输入。本轮明确授权的平台管理员 API 台替代旧的技术 UI 禁令；普通用户仍无控制台入口。新增行为必须有真实服务、授权和可复现验收支持。
