# 当前产品轮交付记录

## 2026-09-25 统一白蓝设计系统与多种登录方式（本轮，已部署）

**目标**：平台、账户、Docs、平台后台与 API 台形成一套白蓝浅色体系；补齐「保持登录」、邮箱验证码登录与
外部身份提供商（Google / Microsoft 等 OpenID）登录、关联与解绑。执行代理为 Claude Code。
本节先于下方历史记录；部署状态以 [deployment.md](deployment.md) 为准。

### 已实现（本机实跑）

| 范围 | 实际行为与证据 |
|---|---|
| 设计令牌 | `packages/ui/src/styles.css` 收敛为白蓝基线（`--bg #F8FAFC`、`--accent #2563EB`、hover `#1D4ED8`、pressed `#1E40AF`、`--accent-weak #EFF6FF`、`--text #0F172A`、`--text-2 #475569`、`--line #E2E8F0`），控件边界 `--line-strong #8894A8`（3.1:1），对比度写在令牌旁；深色主题退役，`data-theme`/系统深色不再改变外壳（E2E G 流程验证）。 |
| 门户 | `portal.css` 从独立绿色主题迁移到共享令牌与共享 `.btn`；公开首页、登录后门户、状态页；品牌标记统一为 `Brand`。桌面 1024 与 375 窄屏截图检查通过。 |
| 账户 / Docs 外壳 | 顶栏、登录框架、Docs 三种页面共用 `Brand`/`BrandMark`；API 台去掉兜底色；Toast 改为白卡+状态条；内联字面色改令牌或 `color-mix()`；三个 `index.html` 统一 `lang`、`theme-color`、`color-scheme`。 |
| 守卫 | 新增 `scripts/check_design.py`（CI 与验证清单已接入）：共享令牌外的字面颜色必须归入有理由的例外；禁止深色主题、私有色板、自选字体、`@media` 外 `!important`。当前通过。 |
| 保持登录 | Store v7 `sessions.persistent`；勾选后中央会话空闲 14 天（`SYNWEVO_SESSION_PERSISTENT_IDLE_S`）、绝对 30 天不变、两个 Cookie 带 `Max-Age`；平台管理员忽略；只接受 JSON `true`；设备列表标记。契约见 sessions.md §2.1。`test_remember_me.py` 7 项。 |
| 邮箱验证码登录 | `/api/auth/login/email/start|verify`：6 位码、10 分钟、单次、5 次错误作废、同形响应防枚举、发出后 outbox 脱敏；启用 TOTP 仍走第二步（`amr=email+totp`）；管理员未启用 TOTP 不发码。三语言邮件模板。`test_email_code_login.py` 9 项。 |
| 外部身份提供商 | `synwevo_identity/external.py`：发现文档、PKCE S256、服务端 state/nonce、id_token 全量校验（签名/iss/aud/azp/nonce/exp/iat/sub，Microsoft 多租户模板）、按 `(provider, subject)` 关联、已验证邮箱匹配只生成关联要约、注册策略生效、管理员需 TOTP、关联/解绑需 strict 重认证且不能移除最后一种登录方式、安全通知。配置不完整拒绝启动；未配置不显示。`test_external_identity.py` 16 项（测试替身提供商，含篡改用例）。 |
| 账户内改口令 | `POST /api/auth/password`：当前口令 + 新口令；本会话保留、其他会话与令牌失效、安全通知、审计；账户页「邮箱与口令」内置表单，邮箱重置保留为备用。`test_password_change.py` 8 项。 |
| 登录页 / 账户页 | 登录：保持登录复选框、「其他登录方式」（安全密钥 / 邮箱验证码 / 已配置提供商）、外部登录错误解释、关联要约表单、片段续接 MFA；账户：外部登录方式卡片（关联/解绑）、设备列表登录方式与「保持登录」标记。三语言资源补齐，账户外壳 19 项校验通过。 |
| 文档 | `docs/design.md` 重写为平台设计系统；`sessions.md` §2.1/§4.3–4.5、`permissions.md` 契约表、`.env.example`、README/TESTING/AGENTS/CLAUDE 验证命令。 |

### 本轮运行证据（本机，2026-09-25）

