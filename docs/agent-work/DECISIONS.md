# 业务应用与个人 Agent：有影响的决定

每条写明决定、依据（现场事实）、代价与撤回条件。现场事实的来源是 2026-09-29 首轮调查（代码与配置，不是旧记录）。

## D1 六个新应用落在哪里：一个新模块 `apps/suite`，挂在 workplace 源（portal 角色）下

* **决定**：Python 包 `synwevo_suite`（`apps/suite/`），前端外壳 `@synwevo/suite-web`（`apps/suite/web`，静态目录
  `suite-assets/`）。由 `synwevo_server` 在 `portal` 与 `all` 角色挂载。入口路径：`/forms`、`/tables`、`/dataprep`、
  `/approvals`、`/extract`、`/agent`，正式地址即 `https://workplace.synwevo.com/<app>`。每个应用是 `synwevo_suite`
  下的独立子包（`forms/`、`tables/`…），**兄弟子包互不导入**（`check_boundaries.py` 机械检查），只经
  `synwevo_core.capabilities` 注册表互相调用；共用代码只能放 `synwevo_suite.common`。
* **依据**：生产 nginx 按主机把 `workplace.synwevo.com` 的全部路径转给 portal 容器；`nginx.conf`、`compose.yaml`
  属主机、CI 部署不下发（`receive.py` 只构建镜像并 `compose up`）。新容器或新子域都需要运维改主机配置与 DNS，
  主提示词 §1.2 要求不为“独立”引入 DNS 依赖。portal 角色已有统一登录（`portal-web` 客户端）。
* **代价**：portal 容器 `mem_limit 768m / 1 CPU`（主机 compose），DataPrep/Extract 必须流式与限额。
  六个应用共进程——一个应用的崩溃会影响同进程的其他应用；以线程池隔离长任务、限额与超时缓解。
* **撤回条件**：出现独立扩缩容需求时，按角色拆出 `suite` 容器，代码不变，只改组装与主机配置。

## D2 共享契约：`synwevo_core.capabilities`（能力注册 + 运行记录 + 事件出站表）

* **决定**：应用在 `install` 时登记清单（应用 ID、名称、入口、对象类型）与操作（稳定 ID、Pydantic 输入/输出模型
  → JSON Schema、所需权限、副作用级别、同步/异步、可取消/可重试、幂等策略、Agent 暴露策略、人工决定限制）。
  所有调用入口（界面、`/api/ops/{op}`、流程步骤、Agent 工具）走同一个 `invoke()`：校验输入 → 解析目标空间 →
  应用资格与空间角色 → 幂等 → 写 `op_runs` → 执行处理函数 → 结果/错误 → 同一事务写 `app_events`。
  运行记录携带 `principal`（`synwevo_core.authority` 的描述符，含委托来源）、`workspace_id`、`app_id`、
  `operation`、`config_version`、`correlation_id`、`parent_run_id`。
* **依据**：现有 `/api/v1` 与幂等、事件、Webhook 全部硬编码为 Docs（`MachineAuth._platform_token` 拒绝非 Docs
  客户端，`authority.check` 硬编码 `docweave.*` scope）；没有通用编排。Pydantic 已随 FastAPI 在依赖里。
* **工作空间由请求显式给出**：每次调用带 `workspace_id`，由服务端按实时成员关系核验；不读浏览器标签页绑定
  （`X-Synwevo-Workspace`）来决定写入位置。

## D3 Docs / Message 通过注入的适配器加入契约

* **决定**：沿用 `DocsControlPlane` / `DocumentIngest` 的组装层注入模式：`synwevo_server` 在 portal 进程里构造
  Docs 适配器（私有、不挂载的 Docs ASGI 应用，`start_jobs=False`），向注册表登记 `docs.*` 操作；执行仍由 Docs
  自己的业务路由与作业执行器完成，权限按调用人实时计算。Message 的业务层在 core，`message.*` 操作直接调用。
  Extract 的文件读取（文字层、OCR 单词坐标、单元格）同样经注入的 Docs 读取器，不把 OCR/PDF 搬进共享包
  （AGENTS.md 明令）。
* **代价**：Docs 代码在 portal 进程里被导入（accounts 角色已如此导入 `DocumentIngest`）。

## D4 功能开关只隔离未验收的应用

* **决定**：`SYNWEVO_SUITE_APPS`（逗号分隔的应用 ID）决定哪些新应用在注册表里登记为可用；未列出的应用
  路由返回 404、不出现在应用目录与 Agent 工具目录。本地与隔离验证实例列全部；生产只列已验收的。
* **依据**：主提示词 §12 P6。

## D5 个人 Agent 的模型：服务端提供方适配器，未配置就明确显示未配置

