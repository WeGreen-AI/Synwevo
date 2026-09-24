# 代码地图与审阅覆盖记录

本文件回答"这个仓库现在是怎么工作的"，并记录本轮代码审阅**读了什么、读到什么程度、基于哪个版本**。
审阅发现的逐条清单在 [review-findings.md](review-findings.md)；修复状态以那里为准。

**2026-09-24 接续说明：** 下方 288 文件与 Git blob 表是 Claude 阶段在 `51b31fe` 的历史基线，
不是随后数百项修改的全量验收。当前工作区（含未跟踪自有代码）的逐文件 SHA-256、阅读程度、阅读者
与证据以 [review-coverage.json](review-coverage.json) 为准；内容变动后未增量阅读的条目不会沿用“完整”。
本轮实施、实际命令和未完成项在 [handoff](../quality/handoff.md)。本轮最终验证以 handoff 的当前记录为准。

### 2026-09-25 增量代码地图

本轮从 `a20537b` 的当前工作树继续，按账户/组织/导航/控制面及其调用链增量阅读；没有重新声称全仓完整阅读。
覆盖表以文件内容哈希绑定：旧哈希不匹配的条目保留历史记录，并重新标记当前阅读程度。
Agent 的实现报告、主线审阅和实际测试分开，不把某个 agent 的“完成”直接当综合通过。

| 用户动作/边界 | 当前实际入口与状态变化 |
|---|---|
| 注册与验证 | identity `registration.py` 注册 pending/none → `mail_outbox` →可信 URL fragment 邮件链接→ `auth_routes` 显式验证与最终口令→重新登录；待验证会话被 http gate 限制。 |
| 门户和 Docs 登录 | Portal 公开页进入中央账户；Docs `/auth/start` 使用 auth-client PKCE/state/nonce，通过身份 provider 换码，RP 服务端会话关联中央会话。合法 next/workspace/locale 经校验后继续。 |
| 当前 tenant | auth-client 每标签页保存 workspace；浏览器 X-Synwevo-Workspace 只提出选择，http gate 重新检查成员、组织/空间存活与应用资格，才设置 ContextVar。机器 tenant 来自已验证凭据。 |
| 组织治理 | core `Organizations` 持有组织/成员规则，identity `organization_routes.py` 在写事务内重读会话/角色/reauth，调用服务并审计。创建组织与首个空间事务化，组织身份不替代业务空间权限。 |
| 文档授权/生成 | Docs `authz.py` 对真实对象执行租户/导出检查；模板、映射、样本与生成仍进入原 `Services/Views/Jobs`，没有第二套引擎。任务快照固定业务输入，authority 在执行与交付时重验当前权限。 |
| API 台 | identity 管理路由检查 api_platform.manage；core Approvals 保存所有者批准和credential binding；server 注入 Docs 适配器，适配器使用明确受限机器凭据进入同一业务 API。平台管理员不是业务内容超级用户。 |
| 数据保存与清理 | Store 单一迁移入口；v6 新表/列不改既有 ID/tenant/key。用户图片在受限 BLOB；文档仍在内容寻址 blobs。账户删除恢复/终止在 identity profile；组织删除为7天软删除窗口；归档恢复执行当前 journal 与外发隔离。 |
| 三语言 | UI 共用语言核心，三个外壳分别登记资源；账户 locale 跨设备持久化，访客本机保存；邮件在入队前固定语言。机器契约和上传文档不翻译。 |

本轮缺陷与修复入口：激活前抢注口令/并发登录、授权码撤销、RP设备撤销、终止删除恢复、
管理员调试请求执行中降权、同一路径邮件fragment接续、请求代次隔离和访问日志查询串保护。每项真实状态以 handoff/测试日志为准。

当前已核实的链路变化：浏览器每次 Docs API 请求重验空间状态、成员、应用资格以及读/写/导出策略；
机器身份由凭据行、服务客户端或委托令牌决定 tenant，不接受请求体提供的空间绕过。任务使用持久主体描述，
在领取、执行、重试及发布前复核当前权限；产物下载再次复核。`render_project_preview` 与 UI 共用
`Views.preview`、`fill_checked` 和输出读回校验。身份刷新消费与继任发行使用同一 Store 事务。
恢复在临时副本重放撤销并停止会话/令牌/任务/外发，校验通过后才安装数据库和 blob；损坏日志不能默默跳过。

## 1. 审阅范围与方法

| 项 | 值 |
|---|---|
| 审阅基线 | `51b31fe`（分支 `upgrade/automation-api-ui`），审阅开始时工作区干净 |
| 范围 | `git ls-files` 中全部项目自有文件 **288 个**：Python、TypeScript/TSX、CSS、HTML、配置、测试、脚本、容器文件与 `docs/` |
| 未逐字阅读（仅识别作用） | `apps/docs/tessdata/*.traineddata`（Tesseract 语言模型，二进制，来自 tesseract-ocr/tessdata）、`package-lock.json`（npm 锁文件，由 `npm install` 生成）、`apps/docs/regression/20260923-*/`（合成回归用例的数据与 xlsx 二进制）、`docs/screenshots/*.png` |
| 方法 | 16 个并行阅读者按模块分组，每人对分到的文件**从头读到尾**（长文件分段、超长行用 `sed -n` 分段），并追踪调用关系；每条 P0/P1 发现再由一个独立复核者尝试推翻（68 个复核） |
| 结果 | 288/288 **完整阅读**；0 部分阅读、0 仅索引、0 未读。发现 374 条：P0 9、P1 59（57 条复核成立、2 条被推翻并降级）、P2 151、P3 155（P2/P3 未逐条复核，修复前由实施者再核对） |

完整阅读的判定：读到文件最后一行；只看函数名、搜索命中或被截断的输出**不算**。每个文件的 blob 哈希记在 §5，
内容变了的文件在后续轮次需要重新阅读，未变的可以凭本表接续。

**以后怎么增量阅读。** 修改某个文件前读它本身、它的直接调用方和被调用方；只有触及 §3 列出的跨模块链路
（网关、身份、租户、任务、授权）时，才重读整条链路。不要求每改一个按钮就重读全仓库。

## 2. 模块地图

```
services/identity  (Synwevo Account)  登录/MFA/Passkey/邀请/找回/管理后台/OIDC 提供方      ── synwevo_identity
apps/portal        (Synwevo Workspace) 登录后的首页：可用应用、工作空间                     ── synwevo_portal
apps/docs          (Synwevo Docs)     文档引擎、业务服务、任务、/api 工作台接口、/api/v1 机器接口 ── synwevo_docs
packages/core      Store(唯一持久化与迁移)、租户上下文、身份与会话、机器身份、OIDC 令牌存储、网关 ── synwevo_core
packages/auth-client OIDC 依赖方、共用会话路由、浏览器端会话上下文                            ── synwevo_auth_client
packages/ui        三个外壳共用的设计变量与基础组件
infra/server       部署组装：按 SYNWEVO_ROLE 把以上模块拼成一个进程                           ── synwevo_server
```

一个数据库（`<数据目录>/docweave.sqlite3`，WAL）＋内容寻址 blob（`<数据目录>/blobs/<前两位>/<sha256>`）。
数据目录：`--data` → `SYNWEVO_DATA_DIR` → 仓库根的 `data/`（由模块位置向上找，不看 cwd）。

## 3. 关键问题（以基线 `51b31fe` 为准；本轮改动见 §4）

**登录从哪里进入。** 口令登录 `POST /api/auth/login`（`services/identity/synwevo_identity/auth_routes.py:108`）
→ `Identity.authenticate`（`packages/core/synwevo_core/identity.py:120`，IP 与账户两级限速、未知账户走等时哈希）
→ 有 TOTP 时发 MFA 票据再 `POST /api/auth/login/mfa`；Passkey 走 `/api/auth/passkeys/authenticate/begin` →
`/api/auth/login/passkey`；首位管理员用 CLI 生成的一次性令牌 `POST /api/auth/bootstrap`。应用侧入口是
`GET /auth/start`（`packages/auth-client/python/synwevo_auth_client/routes.py:42`）：存 state/nonce/PKCE 后
303 到账户服务 `/oidc/authorize`，没有中央会话则去 `/login?next=…`。

**身份如何传给应用。** 授权码 + PKCE(S256)。应用的 `/auth/callback`（`routes.py:53`）在服务端换码、校验
id_token（签名、iss、aud、nonce、exp），`Apps.check_access` 一次，然后建立**应用自己的**会话行（Cookie 只
存它的 SHA-256）。之后每个请求由网关 `_session_gate`（`packages/core/synwevo_core/http.py:217`）载入会话、
校验账户仍 active、会话记住的工作空间仍有成员关系。机器调用方走 `MachineAuth.verify`
（`packages/core/synwevo_core/machine.py:41`）：`dw_` 旧密钥、服务账户（client_credentials）、用户委托
三种凭据产出同一形状的 principal，放在 `request.state.principal`。

**tenant 如何确定。** 一个 ContextVar（`packages/core/synwevo_core/context.py`），**工作空间 id 就是 tenant**。
浏览器会话：会话里的 `workspace_id`（成员关系不存在则为一个永远不匹配的 `__no_workspace__<uid>`，绝不回落到
`default`）；`/api/v1`：principal 的 tenant（旧密钥行的 tenant、服务账户登记的工作空间、委托令牌的工作空间并按
现有成员关系复核）；旧的 none/basic/proxy 模式：`SYNWEVO_UI_TENANT`（默认 `default`）；签名下载链接：链接里的 tenant。