| 运行范围 | 结果 |
|---|---|
| `services/identity/tests` + `tests/integration` | **1104 passed / 0 failed**（296s；含本轮新增 40 项与 v7 迁移测试） |
| `apps/docs/tests` | 全部通过：840 项收集、0 失败、1 项既有 LICENSE 跳过（退出码 0） |
| 前端 | typecheck/build 三外壳通过；UI 11、auth-client 31、Docs 45、账户外壳 19 项通过 |
| 守卫 | `check_boundaries` / `check_brand` / `check_design` 通过 |
| Docs 浏览器 E2E | `ui_e2e.py`（主链路：上传→模板→字段→映射→样本→预览→生成 100 份→下载核对）与 `ui_e2e_keyboard.py`（命令面板、列表键盘、单一主题与纸面、布局记忆）在各自隔离实例上通过。`ui_e2e.py` 中一处早已过时的对话框文案断言按当前界面更正。 |
| 隔离实例浏览器实跑（317xx，`instance=test`，file 邮件，本机 OpenID 替身 `idp_double.py`） | 引导管理员→验证邮件；公开注册 Bob→邮件激活；勾选保持登录登录（会话 `persistent=1`、空闲 336h）；账户页关联 Test IdP→替身同意页→回到账户页显示关联；登出后「以 Test IdP 登入」直接登录（`amr=idp:testidp`）；登出后邮箱验证码登录（outbox 发出后不再含验证码）；门户与 Docs 经统一登录静默进入。 |

### 边界与未完成

- 外部提供商只用**测试替身**验证了 Synwevo 一侧的协议与关联规则；Google / Microsoft 的实际应用注册、回调与联调需要运营方在提供商处登记并填入凭据后才能完成，生产未配置任何提供商（登录页不显示按钮）。
- 邮箱验证码在生产依赖已配置的 SMTP；本机只证明 file 投递。
- 仍未做：本轮未重做 Docs 引擎、API 控制台、组织/空间流程的功能性变更；它们沿用上一轮的实现与验证，本轮只改外壳样式并跑通既有回归与 E2E。
- 部署：`4ecdfb0` 与 `dbd4c94` 已依次经 CI 验证并部署（备份 `20260925T042037Z`、`20260925T044056Z`），线上匿名冒烟见 [deployment.md](deployment.md)；线上未做登录态流程验收。
- 本机 317xx 隔离实例（`instance=test`，数据在会话 scratchpad）与 31780 端口的 OpenID 替身仍在运行供查看；停止：在同一环境变量下 `python scripts/platform.py down`，替身进程直接结束即可。

---

**部署接续（2026-09-25）**：用户已在本机交付之后明确授权部署，主页已更正为工作流程自动化平台，
Docs 单独列为应用。`main` 源码 `1a48359` 已通过完整 CI 并部署；1893 项运行环境测试通过、2 项明确跳过。
线上注册已开放，原有账户/文档/密钥和 57 个 blob 核验保留。发布、浏览器与 SMTP 验证见 [deployment.md](deployment.md)。
下面的“未部署”描述是本机交付 `4caea87` 当时的边界，不是当前部署进展。

更新于 2026-09-25。范围：账户自助、组织/工作空间、管理员 API 台、公共门户及简体中文、繁體中文、English。
从干净 `main` 的 `a20537bb642e0f3a54d4241a371000fcd09e8515` 继续；本文件与实现一并形成本地交付提交，最终提交以 `git log -1` 为准。
**未推送、未部署、未访问 VPS、未改 DNS、未发送外部测试邮件。** `main` push 会触发生产工作流，因此本轮不推送。
上轮生产及 SMTP 记录仍见 [deployment.md](deployment.md)，不计入本轮验证。

## 2026-09-25 导航、弹层与移动端轮（最新）

范围与状态见 [progress.md](progress.md) 同名节；缺陷—根因—修复—验证表见 [issues.md](issues.md)。
运行入口：隔离实例配方见 [verification.md](verification.md) §16；`services/identity/scripts/ui_e2e_shell.py`
（账户/门户/Docs 外壳回归，Chromium + WebKit）与 `apps/docs/scripts/ui_e2e_mobile.py`（390×844 主链路）。

接续点（需要人或外部条件）：

