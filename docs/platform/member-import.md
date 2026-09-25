# 多格式成员批量导入（CI-25）

组织拥有者或持有 `org.members.import` 的成员，用手头的成员名单文件批量邀请人加入组织的某个工作空间，并为已有成员提升空间角色或增加组织自定义角色。
文件不会直接制造成员：新人仍然收到普通邀请并自己接受，接受时才加入组织并获得导入指定的角色。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 组织只能逐个或以 JSON 列表批量邀请（`/workspaces/{wid}/invitations/batch`，≤100）；真实名单在 Excel/Word/PDF/扫描件里，管理员只能手工转录。角色无法随邀请一起指定。 |
| 成熟参考 | Microsoft Entra 批量邀请（CSV 模板→校验→批量任务→逐项结果；2026-09-26 读取）：**文件先校验再提交任务，结果逐行可查**。Slack 自定义角色文档：角色由权限组成、分配需要明确操作。OWASP File Upload Cheat Sheet：内容检查在解析之前。本项目扩展点是"任何合理格式"而不是固定模板。 |
| 对比与选择 | (a) 把解析器搬进共享包：违反「PDF/图片/OCR 属于 Docs」且让账户服务在无引擎机器上启动不了；(b) 账户服务通过 HTTP 调 Docs 解析：新增服务间凭据与运行依赖；(c) **由组装根 `synwevo_server` 把 Docs 的读取器作为适配器注入账户服务**（与已有的 `DocsControlPlane` 同一模式）：边界检查不变，账户服务单独运行时退回标准库读取器（CSV/TSV/JSON/TXT/MD）并如实说明其它格式需要引擎。选 (c)。clamd 适配器是纯标准库且两边都用，搬入 `synwevo_core.malware`（Docs 保留再导出）。 |
| 预期变化 | 组织页新增「批量导入成员」：上传→读取→字段/角色映射→逐行预览（邀请/更新/不变/跳过/阻断及原因）→确认（重认证+理由）→后台逐行执行→结果与 CSV 报告；新人接受邀请时按导入者当时权限分配组织角色并收到通知；导入者收到汇总通知。 |
| 边界与风险 | 权限：`org.members.import` 进入；组织角色受 `OrgRoles.assign` 委派规则约束（计划时、执行时、接受时三次核对）；空间角色受邀请服务规则约束（不能高于自己，owner 不可导入）。数据：原文件仅在映射阶段保留 7 天（供 OCR），结束即删；库里只有有界的行。资源：引擎限制照用 + 导入自有上限。语言：全部三语言；手机宽度可完成。成本：OCR 在账户容器内运行，20 页/次，Tesseract 子进程最小环境。 |
| 验收 | 见 §5；接口测试（识别服务 + 引擎路径）、两引擎浏览器闭环、负向：越权委派、撤销委派后执行失败、文件里的 owner/admin、重复、自己、坏地址、取消。 |
| 发布与恢复 | schema v11 只新增两张表；无数据迁移。回滚到旧版本不影响已有成员（导入产生的邀请与角色分配是普通对象）。生产 `accounts` 容器需要补 `SYNWEVO_CLAMD_ADDRESS`/`SYNWEVO_MALWARE_SCAN` 才会扫描导入文件（运维步骤）。 |

## 2. 对象与状态

| 表 | 字段要点 |
|---|---|
| `member_imports` | `organization_id`、`workspace_id`（目标空间）、`created_by`、`filename`/`sha256`/`size`/`format`、`status`、`extraction`（有界的表格与行）、`mapping`、`summary`、`scan`、`version`、`cancel_requested`、`expires_at`（7 天） |
| `member_import_rows` | `seq`、`source`（工作表/表格/页/行号，OCR 置信度）、`fields`（email/name/workspace_role/org_roles）、`action`、`reason`、`diff`（更新前后）、`status`、`result`、`invitation_id`、`org_role_ids` |

