# 业务应用与个人 Agent：工作包与能力差距

状态：未开始 / 进行中 / 阻塞 / 已验证（本地）/ 已发布（生产上线核对通过）。验收编号见 MASTER_PROMPT §13。
“现状”列来自 2026-09-29 首轮调查。

## 能力差距（首轮调查）

| 需求 | 现状 | 依赖 | 实施位置 | 验收 |
|---|---|---|---|---|
| 应用/能力注册 | 只有 `apps` 表（启用与资格）；无操作注册、无 schema | — | `synwevo_core.capabilities` | A4 |
| 统一运行记录与事件 | Docs 专用 `jobs`/`job_events`/幂等/Webhook | — | `op_runs`、`app_events` | B3–B5 |
| 流程编排 | 不存在（只有各自的状态机） | 注册表 | `synwevo_core.flows` + suite 运行器 | B1–B4 |
| Forms | 不存在（只有 PDF 表单域读写） | 注册表、文件接纳链 | `synwevo_suite.forms` | A2 A3 D5 |
| DataPrep | 不存在（Docs 有 CSV/XLSX 读取器） | 注册表、Docs 读取器 | `synwevo_suite.dataprep` | D1 |
| Approvals | 仅“复制审批”（组织内项目复制） | 注册表、通知 | `synwevo_suite.approvals` | D3 B3 |
| Extract | Docs 有 OCR/PDF 单词坐标/单元格来源 | Docs 读取器注入 | `synwevo_suite.extract` | D2 |
| Tables | Docs 实时表格是文档模板，不是业务数据 | 注册表 | `synwevo_suite.tables` | D4 |
| 个人 Agent | 不存在；无模型提供方 | 注册表、模型密钥 | `synwevo_suite.agent` | C1–C10 |
| 机器凭据调用新应用 | `/api/v1` 与机器身份只认 Docs | API 平台专项 | `MachineAuth` 泛化 | A4 |
| 新应用对外入口 | workplace 源只有 `/workspace` | D1 | server 组装 + suite 外壳 | A1 |

## 工作包（按顺序；同一时间只有一个协调者合并）

| ID | 内容 | 优先级 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|---|
| WP0 | 基础：`apps/suite` 模块与外壳、portal/all 挂载、网关规则、检查脚本/构建/Docker/本地启动、`synwevo_core.capabilities`（注册、`/api/suite/ops`、`op_runs`、`app_events`、幂等）、应用目录登记与开关 | P1 | — | 注册表单元与集成测试；两个角色下路由可达、Docs 路由不受影响；A5 回归 | 已验证（本地，51614b4） |
| WP1 | Forms 完整应用（表单库、编辑器、预览、发布版本、填写请求、公开/专属填写页、草稿、补件、附件接纳链、事件） | P1 | WP0 | A2 A3 D5；界面与 API 两种方式创建；浏览器三语言/窄屏 | 已验证（本地，51614b4）；余项：桌面宽屏与英文/繁体浏览器走查、Docs/Message 适配（WP7） |
| WP2 | Agent 核心：个人会话、提供方适配器、工具循环（注册表发现 + 服务端鉴权）、确认卡、SSE、取消/恢复、界面 | P2 | WP0 | C5 C6 C7 C9 C10；真实模型链路（需密钥） | 进行中（协调者） |
| WP3 | Tables（并行实现者，worktree suite-tables）（字段、记录、唯一性、关联、视图、修订号冲突、字段权限、导入导出、变更记录、事件） | P3 | WP0 | D4 | 进行中 |
| WP4 | DataPrep（并行实现者，worktree suite-dataprep）（导入、方案版本、步骤、预览、全量运行、错误清单、导出） | P3 | WP0、Docs 读取器 | D1 | 进行中 |
| WP5 | Extract（模板、任务、来源定位、复核、修正历史） | P4 | WP0、Docs 读取器注入 | D2 | 未开始 |
| WP6 | Approvals（并行实现者，worktree suite-approvals）（模板、顺序/会签/或签/条件、退回/驳回/撤回/转交、快照与修订、并发终态、提醒） | P4 | WP0、通知 | D3 B3 | 进行中 |
| WP7 | 流程引擎 + Docs/Message 适配器 + B1 全链路 | P4 | WP1–WP6 | B1–B6 | 未开始 |
| WP8 | 跨空间 Agent 任务、个人自动化与预授权、记忆与撤权 | P5 | WP2 WP7 | C1–C4 C8 | 未开始 |
| WP9 | 机器凭据调用新应用（与 API 平台专项协调） | P5 | API 平台 | A4 | 未开始 |
| WP10 | 发布：公开页面数据流更新、生产开关、部署与上线核对 | P6 | 各应用验收 | E4 | 阻塞（GitHub Actions 额度，见 STATE） |