1. **Google / Microsoft**：按 [operations.md](../operations.md) §7.1 登记两个 Web 应用（回调逐字
   `https://accounts.wegreen.ltd/auth/external/google/callback`、`…/microsoft/callback`，scopes `openid email profile`），
   在 VPS `/opt/synwevo/runtime.env` 追加变量，`docker compose restart accounts`，然后在后台「登录方式」页点「立即检查连接」，
   再用受控账户走登录页→提供商→账户页。代理不能替代；secret 不进聊天。
2. **生产管理员的 TOTP**：本人登录账户页 → 安全因素 → 绑定：输入口令（近期重认证）→ 扫码或手动录入 → 输入一次
   动态码确认 → 保存恢复码。在这之前，用邮箱验证码或外部提供商登录得到的管理员会话会看到「当前登录只验证了一个
   因素」的提示，输入口令即可解锁管理页；口令登录本身不受影响。
3. **真机**：verification.md §16 的 iOS Safari / Android Chrome 清单；**真实外部邮箱**与**真实业务样本**同节。

## 实现与已关闭缺口

| 范围 | 实际行为与验证 |
|---|---|
| 注册/首次使用 | 本机开放注册，pending 无业务权限；验证邮件绑定目标邮箱，显式激活并设置最终口令，避免抢注口令继承。个人空间事务性幂等；个人/组织/邀请意图接续到正确空间。三语言真实注册和 SSO 通过。 |
| 账户中心 | 显示名、安全头像、语言/时区、邮箱变更、口令、安全因素、会话、个人导出、删除与恢复；邮箱变更链接支持未登录跨认证接续。真实 Chrome 6 项账户流程及三语言 18 项安全检查通过。 |
| 组织/空间 | 独立组织成员和空间成员；创建、邀请/重发/撤销/拒绝、角色、所有权转移、生命周期、头像、限额和审计。组织身份不自动取得全部业务数据。注册/邀请/个人/组织/空间切换浏览器 19 项通过。 |
| 平台治理 | 真实用户/组织/空间治理、注册策略、邮件队列元数据与受控重试、审计；治理权不赋予文档读取权。高风险操作要求近期验证、理由、事务内权限复核。 |
| 管理员 API 台 | `api_platform.manage` 只给平台管理员；空间所有者批准与凭据管理分离。控制台、真实契约、受限调试、凭据轮换/撤销、Webhook 和调用记录复用既有服务层。实际 UI 批准签发→已安装 SDK→真实 HTTP→12 份 DOCX→轮换/撤销拒绝→调用记录关联全部通过。 |
| 三语言/网站 | 共用语言核心、分模块资源、账户偏好持久化、可信跨源语言接续、邮件入队固定语言；公共首页、门户、账户/组织、Docs、后台和 API 台接入。用户文档和机器枚举不翻译。资源/参数完整性与关键浏览器流程通过。 |
| Docs 保持可用 | 沿用引擎、模板、映射、格式、公式和持久任务。真实三语言注册→个人空间→门户→Docs→上传/映射/预览/批量生成，9 份 XLSX 和 3 份预览读回原文、数值、公式及缓存值通过。 |
| 恢复/迁移 | Store v6 兼容迁移不重建用户/空间/密钥；组织撤权 journal、账户终止删除重放、外发隔离。真实归档恢复、认领状态/ID/文件哈希保留通过。 |

独立检查发现并关闭：激活前抢注与并发登录、forgot 并发重复投递、注销/重置后的未消费授权码兑换、
RP 设备撤销未关联中央会话、晚到 RP 回调重建已撤销会话、终止删除在旧备份中复活认证因素、
管理员调试中途降权、旧请求覆盖新账户/空间、旧 401 清除新会话、晚到 Blob 交付旧空间内容、
初始化后导航被 refresh 打断、Uvicorn 访问日志记录授权码/查询个人信息。针对性回归与真实浏览器复验分开保留。

## 本机运行入口与隔离范围

- 门户：`http://portal.localhost:28769`
- 账户：`http://accounts.localhost:28770/login`
- Docs：`http://docweave.localhost:28765`
- 数据：`/private/tmp/synwevo-platform-round/live/data`；本地邮件：`/private/tmp/synwevo-platform-round/live/mail`。