**对象授权在哪里执行。** 以租户相等为基础：`App.get_project/get_template`（`apps/docs/synwevo_docs/services.py`）、
`JobService.get/artifact`（`jobs.py`）、`authz.source_in_scope` 与 `authz.blob_readable`（`apps/docs/synwevo_docs/authz.py`，
所有按 blob id 取数据的路由都经过它，派生预览由 `blob_grants` 授权）。**基线的缺口**：回归用例路由没有任何租户
检查（P0）；浏览器会话不检查成员角色与 `can_export`（viewer 可写、可下载）；不复核工作空间状态与应用资格。

**文档如何生成。** `POST /api/projects/{pid}/jobs` 或 `POST /api/v1/projects/{pid}/jobs` → `JobService.create`：
读取项目上下文（模板当前版本、映射、就绪来源）→ `records.build_records` 切样本 → 逐条 `plan.build_plan` →
一个事务写入快照、全部 job_items、运行记录与 `job.created` 事件（基线在请求内逐条建计划，2 万条约 74 秒）。
运行线程 `_claim`（原子认领 + 租约）→ `_run` → `_process_item`：`build_plan` → `generate.fill_checked`
（按格式分派到 `templates.docx_fill/xlsx_fill/pdf_fill/image_fill` 或 PPTX；XLSX 再经 `formula.compute_workbook`
真实计算）→ `convert_output` → `verify_output` 读回核对 → `put_blob` → 产物行与样本成功状态同一事务发布。
预览走同一个 `build_plan` 与 `fill_checked`。

**任务何时重新检查权限。** 基线：**从不**。运行、恢复、重试、事件投递与签名链接下载都只看租户，不看提交者的
凭据/成员关系/工作空间状态是否仍然成立（本轮修复，见 §4）。

**数据在哪里保存和清理。** 元数据在 SQLite，字节在内容寻址 blob。清理：运行线程的监督循环删除过期幂等键与
`<数据目录>/tmp` 下 1 小时前的临时文件；ZIP 下载后立即删除临时文件；项目/模板/来源是软删除；**没有 blob GC**
（`apps/docs/scripts/blob_audit.py` 只做只读清点，并与 `authz._REACHABLE` 交叉核对）。

## 4. 本轮对上述链路的改动

（实施与验证完成后在此更新，只写已验证的事实。）


## 5. 覆盖清单（基线 `51b31fe`）

状态：`完整` = 从头读到尾。blob 为 `git ls-files -s` 的对象哈希前 10 位，用于判断文件自审阅后是否变化。