导入状态：`extracted → planned → running → finished | partial | failed | cancelled`。行动作：`invite`（新人或不在此空间的账户）、`update`（已有空间成员：提升空间角色和/或增加组织角色）、`unchanged`、`skip`、`blocked`。行状态：`planned → pending → running → done | failed | skipped`。

### 逐行原因（稳定 id，三语言标签在组织页资源里）

| 原因 | 动作 | 含义 |
|---|---|---|
| `new_person` / `existing_account` / `org_member_new_workspace` | invite | 发邀请；后者已是组织成员，组织角色在执行时立即分配 |
| `already_member` | unchanged | 已是此空间成员且角色不变 |
| `duplicate_in_file` / `self` / `already_invited` | skip | 文件内重复、导入者本人、已有待接受邀请 |
| `bad_email` / `unknown_workspace_role` / `unknown_org_role` | blocked | 语法不合法；角色文字未在映射步骤明确映射（角色权限永远不从名字推断） |
| `role_not_importable` | blocked | 空间 owner、组织 owner/admin 基础角色不能通过文件授予 |
| `role_not_delegable` / `forbidden` / `role_too_high` | blocked | 导入者没有分配该组织角色所需权限 / 不能邀请到此空间 / 不能邀请高于自己的角色 |
| `account_unavailable` / `over_limit` | blocked | 账户被停用或删除 / 超过单次 500 行 |

## 3. 支持的输入与限制

| 输入 | 读取器 | 结果形态 | 限制与说明 |
|---|---|---|---|
| CSV / TSV | 标准库（两种引擎都可）| 表格 | 编码 utf-8-sig/utf-8/gb18030/big5/cp1252 依次尝试；分隔符自动探测；不执行公式 |
| JSON | 标准库 | 表格（对象数组；字段名为首行） | 顶层数组或对象里第一个对象数组 |
| TXT / Markdown | 标准库 | 行（Markdown 管道表格成表格） | 按行找邮箱，行内其余文字作姓名 |
| XLSX / XLSM / XLS | 文档引擎（openpyxl 缓存值 / xlrd） | 每个工作表一张表格 | 隐藏工作表标注；无缓存值的公式读作空白 |
| ODS / DOC / ODT / RTF | 文档引擎（LibreOffice 隔离转换） | 同上 / 同 DOCX | 转换超时与内存上限照引擎 |
| DOCX / DOCM | 文档引擎（lxml） | 每个表格一张表格 + 段落行 | 合并单元格按首格计 |
| PDF（有文字层） | 文档引擎（PyMuPDF/pdfium） | 行（页码+行号） | 前 200 页；无表格结构，按行找邮箱 |
| 扫描 PDF / PNG / JPEG / TIFF / WebP / BMP / GIF | 文档引擎 + Tesseract（chi_sim+eng） | 行（页码、行号、置信度） | `needs_ocr` 后由操作者点击「识别文字」；每次最多 20 页；来源标 `ocr` 便于复核 |
| 其它 | — | `unsupported` + 原因 | 换扩展名不会变成支持 |

## 4. 权限与再核对

- 进入：`Organizations.require(oid, uid, permission="org.members.import", active=True)`；`admin` 基础角色默认含此权限，owner 全部，member 无。
- 组织角色：计划时要求导入者持有 `org.roles.manage` 且持有该角色的每项权限（否则 `role_not_delegable`）；执行/接受时再次由 `OrgRoles.assign(oid, importer, user, ids)` 判定——委派被撤销后行失败或角色不分配，行结果记录 `refused`。
- 空间角色：`Invites.may_invite(importer, wid, role)` 在计划与执行时各一次；已有成员只提升不降级，owner 不动。
- 确认与重试是敏感写：需要最近的 `admin` 重认证（同组织路由）。
- 审计：`organization.member_import.upload/ocr/plan/confirm/cancel/retry/finished`，每个邀请 `invite.create`（meta 含 `import_id`），角色分配 `organization.role.assign`；后台事件 `request=None`（无请求 id、无地址）。

## 5. 验收记录

见 `docs/quality/verification.md` §16.25。