287xx 三进程保留供本机查看，只有本任务合成账户/文档，未使用仓库真实 `data/`。此实例初始化未标记 test，
没有事后改标记；破坏性账户/邀请浏览器验收另用从初始化就标记 test 的隔离实例。
297xx、317xx 验收实例已停止；307xx 干净环境启动实例已自动停止。数据/证据保留，不做删除清理。
PID 以实时 `status` 为准，不能按旧报告 PID 结束进程。

在仓库根维护当前 287xx 实例：

```sh
export SYNWEVO_DATA_DIR=/private/tmp/synwevo-platform-round/live/data
export SYNWEVO_MAIL_MODE=file
export SYNWEVO_MAIL_DIR=/private/tmp/synwevo-platform-round/live/mail
export SYNWEVO_REGISTRATION=open
export SYNWEVO_ACCOUNTS_PORT=28770 SYNWEVO_PORTAL_PORT=28769 SYNWEVO_DOCS_PORT=28765
.venv/bin/python scripts/platform.py status
# 需要重新启动当前实例时：
.venv/bin/python scripts/platform.py restart
# 停止当前实例而保留其数据：
.venv/bin/python scripts/platform.py down
```

完整安装、创建新隔离实例及邮件安全说明见 [operations.md](../operations.md)。`.eml` 包含短期令牌，
只在 0700/0600 私有路径中读取，不写入聊天或日志。没有默认管理员口令，初始管理员沿用交互 CLI 引导。

## 本轮运行证据

证据根 `/private/tmp/synwevo-platform-round/`；不同组有重叠，不能相加为一个总测试数量。
命令入口见 [TESTING.md](../TESTING.md)，代码地图/逐文件 SHA 与阅读程度见
[codebase-review.md](../engineering/codebase-review.md) 和 [review-coverage.json](../engineering/review-coverage.json)。

| 运行范围 | 最终结果、证据和边界 |
|---|---|
| 完整账户+集成套件 | `identity-integration-r2.log/xml`：1055 passed、0 failed、0 skipped，264.29s。首次 1044p/6f 保留在 `identity-integration-final.*`；五项旧 audience 夹具及设备计数缺陷修复后重新全跑。 |
| Docs 完整套件与修复 | `docs-suite.log/xml` 初次 837p/2f/1skip；两项旧夹具适配真实服务登记/上传失败保留语义，`docs-regressions.log/xml` 37p。原有内容/SHA断言保留，未删断言或跳过失败；没有宣称重跑了无关全套。历史 skip 是未选择 LICENSE，见下文。 |
| 前端常规测试 | 根 `npm test --workspaces --if-present`：UI 11、Auth 31、Docs 45、Account 19 检查通过；`final-frontend-tests.log`。请求代次与异步竞态采用确定性测试设施，真实浏览器见下方，二者不混称。 |
| 邮件 | `mail-locales.log/xml`：61p；本地 TLS SMTP 接收器、file sink、三语言模板、失败与限流；不证明外部投递或收件箱。 |
| 组织/恢复/迁移 | `org-extras.*` 25p；`restore-final.*` 73p；`account-restore.*` 2p；`org-restore.*` 9p；`migration-v6.*` 2p。真实归档和重复迁移，保留 ID/归属/文件哈希，不复活会话或外发。 |
| 会话与回调 | `rp-late-final.*` 25p；OIDC 真实兑换后注入登出/重置/过期，晚回调拒绝并验证正向设备关联。设备/SSO专项 94p 亦在聚合回归覆盖。 |
| 访问日志 | `access-log-before.log` 保留发现查询泄漏的失败；`access-log-final.log/xml` 15p，真实 Uvicorn 请求验证日志有路径/状态但无合成授权码、邮箱及查询串。 |
| 本机启动 | `local-runtime.log/xml` 38p；双回环真实监听、端口冲突保护。 |
| 干净副本 | `clean-source-manifest.json` 逐文件哈希；新 Python3.12.6 环境依锁安装、pip check、六包从副本导入；Node24.14.0 最终 `npm ci --offline`、三前端 typecheck/build、上述全部前端测试通过（`clean-final-*.log`）；307xx 源站真实 READY 后停止。未复制 data、配置秘密或依赖目录。 |
| 组织/邀请最终浏览器 | `task-f-browser-r5/report.json`：19检查，0失败/0pageerror，407请求；三语言注册/激活/组织、移动390px、组织头像、既有/新用户邀请、拒绝、个人幂等、明确空间SSO/语言刷新。 |
| 账户最终浏览器 | `/private/tmp/synwevo-account-browser-proof-dt_1up9u/report.json`：6流程；邮箱变更未登录接续、旧会话撤销、资料/头像/偏好、个人导出/删除/恢复，无pageerror。账户由隔离身份服务夹具建立；公开注册由上/下两项证明。 |
| 三语言安全最终浏览器 | `security-browser-r4/report.json`：18检查，0失败/0pageerror；真实公开注册、file-mail激活、虚拟 UV CTAP2 Passkey登记/改名/登录/移除、TOTP错误与正确值、恢复码单次消费与重复拒绝。不是物理设备证明。 |
| Docs 最终全链路浏览器 | `docs-browser-final-r2/results.json`：最终请求代次及SSO导航代码下，真实注册→验证→个人→门户→Docs；三语言各3份XLSX与1份预览，原文/公式`=B4*2`/数值12.5、0、-3.25/缓存25、0、-6.5均读回一致；所有修改与预览/ZIP有workspace头，无Docs错误或pageerror。 |
| API 台最终 SDK 浏览器 | `api-platform-evidence/browser-sdk-live/evidence.json`：UI申请/所有者批准/签发，已安装 Python SDK0.1.0 经44次真实本地HTTP请求、无Cookie，程序定义5字段/5映射，预检12有效、真实预览无新任务、12份DOCX/ZIP逐份读回及SHA校验。39条成功调用匹配服务端request ID。Hans轮换后旧key的项目/下载401；Hant撤回后新key同样401；普通owner页面/API404；注销POST200、旧me401。三语言契约搜索/分页通过，0pageerror/外部请求。 |
| API 后端专项 | `api-platform-evidence/` 原83项回归含44竞态、2种凭据引擎流水线与受限控制面；原流水线入站为ASGI TestClient，签名Webhook接收为真实回环服务。只有上项称真实SDK socket全链路。 |