| 文件 | 行数 | blob | 状态 | 阅读组 | 作用 |
|---|---:|---|---|---|---|
| `.env.example` | 65 | `7b1cdf8835` | 完整 | 门户/接入/UI包/组装/脚本 | Sample configuration (also used verbatim as the compose env_file) |
| `.gitignore` | 23 | `7510845895` | 完整 | 门户/接入/UI包/组装/脚本 | Ignores venv/node_modules/dist/data/.env/.local |
| `AGENTS.md` | 89 | `c5c1a4f2bf` | 完整 | 门户/接入/UI包/组装/脚本 | Repo working rules, module boundaries and verification commands |
| `NOTICE` | 103 | `3a1a1715e8` | 完整 | 门户/接入/UI包/组装/脚本 | Third-party license notice |
| `README.md` | 107 | `f331b370d7` | 完整 | 门户/接入/UI包/组装/脚本 | Top-level orientation and quick start |
| `apps/docs/AGENTS.md` | 56 | `b8d30d90cf` | 完整 | Docs 接口层 | Module agent guide: ownership, relying-party stance, standalone run, verification commands |
| `apps/docs/README.md` | 108 | `ee8e59c3eb` | 完整 | Docs 接口层 | Module README: features, quickstart, API key CLI usage, doc index, known limits |
| `apps/docs/pyproject.toml` | 27 | `b562169fa8` | 完整 | Docs 接口层 | Package metadata; abstract deps; optional pymupdf/pdfium extras |
| `apps/docs/regression/README.md` | 17 | `2b50a02ae2` | 完整 | 脚本与集成测试 | Describes the regression-case directory and its one committed synthetic case |
| `apps/docs/requirements-pdfium.txt` | 13 | `6e97badfd5` | 完整 | Docs 接口层 | AGPL-free pinned requirement set (no PyMuPDF) |
| `apps/docs/requirements.txt` | 14 | `a47906f8a0` | 完整 | Docs 接口层 | Pinned requirement set including PyMuPDF and pypdfium2 |
| `apps/docs/scripts/_testenv.py` | 143 | `4e912716b7` | 完整 | 脚本与集成测试 | Test-instance guard (require_test_instance) plus a throwaway isolated server (server/target) for the browser scripts |
| `apps/docs/scripts/bench_argon2.py` | 27 | `b9a68b67c3` | 完整 | 脚本与集成测试 | Prints Argon2id cost on this host for choosing password parameters |
| `apps/docs/scripts/benchmark.py` | 166 | `0c2e96b452` | 完整 | 脚本与集成测试 | Batch benchmark: runs the UI HTTP API in-process on a temp data dir with synthetic XLSX |
| `apps/docs/scripts/blob_audit.py` | 270 | `32e5ce77cc` | 完整 | 脚本与集成测试 | Read-only inventory of blob references (direct columns plus JSON), cross-checked against authz._REACHABLE |
| `apps/docs/scripts/make_fixtures.py` | 492 | `12fe457582` | 完整 | 脚本与集成测试 | Generates the synthetic TEST-only fixture files |
| `apps/docs/scripts/regression.py` | 51 | `ce186f9895` | 完整 | 脚本与集成测试 | CLI runner for the saved regression cases (synwevo_docs.regression.run) |
| `apps/docs/scripts/seed_samples.py` | 408 | `de4a2ae9c7` | 完整 | 脚本与集成测试 | Seeds 3 demo projects over /api/v1 with an API key, then checks the first artifact of each |
| `apps/docs/scripts/snapshot.py` | 351 | `178a2ea254` | 完整 | 脚本与集成测试 | Backup create (SQLite backup API plus blobs plus manifest) / verify (restore to temp and compare) / restore |
| `apps/docs/scripts/ui_e2e.py` | 205 | `b7b83339d3` | 完整 | 脚本与集成测试 | Playwright E2E of the main flow (project, upload, fields, rule, mappings, preview, 100 DOCX, ZIP) |
| `apps/docs/scripts/ui_e2e_keyboard.py` | 226 | `ba9ab1cd8f` | 完整 | 脚本与集成测试 | Playwright E2E: command palette, list keyboard/bulk/undo, theme vs paper, layout memory, reduced motion |
| `apps/docs/scripts/ui_e2e_more.py` | 246 | `4297745095` | 完整 | 脚本与集成测试 | Playwright E2E: per-group XLSX detail, PDF box drawing, built-in doc and sheet editors |
| `apps/docs/scripts/ui_perf.py` | 165 | `e8c93d7a95` | 完整 | 脚本与集成测试 | Headed Chrome frame and interaction timing against a given project |
| `apps/docs/synwevo_docs/__init__.py` | 0 | `e69de29bb2` | 完整 | Docs 接口层 | Empty package marker |
| `apps/docs/synwevo_docs/api/__init__.py` | 0 | `e69de29bb2` | 完整 | Docs 接口层 | Empty package marker |
| `apps/docs/synwevo_docs/api/app.py` | 588 | `722492f39b` | 完整 | Docs 接口层 | All workbench /api routes (74), create_app factory mounting platform gate, probes, v1 router, SSO/session routers and SPA; upload size helpe |
| `apps/docs/synwevo_docs/api/main.py` | 3 | `793f52dc26` | 完整 | Docs 接口层 | ASGI entry `app = create_app()` for standalone uvicorn |
| `apps/docs/synwevo_docs/api/v1.py` | 462 | `b3b1d8fe6a` | 完整 | Docs 接口层 | Machine API /api/v1 (39 routes), signed download links, path-import from IMPORT_ROOTS, OpenAPI subset export |
| `apps/docs/synwevo_docs/authz.py` | 99 | `e47f9166c6` | 完整 | Docs 接口层 | Object-level tenant reachability checks: blob_readable, grant_blob, source_in_scope, job_in_scope, same_project |
| `apps/docs/synwevo_docs/builtin_service.py` | 60 | `1ef03c5c3b` | 完整 | Docs 接口层 | Create/save templates from built-in DOCX/XLSX editors |
| `apps/docs/synwevo_docs/capabilities.py` | 131 | `399a11f2dd` | 完整 | Docs 接口层 | Format capability matrix + environment report (LibreOffice, Tesseract, PDF engine/licence) |
| `apps/docs/synwevo_docs/cli.py` | 324 | `94de335d4e` | 完整 | Docs 接口层 | Ops CLI: apikey create/list/revoke/rotate, openapi export, jobs recover/list, projects list/prune, info |
| `apps/docs/synwevo_docs/definitions.py` | 335 | `8717374835` | 完整 | Docs 接口层 | Declarative template-field and project definition import/export (docweave.* schema v1) |
| `apps/docs/synwevo_docs/engine/__init__.py` | 0 | `e69de29bb2` | 完整 | 引擎：计划/公式/模板 | Empty package marker (0 lines) |
| `apps/docs/synwevo_docs/engine/blocks.py` | 351 | `082ee01795` | 完整 | 引擎：Office/表格 | Per-block sample mode: splits a source (sheet/DOCX/PDF/text) into virtual SourceDocs at start/end marker matches |
| `apps/docs/synwevo_docs/engine/builtin.py` | 452 | `74c73b7291` | 完整 | 引擎：PDF/图片/PPTX | Built-in editor JSON (TipTap doc / grid) -> DOCX/XLSX template bytes plus slot list |
| `apps/docs/synwevo_docs/engine/converters.py` | 172 | `055f790616` | 完整 | 引擎：Office/表格 | LibreOffice subprocess wrapper (per-PID private profile, CJK font injection, CPU rlimit, timeout, global per-process lock) |
| `apps/docs/synwevo_docs/engine/desensitize.py` | 224 | `0ce64c2908` | 完整 | 引擎：PDF/图片/PPTX | Local desensitization of XLSX/CSV/DOCX/TXT source copies with consistent pseudonyms |
| `apps/docs/synwevo_docs/engine/detect.py` | 148 | `451d61d566` | 完整 | 引擎：Office/表格 | Content-based format detection (magic bytes, zip/OLE inspection, text heuristics) and LibreOffice conversion routing |
| `apps/docs/synwevo_docs/engine/docx_blocks.py` | 233 | `320dca8a4c` | 完整 | 引擎：Office/表格 | Repeating paragraph blocks in DOCX templates: wrap as block-level sdt, auto-detect label/value columns, clone per detail row on fill |
| `apps/docs/synwevo_docs/engine/docx_core.py` | 706 | `6f1063967d` | 完整 | 引擎：Office/表格 | DOCX structural model: flattened block paths, paragraph text/segments, run splitting, dw: content-control marking, filling, table-row helper |
| `apps/docs/synwevo_docs/engine/docx_render.py` | 516 | `bcf310507c` | 完整 | 引擎：Office/表格 | DOCX -> JSON renderer (styles, numbering, tables, inline images as data URIs, field markers) for sources, templates and previews |
| `apps/docs/synwevo_docs/engine/fonts.py` | 150 | `0d1732be16` | 完整 | 引擎：Office/表格 | CJK font file discovery with cmap coverage check; installed-family listing and missing-font detection for DOCX |
| `apps/docs/synwevo_docs/engine/formula.py` | 1350 | `72dc62fa56` | 完整 | 引擎：计划/公式/模板 | Built-in Excel formula parser/evaluator; computes the cached values written into filled XLSX (compute_workbook) and evaluates source cells t |
| `apps/docs/synwevo_docs/engine/generate.py` | 166 | `8a62ac2e13` | 完整 | 引擎：计划/公式/模板 | Fills one document per record (fill_native/fill_checked with the pagination guard), lists export formats, converts output (PDF/CSV) |
| `apps/docs/synwevo_docs/engine/image_raster.py` | 152 | `7cca2cb6a0` | 完整 | 引擎：PDF/图片/PPTX | Pure-Pillow fill path for image templates (used when PyMuPDF engine is not active) |
| `apps/docs/synwevo_docs/engine/image_template.py` | 364 | `6fbbe96e26` | 完整 | 引擎：PDF/图片/PPTX | Image template wrapper PDF, rasterise, to_pdf, pixel helpers (background/ink/rules/patch) |
| `apps/docs/synwevo_docs/engine/images.py` | 49 | `97f6852910` | 完整 | 引擎：PDF/图片/PPTX | Raster source open/inspect/to_png; sets Image.MAX_IMAGE_PIXELS from LIMITS |
| `apps/docs/synwevo_docs/engine/layout.py` | 232 | `f79501f271` | 完整 | 引擎：Office/表格 | Pagination guard: LibreOffice PDF page counting (LRU cache), shrink-to-fit ladder for DOCX fields, fit report |
| `apps/docs/synwevo_docs/engine/limits.py` | 37 | `712c43aacb` | 完整 | 引擎：计划/公式/模板 | Processing limits taken from env vars (file size, unzipped size, cells, pages, records per job, timeouts) |
| `apps/docs/synwevo_docs/engine/numfmt.py` | 354 | `efe12b9288` | 完整 | 引擎：计划/公式/模板 | Small Excel number/date format renderer plus serial<->datetime helpers |
| `apps/docs/synwevo_docs/engine/ocr.py` | 127 | `fc1e2d7ef1` | 完整 | 引擎：PDF/图片/PPTX | Tesseract CLI subprocess OCR with timeout, TSV parsing, word joining |
| `apps/docs/synwevo_docs/engine/ooxml.py` | 240 | `e185df76bc` | 完整 | 引擎：Office/表格 | OPC zip package access: size/entry/path checks, lazy lxml parsing (no entity resolution), relationships, part add/remove, save |
| `apps/docs/synwevo_docs/engine/pdf_engine.py` | 668 | `1e9307c70c` | 完整 | 引擎：PDF/图片/PPTX | PDF engine selection (PyMuPDF/pdfium), inspect/render/words/anchors, redaction working copy, fill_box/fill_pdf |
| `apps/docs/synwevo_docs/engine/pdf_repeat.py` | 426 | `b7bfef57c1` | 完整 | 引擎：PDF/图片/PPTX | PDF repeat-area layout detection, validation, cell rects, row filling and overflow planning (error/shrink/continue) |
| `apps/docs/synwevo_docs/engine/pdfium_shim.py` | 400 | `cea2667e16` | 完整 | 引擎：PDF/图片/PPTX | Read-only PyMuPDF-compatible surface over pypdfium2 with process-wide RLock |
| `apps/docs/synwevo_docs/engine/plan.py` | 311 | `c4ec53d10c` | 完整 | 引擎：计划/公式/模板 | build_plan: resolves every mapping for one record into slot values (single/region/repeat), static_check, slot defaults/required/format |
| `apps/docs/synwevo_docs/engine/pptx_engine.py` | 534 | `66b533a7f4` | 完整 | 引擎：PDF/图片/PPTX | PPTX inspect/render/extract, slot creation and fill (text ranges, cells, regions, repeat rows) |
| `apps/docs/synwevo_docs/engine/raster_text.py` | 209 | `30ba7ddc15` | 完整 | 引擎：PDF/图片/PPTX | Font-metric text wrap/shrink/draw onto pixels, mirroring PyMuPDF insert_textbox |
| `apps/docs/synwevo_docs/engine/records.py` | 197 | `8c2bb9c188` | 完整 | 引擎：计划/公式/模板 | Sample splitting (per_row/per_file/per_group/per_block) and record_context, which rebuilds the table view for each record |
| `apps/docs/synwevo_docs/engine/refshift.py` | 164 | `c0a4f8c7df` | 完整 | 引擎：计划/公式/模板 | Shifts A1 references in formulas/sqref when repeat rows are inserted or deleted in XLSX |
| `apps/docs/synwevo_docs/engine/resolve.py` | 496 | `8a38fe71df` | 完整 | 引擎：计划/公式/模板 | SourceDoc plus locator resolvers (cell/column/docx/pdf/ocr/text/range/detail), including anchor checks and OCR corrections |
| `apps/docs/synwevo_docs/engine/sheet_render.py` | 188 | `613b7eded0` | 完整 | 引擎：Office/表格 | openpyxl-based XLSX grid windows (values, cached formula results, styles, merges) and parsed-workbook windows |
| `apps/docs/synwevo_docs/engine/sources.py` | 218 | `f2318f8b8f` | 完整 | 引擎：Office/表格 | Source parsing entry: detect -> optional LO conversion -> tabular/DOCX/PDF/image/PPTX/text summaries and signatures; HTML table/text extract |
| `apps/docs/synwevo_docs/engine/tabular.py` | 498 | `8a4806099f` | 完整 | 引擎：Office/表格 | CSV/XLSX/XLS readers into typed Workbook/Sheet model, cell-count limit, formula scan and missing-cache computation, table views and column r |
| `apps/docs/synwevo_docs/engine/templates.py` | 807 | `7ed00b9cb1` | 完整 | 引擎：计划/公式/模板 | Template field (slot) creation for DOCX/XLSX/PDF/image and filling (docx_fill, xlsx_fill, pdf_fill, image_fill) |
| `apps/docs/synwevo_docs/engine/transforms.py` | 238 | `f02928a6b1` | 完整 | 引擎：计划/公式/模板 | Fixed set of value transforms, validation, group aggregation and multi-source combine |
| `apps/docs/synwevo_docs/engine/values.py` | 142 | `2235855f60` | 完整 | 引擎：计划/公式/模板 | Typed Value model (str/int/dec/float/date/...), float_repr, from_python, as_decimal |
| `apps/docs/synwevo_docs/engine/xlsx_cache.py` | 57 | `f3775e4144` | 完整 | 引擎：Office/表格 | LibreOffice recalculation fallback: writes LO-computed values back as cached values into the generated XLSX |
| `apps/docs/synwevo_docs/engine/xlsx_doc.py` | 683 | `555664783b` | 完整 | 引擎：Office/表格 | Direct XLSX XML editing: cell writes via shared strings, merges, shared-formula expansion, row insertion/deletion with dependent-structure s |
| `apps/docs/synwevo_docs/jobs.py` | 927 | `ff07a21f59` | 完整 | Docs 服务与任务 | Generation job service: naming, ProjectContext (plans), output verification, create/idempotency, claim/lease/heartbeat/recover, cancel/retry |
| `apps/docs/synwevo_docs/licensing.py` | 176 | `6f3136edff` | 完整 | Docs 接口层 | Third-party licence report and AGPL §13 Corresponding Source tarball builder |
| `apps/docs/synwevo_docs/locators.py` | 337 | `e16930cc13` | 完整 | Docs 服务与任务 | Turns UI selections into stored locators (build_locator) and resolves stored locators back to on-screen positions (locate_all/locate/docx_te |
| `apps/docs/synwevo_docs/regression.py` | 273 | `665ebef1aa` | 完整 | Docs 服务与任务 | Regression cases: capture a project (raw source bytes, template, expected values/text) to a directory and replay it through build_plan + fil |
| `apps/docs/synwevo_docs/services.py` | 824 | `46633d62d2` | 完整 | Docs 服务与任务 | App service layer: projects, sources (upload/parse pool/options/desensitize), SourceDoc cache, templates and versions, slots, mapping revisi |
| `apps/docs/synwevo_docs/views.py` | 396 | `a1573825d1` | 完整 | Docs 服务与任务 | Read models: source viewers/search/page images/words/OCR/corrections, template view, records, rule preview, preview (plan+fill_checked) with |
| `apps/docs/synwevo_docs/webhooks.py` | 301 | `6e8d760baf` | 完整 | Docs 接口层 | Deployment-level signed webhook dispatcher: event cursor, delivery table, backoff, SSRF check with pinned connection |
| `apps/docs/tests/conftest.py` | 43 | `34b8f78abf` | 完整 | Docs 测试 A | Suite fixtures: synthetic testdata generator (make_fixtures.py into repo-root testdata/), per-test create_app(tmp data dir) TestClient, Api  |
| `apps/docs/tests/helpers.py` | 128 | `9f2c5c6d70` | 完整 | Docs 测试 A | Api wrapper over workbench HTTP routes (project/upload/template/slot/rule/maps/job polling/outputs) and docx_text extractor |
| `apps/docs/tests/test_acceptance_1_4.py` | 212 | `1a3a05450e` | 完整 | Docs 测试 A | End-to-end scenarios 1-4: per-row 100 DOCX, per-file 20 DOCX with locators, per-group 15 orders (DOCX and XLSX with formula expansion), regi |
| `apps/docs/tests/test_acceptance_5_8.py` | 197 | `b7ed70d08f` | 完整 | Docs 测试 A | Scenarios 5-8: region fill/size mismatch blocking, repeat 0/1/many + PDF pagination, XLSX zero-row removal, one-to-many/conflicts, structure |
| `apps/docs/tests/test_acceptance_9_14.py` | 392 | `46e321a430` | 完整 | Docs 测试 A | Scenarios 9-14: template byte-preservation, PDF/OCR sources and PDF/form templates, persistence across restart, snapshot isolation, partial  |
| `apps/docs/tests/test_api_docs.py` | 64 | `a762c5f1cb` | 完整 | Docs 测试 A | OpenAPI file regenerated by CLI must equal committed file; spec only lists /api/v1 + probes; example-definition.json drives a real job |
| `apps/docs/tests/test_api_v1.py` | 302 | `a7f90540f4` | 完整 | Docs 测试 A | Machine API: key auth/scopes/revocation, tenant isolation (projects only), headless flow with checksums/events/definition round-trip, idempo |
| `apps/docs/tests/test_cache_headers.py` | 161 | `eb8514cffa` | 完整 | Docs 测试 A | Cache-Control per response class (entry HTML, hashed assets, preview with ETag/304), editor body 413 guard |
| `apps/docs/tests/test_claims.py` | 75 | `0c644f0572` | 完整 | Docs 测试 A | Engine claims from docs/FORMATS.md: XLSX row insert shifts tables/print area/charts/comments/drawings; scanned PDF template pixel clearing |
| `apps/docs/tests/test_cleanup_tool.py` | 191 | `c481f096a0` | 完整 | Docs 测试 A | CLI `projects list/prune` classifies by origin evidence (dry-run default, delete needs ack); blob_audit inventory sees JSON-embedded refs, g |
| `apps/docs/tests/test_docx_blocks.py` | 189 | `5035a06e17` | 完整 | Docs 测试 A | Repeating paragraph blocks in DOCX: column detection, fill, separators, zero-row policies, column add/remove, selection rules, built-in edit |
| `apps/docs/tests/test_engine.py` | 232 | `9a7e10d407` | 完整 | Docs 测试 A | Engine unit tests: number formats, value fidelity, CSV encodings, transforms, detection, refshift, DOCX cross-run fields, XLSX row insert, P |
| `apps/docs/tests/test_formats.py` | 232 | `757d801349` | 完整 | Docs 测试 A | PPTX source/template, legacy formats via LibreOffice, HTML/TXT/MD sources, CSV export, built-in doc/sheet editors, upload guards (path, corr |
| `apps/docs/tests/test_formula.py` | 149 | `7b9504a1cb` | 完整 | Docs 测试 A | Built-in formula engine semantics, errors/cycles, computed caches without LibreOffice, source formulas without cache, 86-formula cross-check |
| `apps/docs/tests/test_formula_isolation.py` | 123 | `ba15e99b24` | 完整 | Docs 测试 A | Each generated XLSX carries its own computed cache values; stale template caches replaced |
| `apps/docs/tests/test_image_raster.py` | 105 | `96f55b0817` | 完整 | Docs 测试 A | Image templates without a PDF engine (pdfium): capability split, full flow with pixel-identity outside the field, repeat refusal reason, ove |
| `apps/docs/tests/test_image_template.py` | 141 | `cd46645a95` | 完整 | Docs 测试 A | PNG/JPEG templates: pixel-table detection, ink/background sampling, end-to-end output size/DPI/gradient, JPEG+PDF export, slot rules, EXIF/a |
| `apps/docs/tests/test_instance_guard.py` | 107 | `c9c57e4b0b` | 完整 | Docs 测试 B | Store instance_kind stamping (test vs normal), /healthz minimal body, /readyz no-write probe |
| `apps/docs/tests/test_job_reliability.py` | 256 | `2d41045bcb` | 完整 | Docs 测试 B | JobService semantics: idempotent create, concurrent same key, all-failed status, cancel arbitration, retry runs, lease recovery, publication |
| `apps/docs/tests/test_large_sheet.py` | 47 | `7577fad4e1` | 完整 | Docs 测试 B | Grid windows of a 4000x20 sheet are slices of a cached workbook (no Workbook.from_json rebuild) |
| `apps/docs/tests/test_layout.py` | 167 | `59c139f24f` | 完整 | Docs 测试 B | LibreOffice pagination guard: warn/fit modes, shrink-to-fit, preview-layout, CJK font, layout report honesty |
| `apps/docs/tests/test_licensing.py` | 120 | `5f2ae8cc9b` | 完整 | Docs 测试 B | AGPL notice follows installed PyMuPDF; source archive completeness; Dockerfile COPY coverage; NOTICE vs THIRD_PARTY |
| `apps/docs/tests/test_listen_guard.py` | 68 | `529e7bcd29` | 完整 | Docs 测试 B | check_listen_config refuses public bind without UI auth; run.py actually calls it (subprocess) |
| `apps/docs/tests/test_migration_v3.py` | 104 | `4fc242dee6` | 完整 | Docs 测试 B | Additive migration from v1/v2 adds V3 columns; idempotent; Store(migrate=False) does not bump schema version |
| `apps/docs/tests/test_migration_v4.py` | 112 | `2ee9bcd7b6` | 完整 | Docs 测试 B | v3->v4: rows untouched, legacy 'default' workspace pending_claim with zero members, CHECK constraints, normalized email uniqueness |
| `apps/docs/tests/test_pdf_repeat.py` | 167 | `767e5d3d67` | 完整 | Docs 测试 B | PDF repeat-detail areas: detection, overflow error/continue/shrink, rotation, slot grid edit, layout validation |
| `apps/docs/tests/test_pdfium_engine.py` | 102 | `851e5ee42a` | 完整 | Docs 测试 B | pdfium engine geometry parity with PyMuPDF, PDF source flow, template refusal, page-level blocks, OCR, /api/about and /api/source-code |
| `apps/docs/tests/test_pdfium_threading.py` | 66 | `f2ae00514d` | 完整 | Docs 测试 B | Subprocess repro: 200 concurrent pdfium reads on 8 threads must not crash; LOCK is RLock |
| `apps/docs/tests/test_per_block.py` | 121 | `6820c29a84` | 完整 | Docs 测试 B | per_block record rule across xlsx/docx/pdf/txt: split, locate, block-scoped views/search, preflight blocking with record+field location, gen |
| `apps/docs/tests/test_project_archive.py` | 66 | `bb189eca43` | 完整 | Docs 测试 B | Project archive vs soft delete, list fields, origin provenance (ui vs api:<key>) |
| `apps/docs/tests/test_raster_text.py` | 144 | `008a3512bb` | 完整 | Docs 测试 B | Pillow raster text layout matches PyMuPDF insert_textbox; wrapping, shrink floor, ink stays in box, fractional sizes |
| `apps/docs/tests/test_regression_cases.py` | 104 | `c084a01055` | 完整 | Docs 测试 B | Regression case capture/replay/diff/download/delete, CLI script, desensitize copies |
| `apps/docs/tests/test_slot_options.py` | 78 | `8b93dbdbf3` | 完整 | Docs 测试 B | Slot enum validation (empty_policy, data_type, align, valign) on create and PATCH |
| `apps/docs/tests/test_snapshot.py` | 158 | `29213d63c2` | 完整 | Docs 测试 B | scripts/snapshot.py create/verify/restore, missing-blob detection, credential-revival warnings, 0600 archive, ../ path refusal |
| `apps/docs/tests/test_webhooks.py` | 226 | `bd1de60968` | 完整 | Docs 测试 B | Env-configured webhook dispatcher: HMAC signature, no duplicate, 5xx backoff, 4xx no retry, SSRF allowlist, DNS pinning, no ambient proxy |
| `apps/docs/tests/test_workflow.py` | 82 | `36a2ec86e2` | 完整 | Docs 测试 B | Per-file locator overrides, undo/redo with slot-delete cascade, preview invalidation, stale base_rev 409, template duplicate isolation |
| `apps/docs/web/index.html` | 13 | `64ee91e38d` | 完整 | 工作台页面 | SPA HTML shell (lang zh-CN, inline SVG favicon, loads /src/main.tsx) |
| `apps/docs/web/package.json` | 39 | `bd62c8e785` | 完整 | 工作台页面 | @synwevo/docs-web workspace manifest: React 19, react-router 7, Tiptap 3, Vite 8, TS 7; scripts dev/build/typecheck |
| `apps/docs/web/src/components/AnyView.tsx` | 62 | `d2e176264a` | 完整 | 工作台组件 | Dispatcher choosing Grid/Doc/Pdf/Slides/Text view from a /view response; loading/pending/error states |
| `apps/docs/web/src/components/CommandPalette.tsx` | 83 | `c13f8d8097` | 完整 | 工作台组件 | Cmd/Ctrl+K palette listing Workspace commands with filter and arrow/Enter/Esc handling |
| `apps/docs/web/src/components/Connectors.tsx` | 69 | `2ee5922c8d` | 完整 | 工作台组件 | SVG curves linking source/target marks of enabled mappings; recomputed on scroll/resize/800ms interval |
| `apps/docs/web/src/components/DesensitizeDialog.tsx` | 107 | `f1aa5dd9e0` | 完整 | 工作台组件 | Dialog calling POST /api/projects/{pid}/desensitize and offering downloads of desensitized copies |
| `apps/docs/web/src/components/FilePanel.tsx` | 114 | `ccbd4d1a7c` | 完整 | 工作台组件 | Source file list: upload (per-file multipart), drag reorder, set representative, download original, remove |
| `apps/docs/web/src/components/GenerateDialog.tsx` | 147 | `a46f2e4498` | 完整 | 工作台组件 | Batch generation dialog: debounced preflight (generation-summary), formats/naming/error policy/layout, POST job with request_id |
| `apps/docs/web/src/components/MappingDialog.tsx` | 260 | `7bef89e1a5` | 完整 | 工作台组件 | Create/edit/merge/replace a mapping (single/region/repeat) with transforms, aggregation, value mode, style |
| `apps/docs/web/src/components/MoreMenu.tsx` | 43 | `96b2911860` | 完整 | 工作台组件 | Overflow menu button with arrow-key navigation |
| `apps/docs/web/src/components/PdfRepeatEditor.tsx` | 189 | `0828e9beaa` | 完整 | 工作台组件 | Column/row-height editor for PDF repeat areas (detect via POST pdf-repeat-detect, drag dividers, split/merge) |
| `apps/docs/web/src/components/PreviewPane.tsx` | 125 | `1bbb8ebcd4` | 完整 | 工作台组件 | Real-sample preview: GET /preview, optional LibreOffice layout, download preview file, issue list with locate |
| `apps/docs/web/src/components/RegressionDialog.tsx` | 48 | `cefb394ae3` | 完整 | 工作台组件 | Saves current project as a regression case via POST /api/projects/{pid}/regression-cases |
| `apps/docs/web/src/components/RuleDialog.tsx` | 304 | `796cf18e74` | 完整 | 工作台组件 | Record rule editor (per_row/per_file/per_group/per_block) with debounced rule-preview |
| `apps/docs/web/src/components/Shortcuts.tsx` | 67 | `d9e75c0c13` | 完整 | 工作台组件 | Static keyboard-shortcut help modal |
| `apps/docs/web/src/components/SidePanel.tsx` | 480 | `7ee3b1fecc` | 完整 | 工作台组件 | Right panel: mapping list (keyboard multi-select, bulk enable/delete, rebind), field editor, samples list, per-file validation with OCR corr |
| `apps/docs/web/src/components/SlotDialog.tsx` | 262 | `1f7c5b2f62` | 完整 | 工作台组件 | Create template field from a selection (text/cell/repeat/region/repeat_block/pdf_box/pdf_repeat/pptx) |
| `apps/docs/web/src/components/TemplateChooser.tsx` | 115 | `32e9305a0d` | 完整 | 工作台组件 | Set project template: upload, new builtin, or pick/duplicate from library |
| `apps/docs/web/src/components/Transforms.tsx` | 78 | `81674af473` | 完整 | 工作台组件 | Controlled-op value transform editor (trim, date format, decimals, map, pad...) |
| `apps/docs/web/src/lib/describe.ts` | 123 | `e394ce6eaa` | 完整 | 工作台页面 | Chinese labels for mapping/slot/locator/selection kinds; slotPos maps template slots to viewer positions |
| `apps/docs/web/src/lib/latest.ts` | 29 | `50dbbf593b` | 完整 | 工作台页面 | useLatest hook: abort previous request, drop stale answers (used by RuleDialog/GenerateDialog only) |
| `apps/docs/web/src/lib/status.ts` | 50 | `a8a2bff0ec` | 完整 | 工作台页面 | Single job/item status -> Chinese label+tone map, rule mode labels |
| `apps/docs/web/src/lib/types.ts` | 152 | `3a5ab9ce3b` | 完整 | 工作台页面 | TS interfaces for SourceFile, Slot, TemplateDetail, Mapping, RecordRule, Project, viewer Pos/Mark/Selection |
| `apps/docs/web/src/main.tsx` | 64 | `d27bfb8cf6` | 完整 | 工作台页面 | Router + providers; every route wrapped in RequireSession; lazy DocEditor/SheetEditor; EditorSwitch for /templates/:tid/edit |
| `apps/docs/web/src/pages/About.tsx` | 37 | `147640f053` | 完整 | 工作台页面 | About/licence page: third-party table from /api/about |
| `apps/docs/web/src/pages/Capabilities.tsx` | 57 | `e36136ebf4` | 完整 | 工作台页面 | Format capability matrix from /api/capabilities (live environment: LibreOffice, Tesseract) |
| `apps/docs/web/src/pages/DocEditor.tsx` | 362 | `ea219ffa3c` | 完整 | 工作台页面 | Tiptap-based builtin DOCX template editor (fields, repeat rows/blocks, page setup, image downscale) saving via PUT/POST /api/templates/.../b |
| `apps/docs/web/src/pages/Home.tsx` | 228 | `cff0d51b9a` | 完整 | 工作台页面 | Project list (client-side search/sort/archived toggle, tbody keyboard cursor) and template library with row menus |
| `apps/docs/web/src/pages/JobPage.tsx` | 158 | `272e14740c` | 完整 | 工作台页面 | Job progress page polling /api/jobs/{jid} + /items (limit 5000) every 900ms; cancel/retry; zip/CSV/artifact downloads; client-side status fi |
| `apps/docs/web/src/pages/Regression.tsx` | 75 | `32f93fa91c` | 完整 | 工作台页面 | Regression-case list/run/download/delete page |
| `apps/docs/web/src/pages/SheetEditor.tsx` | 242 | `b3496f27ce` | 完整 | 工作台页面 | Custom div-grid builtin XLSX template editor (cells, styles, merges, fields, sheets) saving via builtin routes |
| `apps/docs/web/src/pages/Workspace.tsx` | 866 | `cbc1c2134f` | 完整 | 工作台页面 | Main mapping workbench: source/template viewers, selection->slot->mapping, rule dialog, relocation overrides, find, OCR, CSV options, comman |
| `apps/docs/web/src/views/DocView.tsx` | 391 | `4282b69167` | 完整 | 工作台组件 | DOCX renderer with text/cell/row/table/paragraph-block selection and mark overlays |
| `apps/docs/web/src/views/GridView.tsx` | 448 | `a9a9b2e797` | 完整 | 工作台组件 | Virtualized spreadsheet grid (200-row windows), drag/shift-click/arrow selection, marks, sheet tabs |
| `apps/docs/web/src/views/PdfView.tsx` | 152 | `435a28e9ab` | 完整 | 工作台组件 | PDF/image page renderer with region drawing, word hit-testing, marks, page tracking |
| `apps/docs/web/src/views/SlidesView.tsx` | 91 | `fc176a123c` | 完整 | 工作台组件 | PPTX slide renderer with text/cell/row selection |
| `apps/docs/web/src/views/TextView.tsx` | 60 | `7c163cca24` | 完整 | 工作台组件 | Plain-text line renderer with span selection and marks |
| `apps/docs/web/src/vite-env.d.ts` | 1 | `11f02fe2a0` | 完整 | 工作台页面 | Vite client type reference |
| `apps/docs/web/tsconfig.json` | 17 | `949d0eb3c7` | 完整 | 工作台页面 | Strict TS config, noEmit, bundler resolution, types: [] |
| `apps/docs/web/vite.config.ts` | 13 | `c0ae305077` | 完整 | 工作台页面 | Vite build (dist/assets) and dev server 5173 proxying only /api to 127.0.0.1:8765 |
| `apps/portal/pyproject.toml` | 17 | `cac7e9a1fc` | 完整 | 门户/接入/UI包/组装/脚本 | Portal package metadata |
| `apps/portal/requirements.txt` | 3 | `21b1b6f9c0` | 完整 | 门户/接入/UI包/组装/脚本 | Portal pinned deps (delegates to auth-client) |
| `apps/portal/synwevo_portal/__init__.py` | 9 | `e445dec87f` | 完整 | 门户/接入/UI包/组装/脚本 | Portal package docstring/boundary statement |
| `apps/portal/synwevo_portal/app.py` | 53 | `97e3558d7d` | 完整 | 门户/接入/UI包/组装/脚本 | Standalone portal ASGI factory (gate, portal API, probes, session and RP routers, shell) |
| `apps/portal/synwevo_portal/routes.py` | 81 | `10527f1d70` | 完整 | 门户/接入/UI包/组装/脚本 | GET /api/portal/home and POST /api/portal/workspace |
| `apps/portal/web/index.html` | 13 | `0893ca69c5` | 完整 | 门户/接入/UI包/组装/脚本 | Portal shell HTML |
| `apps/portal/web/package.json` | 29 | `ef31186df8` | 完整 | 门户/接入/UI包/组装/脚本 | Portal web workspace manifest |
| `apps/portal/web/src/Portal.tsx` | 223 | `98c9183845` | 完整 | 门户/接入/UI包/组装/脚本 | Portal home UI: loading, signed-out, error and ready states, workspace chips, app cards |
| `apps/portal/web/src/main.tsx` | 11 | `ae8db405dd` | 完整 | 门户/接入/UI包/组装/脚本 | Portal React entry |
| `apps/portal/web/src/portal.css` | 134 | `164b66130d` | 完整 | 门户/接入/UI包/组装/脚本 | Portal layout styles on shared tokens |
| `apps/portal/web/src/vite-env.d.ts` | 1 | `11f02fe2a0` | 完整 | 门户/接入/UI包/组装/脚本 | Vite types |
| `apps/portal/web/tsconfig.json` | 17 | `949d0eb3c7` | 完整 | 门户/接入/UI包/组装/脚本 | TS config |
| `apps/portal/web/vite.config.ts` | 13 | `020a62f3d6` | 完整 | 门户/接入/UI包/组装/脚本 | Vite build/dev config (portal-assets) |
| `conftest.py` | 21 | `480ad80cad` | 完整 | 门户/接入/UI包/组装/脚本 | Root pytest fixture platform_root |
| `docs/ACCEPTANCE.md` | 71 | `216fdf74f7` | 完整 | 其余文档 | Acceptance results for the 14 Synwevo Docs scenarios, Office check and benchmark numbers |
| `docs/ARCHITECTURE.md` | 100 | `cb425fe880` | 完整 | 其余文档 | Synwevo Docs engine and module architecture, data model, preview==generation path, job lifecycle, security measures |
| `docs/DEPENDENCIES.md` | 93 | `f4c5733db9` | 完整 | 其余文档 | Third-party components, licences, system tools, AGPL-free pdfium mode and AGPL source offer |
| `docs/DEPLOY.md` | 62 | `63caec8183` | 完整 | 其余文档 | Local and Docker run/deploy, UI auth modes, reverse proxy, limits, upgrade |
| `docs/FORMATS.md` | 94 | `65a6ed1c76` | 完整 | 其余文档 | Per-format capability matrix, format rules (formula engine, XLSX row insert, DOCX/PDF/OCR/PPTX), gaps |
| `docs/TESTING.md` | 59 | `951e656e35` | 完整 | 其余文档 | Fixture generation, pytest suites, browser E2E scripts, benchmark, manual checklist |
| `docs/USER_GUIDE.md` | 71 | `25a146e0be` | 完整 | 其余文档 | End-user walkthrough of the workbench (sources, templates, samples, mappings, preview/generate) |
| `docs/architecture/monorepo.md` | 141 | `43b6c0a0f9` | 完整 | 其余文档 | Current monorepo layout, dependency direction, data ownership, data-dir resolution, roles, shells |
| `docs/architecture/platform.md` | 179 | `b57c33d8bd` | 完整 | 其余文档 | Platform identity architecture: Authlib OIDC, *.localhost topology, id concepts, permission layers, progress table |
| `docs/design.md` | 118 | `3e0b65ad97` | 完整 | 其余文档 | Design record: UX audit, references, tokens, layout, interaction/IME rules, motion, a11y, frame timing |
| `docs/development/local-platform.md` | 332 | `2f90820c0d` | 完整 | 其余文档 | Clean-environment local platform startup: setup, up, data dir, bootstrap, clients, mail, passkeys, verification, env vars |
| `docs/integration/api.md` | 302 | `5552883bfd` | 完整 | 接入与安全文档 | Machine API (/api/v1) contract: auth (dw_ keys + platform tokens), error envelope, routes, job semantics, downloads, events/webhooks, limits |
| `docs/integration/app-onboarding.md` | 370 | `a13db090ab` | 完整 | 接入与安全文档 | How an app joins the platform identity: app/client registration CLI, RP endpoints, SSO/logout, machine identity, keys, repo layout for new a |
| `docs/integration/automation.md` | 160 | `3391444bee` | 完整 | 接入与安全文档 | End-to-end headless automation guide (script, curl, retries, links, webhook verification) |
| `docs/integration/example-definition.json` | 92 | `16e4894064` | 完整 | 接入与安全文档 | Sample fields+rule+mappings definition used by automation_example.py and test_api_docs.py |
| `docs/integration/openapi.yaml` | 1187 | `48dd3b74e2` | 完整 | 接入与安全文档 | Generated OpenAPI of /api/v1 + probes (python -m synwevo_docs.cli openapi) |
| `docs/integration/ports.md` | 286 | `37127ad3f5` | 完整 | 接入与安全文档 | Ports, bind addresses, role filter matrix, OIDC/RP endpoints, reverse proxy, production instance notes |
| `docs/migrations/platform-restructure.md` | 261 | `2ea1d96306` | 完整 | 其余文档 | Execution record of the DocWeave→platform monorepo restructure (baseline, backup, mapping, phases, verification) |
| `docs/migrations/synwevo-rebrand.md` | 203 | `62e9227755` | 完整 | 其余文档 | Synwevo rebrand record: name/path/package mapping, env compatibility rules, retained identifiers, remote switch, rollback |
| `docs/operations.md` | 91 | `be9de34566` | 完整 | 其余文档 | Ops runbook: production start, persistence/backup, upgrade/migration, daily ops, troubleshooting |
| `docs/quality/issues.md` | 118 | `c1f0760901` | 完整 | 其余文档 | Issue register DW-001…DW-061, earlier fixes and restructure fixes PR-001…PR-007 |
| `docs/quality/progress.md` | 322 | `ffd4cb8bca` | 完整 | 其余文档 | Hand-off progress log across rounds; remaining items and measured long synchronous requests |
| `docs/quality/verification.md` | 528 | `f58d184788` | 完整 | 其余文档 | Commands actually run and their results per round (2026-09-23, restructure, rebrand) |
| `docs/security/identity-migration.md` | 241 | `103877a899` | 完整 | 接入与安全文档 | v4 migration model, legacy default workspace, claim flow, legacy auth modes, backup/restore/rollback |
| `docs/security/permissions.md` | 436 | `564fb8cef9` | 完整 | 接入与安全文档 | Authorization contract: trust boundaries, identity kinds, machine identity, webhooks, role matrix, bypass list, job re-auth, progress table |
| `docs/security/sessions.md` | 277 | `9a6a6b46e5` | 完整 | 接入与安全文档 | Session-layer contract: three session layers, cookies, clocks, CSRF, SSO, reauth, logout/revocation timings |
| `infra/Dockerfile` | 67 | `99e6149112` | 完整 | 门户/接入/UI包/组装/脚本 | Two-stage image (npm ci build of three shells, then Python runtime with role=all) |
| `infra/docker-compose.yml` | 17 | `67cd4bbd97` | 完整 | 门户/接入/UI包/组装/脚本 | Compose deployment |
| `infra/server/pyproject.toml` | 20 | `a7225f8893` | 完整 | 门户/接入/UI包/组装/脚本 | Composer package metadata |
| `infra/server/synwevo_server/__init__.py` | 13 | `b2068716f8` | 完整 | 门户/接入/UI包/组装/脚本 | Composer docstring (roles) |
| `infra/server/synwevo_server/app.py` | 119 | `42e61cd007` | 完整 | 门户/接入/UI包/组装/脚本 | Role composer: gate, probes, session routes, docs/identity/portal install, RP routers, shells |
| `infra/server/synwevo_server/main.py` | 4 | `cecff25310` | 完整 | 门户/接入/UI包/组装/脚本 | uvicorn import target |
| `infra/server/synwevo_server/run.py` | 32 | `dd71b2e4d5` | 完整 | 门户/接入/UI包/组装/脚本 | Process entry: listen-config guard plus uvicorn |
| `package.json` | 24 | `5684fea186` | 完整 | 门户/接入/UI包/组装/脚本 | npm workspaces root |
| `packages/AGENTS.md` | 20 | `6efb7f09fc` | 完整 | 门户/接入/UI包/组装/脚本 | Shared-package rules |
| `packages/auth-client/python/pyproject.toml` | 17 | `b2796f7453` | 完整 | 门户/接入/UI包/组装/脚本 | auth-client package metadata |
| `packages/auth-client/python/requirements.txt` | 5 | `cbf0a492ee` | 完整 | 门户/接入/UI包/组装/脚本 | Pins httpx/authlib |
| `packages/auth-client/python/synwevo_auth_client/__init__.py` | 9 | `3e96e2f971` | 完整 | 门户/接入/UI包/组装/脚本 | Docstring |
| `packages/auth-client/python/synwevo_auth_client/routes.py` | 195 | `29c96fb049` | 完整 | 门户/接入/UI包/组装/脚本 | RP HTTP: /auth/start, /auth/callback, /api/auth/sign-out, /auth/backchannel-logout, account-path redirects |
| `packages/auth-client/python/synwevo_auth_client/rp.py` | 247 | `e35c0c6e37` | 完整 | 门户/接入/UI包/组装/脚本 | OIDC RP core: state/nonce/PKCE rows, code exchange, id_token verification, back-channel logout, sweep |
| `packages/auth-client/python/synwevo_auth_client/session_routes.py` | 132 | `0e49ce674c` | 完整 | 门户/接入/UI包/组装/脚本 | /api/auth/state, /logout, /me, /workspace for every role |
| `packages/auth-client/web/package.json` | 21 | `0f81d2495c` | 完整 | 门户/接入/UI包/组装/脚本 | Web auth-client manifest |
| `packages/auth-client/web/src/PlatformNav.tsx` | 72 | `eeb641feff` | 完整 | 门户/接入/UI包/组装/脚本 | Docs top bar: back to portal, workspace select, account link |
| `packages/auth-client/web/src/api.ts` | 161 | `660abe4124` | 完整 | 门户/接入/UI包/组装/脚本 | fetch/XHR client with CSRF echo and gateway attribution |
| `packages/auth-client/web/src/index.ts` | 9 | `5b45332f60` | 完整 | 门户/接入/UI包/组装/脚本 | Barrel export |
| `packages/auth-client/web/src/session.tsx` | 139 | `ce74e9795b` | 完整 | 门户/接入/UI包/组装/脚本 | SessionProvider, RequireSession, signInUrl, safeNext |
| `packages/core/pyproject.toml` | 20 | `d35f71f912` | 完整 | 共享核心 | Packaging metadata for synwevo-core (abstract deps: fastapi, starlette, argon2-cffi) |
| `packages/core/requirements.txt` | 8 | `eea02fb7f0` | 完整 | 共享核心 | Pinned versions for the core package (fastapi, starlette, uvicorn, multipart, pydantic, argon2-cffi) |
| `packages/core/synwevo_core/__init__.py` | 10 | `ad50756790` | 完整 | 共享核心 | Package docstring: Store is the only DB opener and the only migrator |
| `packages/core/synwevo_core/apps.py` | 227 | `bbd01978b2` | 完整 | 共享核心 | App registry, workspace enablement, entitlements, check_access / check_access_for_workspace, bootstrap_docs_app |
| `packages/core/synwevo_core/auth.py` | 247 | `9bfb5fe150` | 完整 | 共享核心 | Legacy dw_ API keys, UI auth modes (none/basic/proxy/session), listen-config guard, server root secret, signed download links, in-process ra |
| `packages/core/synwevo_core/config.py` | 34 | `511516b608` | 完整 | 共享核心 | Platform/product names, platform_name(), portal_url() |
| `packages/core/synwevo_core/context.py` | 39 | `c4ad790c24` | 完整 | 共享核心 | Request-scoped tenant ContextVar (default UI_TENANT='default') |
| `packages/core/synwevo_core/env.py` | 102 | `22e0ee2d62` | 完整 | 共享核心 | SYNWEVO_/SYNWEVO_DOCS_/DOCWEAVE_ config compatibility reader with CRITICAL conflict refusal |
| `packages/core/synwevo_core/errors.py` | 20 | `7bb794a173` | 完整 | 共享核心 | ServiceError (shared error contract) and not_found helper |
| `packages/core/synwevo_core/http.py` | 538 | `ef0b299f2f` | 完整 | 共享核心 | build_platform, error handlers, request gate middleware (role boundary, machine auth, session/CSRF/tenant), probes, static shells |
| `packages/core/synwevo_core/identity.py` | 370 | `34b1035b69` | 完整 | 共享核心 | Users, authentication with rate buckets, workspaces, memberships, last-owner/last-admin guards, bootstrap tokens, registration mode |
| `packages/core/synwevo_core/machine.py` | 171 | `da96baa6bf` | 完整 | 共享核心 | Resolves /api/v1 callers: legacy dw_ keys, service accounts, user delegations; scope check |
| `packages/core/synwevo_core/oidc.py` | 531 | `7a92aa5c3a` | 完整 | 共享核心 | OIDC keys, clients, codes, tokens (hash storage, refresh rotation), id/logout tokens, logout delivery and verification, discovery |
| `packages/core/synwevo_core/passwords.py` | 167 | `8ea38a19a1` | 完整 | 共享核心 | Argon2id hashing with a concurrency semaphore, password policy |
| `packages/core/synwevo_core/sessions.py` | 242 | `dd8d6bd21a` | 完整 | 共享核心 | Server-side sessions (hashed cookie ids, idle/absolute clocks, reauth scope), CSRF double-submit, cookie attributes |
| `packages/core/synwevo_core/store.py` | 740 | `bd32339baa` | 完整 | 共享核心 | SQLite schema v1-v4 plus unconditional column migrations, legacy workspace seeding, tx/locking, content-addressed blob store |
| `packages/ui/package.json` | 19 | `c7605fa381` | 完整 | 门户/接入/UI包/组装/脚本 | UI package manifest |
| `packages/ui/src/index.ts` | 8 | `5abca7514b` | 完整 | 门户/接入/UI包/组装/脚本 | Barrel export |
| `packages/ui/src/styles.css` | 532 | `7229de0467` | 完整 | 门户/接入/UI包/组装/脚本 | Design tokens, dark theme, reduced motion, and most Docs component CSS |
| `packages/ui/src/theme.ts` | 62 | `5d6fa775b2` | 完整 | 门户/接入/UI包/组装/脚本 | Theme and localStorage remember/recall with legacy docweave.* fallback |
| `packages/ui/src/ui.tsx` | 135 | `f569b93c1d` | 完整 | 门户/接入/UI包/组装/脚本 | Toast, Modal, confirm/prompt dialog, formatting helpers |
| `packages/ui/src/useFit.ts` | 16 | `6123a36075` | 完整 | 门户/接入/UI包/组装/脚本 | ResizeObserver scale hook |
| `pyproject.toml` | 6 | `565b167bce` | 完整 | 门户/接入/UI包/组装/脚本 | Root pytest config |
| `requirements-dev.txt` | 10 | `b30e421078` | 完整 | 门户/接入/UI包/组装/脚本 | Dev/test pins aggregating all modules |
| `scripts/check_boundaries.py` | 110 | `e3749b787c` | 完整 | 门户/接入/UI包/组装/脚本 | AST import-boundary, sys.path, abs-path and symlink check (Python only) |
| `scripts/check_brand.py` | 109 | `2d9c27523b` | 完整 | 门户/接入/UI包/组装/脚本 | Legacy-brand residue classifier |
| `scripts/platform.py` | 439 | `34b43d6808` | 完整 | 门户/接入/UI包/组装/脚本 | Root CLI: setup/up/down/restart/status/logs/test/deps/backup/verify-restore |
| `services/identity/AGENTS.md` | 50 | `b8b59d318d` | 完整 | 账户服务后端 | Module charter: what the account service owns and doesn't, hard constraints, run and verify commands |
| `services/identity/pyproject.toml` | 20 | `9e5dd4e676` | 完整 | 账户服务后端 | Package metadata; abstract deps (no Docs dependency) |
| `services/identity/requirements.txt` | 11 | `687837ade6` | 完整 | 账户服务后端 | Version pins for the identity service (chains to auth-client and core) |
| `services/identity/synwevo_identity/__init__.py` | 12 | `36eec199de` | 完整 | 账户服务后端 | Package docstring (boundary statement) |
| `services/identity/synwevo_identity/admin_routes.py` | 288 | `b73ecc0712` | 完整 | 账户服务后端 | Platform admin API: overview, workspaces with counts, members, legacy claim, users list/status/revoke-sessions/platform-role, audit search |
| `services/identity/synwevo_identity/app.py` | 81 | `fa2a2d5664` | 完整 | 账户服务后端 | Account-service factory: mounts auth, invite, MFA, recovery, passkey, OIDC and admin routers; starts the mailer; runs app bootstrap and adop |
| `services/identity/synwevo_identity/auth_routes.py` | 678 | `a7fd585e65` | 完整 | 账户服务后端 | Routes for login (password, passkey, MFA ticket), first-admin bootstrap, reauth, own sessions, invitations, forgot/reset/verify-email, passk |
| `services/identity/synwevo_identity/cli.py` | 379 | `0269bdd837` | 完整 | 账户服务后端 | Operator CLI: bootstrap, ui-user, app registry and entitlements, OIDC client register/list/show/disable, signing keys, info |
| `services/identity/synwevo_identity/invites.py` | 250 | `fd2ff1e95a` | 完整 | 账户服务后端 | Invitation create/list/revoke/preview/accept and email tokens (verify/reset/email_change), stored as SHA-256 |
| `services/identity/synwevo_identity/mail.py` | 251 | `1ba4510303` | 完整 | 账户服务后端 | mail_outbox queue plus a worker thread (smtp/file/disabled) and message bodies with token links |
| `services/identity/synwevo_identity/mfa.py` | 265 | `843e9b3345` | 完整 | 账户服务后端 | TOTP seed AES-GCM encryption, enrolment and verification with step replay guard, recovery codes, MFA sign-in tickets |
| `services/identity/synwevo_identity/passkeys.py` | 271 | `5e05ed349e` | 完整 | 账户服务后端 | WebAuthn registration and usernameless authentication ceremonies; challenge storage and spend |
| `services/identity/synwevo_identity/provider_routes.py` | 353 | `5dcc136b37` | 完整 | 账户服务后端 | OIDC provider: discovery, JWKS, authorize, token (code/refresh/client_credentials), userinfo, revoke, RP-initiated logout with back-channel  |
| `services/identity/tests/conftest.py` | 22 | `cea3a73f56` | 完整 | 账户界面与测试 | Shared pytest fixture identity_client for the account-service app |
| `services/identity/tests/test_admin.py` | 285 | `b7e7dc82b6` | 完整 | 账户界面与测试 | Admin backend tests: 404 for non-admins, claiming legacy data (reauth, reason, audit, single claim), suspending users, revoking sessions and |
| `services/identity/tests/test_identity.py` | 335 | `c53037f1b1` | 完整 | 账户界面与测试 | Core identity tests: password policy and Argon2, email normalisation, login enumeration and backoff, session expiry and reauth scope, CSRF h |
| `services/identity/tests/test_invites.py` | 424 | `87ecdfae18` | 完整 | 账户界面与测试 | Invitation tests (issue, authz, role ceiling, supersede, accept, concurrent accept, inviter losing authority), email tokens, mail outbox, an |
| `services/identity/tests/test_mfa.py` | 312 | `040ce6afcd` | 完整 | 账户界面与测试 | TOTP enrolment and reauth, encrypted seed, replay and drift, the two-step login ticket, recovery codes, disabling TOTP |
| `services/identity/tests/test_oidc_flow.py` | 360 | `875cf221a4` | 完整 | 账户界面与测试 | Authorization-code flow end to end over HTTP: PKCE, redirects, scopes, code replay, userinfo, revocation, app entitlement, adoption and laun |
| `services/identity/tests/test_oidc_logout.py` | 261 | `84e8fffcb4` | 完整 | 账户界面与测试 | Refresh rotation and replay-family revocation, entitlement and suspension on refresh, RP-initiated logout, back-channel logout token |
| `services/identity/tests/test_oidc_provider.py` | 308 | `6621eb31a1` | 完整 | 账户界面与测试 | OIDC provider core: key persistence and rotation, client registration, codes, PKCE, id_token claims, token storage and revocation, discovery |
| `services/identity/tests/test_passkeys.py` | 327 | `3162e6239c` | 完整 | 账户界面与测试 | WebAuthn ceremonies driven by a software authenticator labelled as test equipment: register, sign in, replay, origin, RP ID, removal, last f |
| `services/identity/tests/test_recovery.py` | 260 | `718cedc636` | 完整 | 账户界面与测试 | Forgot/reset password and email verification over HTTP: enumeration resistance, preview not spending the token, single use, session and toke |
| `services/identity/web/index.html` | 13 | `978769f98a` | 完整 | 账户界面与测试 | Vite HTML entry for the account shell (zh-CN, inline SVG favicon) |
| `services/identity/web/package.json` | 33 | `b56a572b31` | 完整 | 账户界面与测试 | Account web package manifest: React 19, react-router 7, qrcode, workspace packages @synwevo/auth-client and @synwevo/ui |
| `services/identity/web/src/Admin.tsx` | 399 | `64617d9b4e` | 完整 | 账户界面与测试 | Platform admin console: overview, legacy-data ClaimCard, Users tab (suspend/restore, revoke sessions), Workspaces tab (read-only), Audit tab |
| `services/identity/web/src/components/MfaSection.tsx` | 203 | `6094a62bd7` | 完整 | 账户界面与测试 | TOTP enrol/confirm/disable and recovery-code regeneration, with an inline password reauth prompt |
| `services/identity/web/src/components/PasskeySection.tsx` | 243 | `65eb778dfb` | 完整 | 账户界面与测试 | Passkey list, rename, delete and enrol through navigator.credentials; exports toJSON and base64url helpers |
| `services/identity/web/src/main.tsx` | 76 | `d6ef4efa3c` | 完整 | 账户界面与测试 | Account shell router: /login, /invite, /forgot-password, /reset-password, /logged-out, /account, /admin, / and catch-all |
| `services/identity/web/src/pages/Account.tsx` | 184 | `55f85a7c90` | 完整 | 账户界面与测试 | Account and security centre: profile (read-only), workspace list, sessions list and revocation, plus PasskeySection and MfaSection |
| `services/identity/web/src/pages/Invite.tsx` | 166 | `fb7208c9bc` | 完整 | 账户界面与测试 | Invitation landing page: preview, wrong-account handling, password set for new accounts, accept |
| `services/identity/web/src/pages/Login.tsx` | 268 | `a7217454f1` | 完整 | 账户界面与测试 | Password login, MFA second step (TOTP or recovery code), passkey login, and first-admin bootstrap form |
| `services/identity/web/src/pages/Recover.tsx` | 161 | `8349ce134c` | 完整 | 账户界面与测试 | Forgot-password request page and reset-password page (preview, then POST) |
| `services/identity/web/src/vite-env.d.ts` | 1 | `11f02fe2a0` | 完整 | 账户界面与测试 | Vite client type reference |
| `services/identity/web/tsconfig.json` | 17 | `949d0eb3c7` | 完整 | 账户界面与测试 | Strict TypeScript config, bundler resolution, noEmit |
| `services/identity/web/vite.config.ts` | 13 | `1846c4d67e` | 完整 | 账户界面与测试 | Vite build (outDir dist, assets under account-assets) and dev server on 5175 proxying /api to 8770 |
| `tests/integration/automation_example.py` | 287 | `ab1b3f0b7a` | 完整 | 脚本与集成测试 | Headless API client: upload, fields, rule, mappings, preflight, job, wait, download and checksum; API key or client_credentials |
| `tests/integration/conftest.py` | 18 | `603419f6b2` | 完整 | 脚本与集成测试 | The 'composed' fixture (synwevo_server.create_app in session mode) |
| `tests/integration/migration_rehearsal.py` | 198 | `d1b6283b43` | 完整 | 脚本与集成测试 | Migrates a snapshot of a real DB in a temp dir and checks counts, row fingerprints and idempotency |
| `tests/integration/test_auth_gate.py` | 83 | `4b02684047` | 完整 | 脚本与集成测试 | Basic-auth gate has no bypass for /api-prefixed paths; probes are anonymous in every mode and role |
| `tests/integration/test_auth_http.py` | 277 | `e29373bec9` | 完整 | 脚本与集成测试 | Session-mode account HTTP: login, legacy workspace invisibility, CSRF, suspension, logout, bootstrap, audit |
| `tests/integration/test_authz_isolation.py` | 232 | `70f382408d` | 完整 | 脚本与集成测试 | Cross-workspace isolation for source/blob/locate/job-items routes, plus blob grants |
| `tests/integration/test_config_compat.py` | 95 | `fb66922f21` | 完整 | 脚本与集成测试 | SYNWEVO_*/DOCWEAVE_* config fallback and conflict refusal |
| `tests/integration/test_machine_identity.py` | 267 | `ce1ea80f95` | 完整 | 脚本与集成测试 | Service accounts and delegation: scope intersection, suspension, membership removal, demotion, revoke, app disable, legacy keys |
| `tests/integration/test_role_boundaries.py` | 94 | `de5db61ae0` | 完整 | 脚本与集成测试 | Per-role route refusal (not_in_this_role), password_login flag, legacy CSRF header |
| `tests/integration/test_shells.py` | 65 | `ff4eb69d63` | 完整 | 脚本与集成测试 | Each role serves its own frontend shell; the RP role redirects /login and /account |
| `tests/integration/test_sso_chain.py` | 416 | `c7d64c5c22` | 完整 | 脚本与集成测试 | Two-origin OIDC chain over ASGI with a host-scoped jar: start, callback integrity, entitlement, back-channel logout, auth_time |
| `tests/integration/verify_sso.py` | 207 | `70ae533df5` | 完整 | 脚本与集成测试 | Live-server SSO check with a real cookiejar across accounts, portal and docs |