* **现场**：仓库与本机都没有任何模型提供方配置（首轮调查：无 SDK、无密钥变量；`ANTHROPIC_BASE_URL` 属于
  Claude Code 宿主，不是产品配置，不得借用）。`docs/platform/public-pages.md:48` 公开声明“没有 AI 处理用户内容”。
* **决定**：实现真实的提供方适配器（Anthropic Messages API，经锁定的官方 Python SDK，模型名来自配置）；平台级密钥由运维以
  环境变量提供，个人自备密钥以 `synwevo_core.sealed` 加密存储；工作空间“允许的 AI 数据处理提供方”策略默认
  **关闭**——未开启的空间，其业务内容不会进入模型上下文（工具仍按权限执行，但结果不回送模型，只返回给用户
  并说明原因）。上线前同步修改公开的数据与隐私页面。
* **阻塞**：真实模型链路的验收需要持有人提供一个提供方密钥（见 STATE.md「阻塞」）。测试用明确标注的替身
  驱动工具循环，不作为生产回退，也不算真实模型验收。


## 2026-10-02 · 保持串行完整运行时门槛，按实际容量调整时间预算

CI36997527305执行2925/4214项用了39m37后因SDK来源断言停止；当前4257项按同速含构建预计约63min。verify作业上限由45改75分钟，保留2CPU/3GiB、断网、清理、全部测试/断言/skip及verify成功后才deploy的依赖。75是运营时间预算，不改变测试中的性能/正确性要求。GC分批实测净省3.4%不足以支持改清理；分组发现testdata生成顺序及不稳定nodeID，暂不引入分组或新增Actions依赖。若完整新CI仍出现内存或业务故障，继续定位根因，不无限加时。

## 2026-10-03 · CI容量按根因处理，不再加时

6052c13 verify用74m36/75。测量显示每测固定成本（全量gc、每测新库建表、生产Argon2）约占75%。以测试基础设施修复（gc.freeze、迁移模板库、测试Argon2参数，均保留测试与断言，相关参数/迁移/计时测试显式保留生产路径）代替继续加时；6533c2a verify降至24min。75分钟预算不变。撤回条件：若模板库或测试参数掩盖真实缺陷（如本轮暴露的GCRA问题），优先修产品并补显式标记测试。

## 2026-10-03 · 跨空间内容转移：一份共享数据流契约（C1–C4）

* **现场**：Agent 读过 A 的内容后向任何其他空间写入一律 `source_transfer_unavailable`；流程跨空间引用一律
  `flow_transfer_policy_required`；`forms.duplicate` 跨空间只核对目标写权限，不核对来源导出与目标受众。三处口径不一。
* **决定**：`synwevo_core.data_flow` 是唯一判定“S 的内容成为 T 所有之物的一部分”的地方，Agent、Forms 复制和流程
  共用。依次核对：本人仍是 S 的有效成员且有导出权（owner/admin/editor 或 `can_export`，与 Docs “读取≠导出”同义）；
  S 的外发政策（`none` 不外发 / `audience` 目标空间每位成员都已是 S 成员 / `organization` 同一组织 / `any`），
  个人空间默认 `any`（只有本人），组织内空间默认 `organization`，其余共享空间默认 `audience`（没有人因转移获得原本没有的阅读面）；S 所属组织的上限
  （`organization` 默认，组织内容不出组织 / `any`），组织上限只能收紧不能放宽空间政策。空间政策由该空间
  owner/admin 设置；组织上限仅组织所有者设置、写组织审计。
* **绑定**：Agent 的确认卡展示来源空间、目标与受众；确认指纹包含每个来源的政策修订号、组织上限和受众，任一变化
  确认失效；执行前再判定一次。流程在每次解析引用时以流程所有人当前权限重新判定。
* **不做**：不按字段追踪模型输出中哪些字来自哪个空间（做不到可靠），因此分支里传给模型的任何来源都视为可能
  转移；读操作的参数不视为转移（已知局限，记录在 docs/platform/data-flow.md）。转移后的独立产物按目标空间
  权限管理，保留来源空间 ID 作出处，不保留来源正文依赖。

## 2026-10-10 显式业务上下文沿用来源与报价契约

首批资源为Forms定义和Tables字段视图，经现有list/get读取，客户端只传引用及指纹；用户消息api_json保存有界快照、sources_json保存来源，不新增数据库/迁移。引用进入quote/start/持续授权同一摘要，空引用兼容旧摘要。合法复制目标的读取权限与原始来源导出约束分离，手动选择与工具读取共用carried规则；不把复制来源误当持续读取依赖。个人文件另做user归属，不借任意workspace容纳私人附件。