Docs 最终自动操作观测：Chrome153、1600×1000、Apple Silicon、本地合成3行，三语言分别4.34/4.26/4.25秒；
不是人类完成时间、线上性能或前后改善幅度。截图、请求元数据及实际产物 SHA 与 JSON 同目录。
更早的浏览器驱动假设失败也保留；最终脚本通过实际按钮完成接续，不用手动跳转绕开产品流程。
预期 400/401/403/404 是负面验收，不改成假200。

## 覆盖与尚未验证的边界

当前阅读是相关模块增量审阅，未变文件仅在 SHA 一致时接续，未声称重新完整阅读全仓。
覆盖清单区分 full/partial/indexed/excluded；partial/indexed 不能算已完整阅读。历史完整审阅不是本轮全部页面/格式验收。
临时证据可能被系统清理；仓库保留可复现脚本与结果说明，数据库、邮件、凭据、产物不进 Git。

以下是准确的交付边界，不作为已通过项：

- 本轮使用合成文档和本地邮件设施；物理 Passkey、真实SMTP最终收件箱、用户真实复杂模板未验证。上一轮SMTP结果不冒充本轮三语言收件箱证据。
- 关键流程、移动布局和语言资源已验收；没有对每一页面/每一句译文完成独立人工语言审校，也没有全浏览器/低配设备长稳或正式无障碍、安全认证。
- 下载仍先缓冲浏览器 Blob；超大ZIP的流式授权下载是已记录的性能边界，不冒充无限规模支持。
- CLI/其他语言客户端/n8n 的既有能力未删除；本轮新增控制台与安装后PythonSDK已实跑，没有重新宣称所有第三方集成都完成本轮端到端验收。
- LICENSE 尚待权利人选择，历史许可证测试因此skip；发布前需确认依赖许可义务。运营主体、隐私/服务文本的适用法律资料也需运营方确认，页面不编造企业地址或认证。
- Linux生产部署、外部云工作流与生产日志代理配置不在本轮操作范围；不因本机通过而宣称生产已验收。

本轮未留下已知阻断主链路的失败。后续如获部署授权，先复核生产备份、实际邮件/可信URL、schema迁移和平台策略，
再按既有受控流程部署；本轮不触发该动作。
