# 平台单仓库重组：路径映射、Git 保全、数据处理与恢复

本轮把 DocWeave 仓库改造成**平台母仓库**：一个 Git 仓库，内部清楚分离平台门户、统一账户服务、
DocWeave 与确有共用的包。本文件是执行记录：先写映射与恢复步骤，再实施。

上一轮的决定（`docs/architecture/platform.md` §1「不新建仓库、不搬目录」）在本轮被**显式替换**。
那条决定的理由仍然成立——一次改动同时动数据库路径、静态资源 base、导入路径和启动方式，是"用户数据
看起来不见了"最常见的来源。所以本轮不是取消那个顾虑，而是把它拆成**可验证的独立步骤**：目录重命名
与数据位置分开处理、纯移动与逻辑修复分开提交、每个边界完成后立刻跑针对性验证。

---

## 1. 迁移前基线（实测，不是沿用报告）

| 项目 | 值 |
|---|---|
| Git 根目录 | `/Users/lihiko/repo/wegreen/DocWeave` |
| 分支 | `upgrade/automation-api-ui` |
| HEAD | `e292a3e3ea4f4b1e615cdb14c655dfd80cf86cef` |
| remote | `origin git@github.com:WeGreen-AI/DocWeave.git`（fetch/push 同一地址） |
| 工作区状态 | `git status --porcelain` 为空：无已暂存、未暂存或未跟踪改动 |
| 分支清单 | `main`、`upgrade/automation-api-ui`、`origin/upgrade/automation-api-ui` |
| 标签 / stash / 子模块 / linked worktree | 均无；`.git` 是普通目录，内部无绝对路径 |
| 后端测试 | `644 passed, 1 skipped`（`pytest apps/docs/tests`，186 s，退出码 0） |
| 数据库 | `data/docweave.sqlite3`，schema v4，44 张表 |
| 真实数据 | 69 projects（全部 tenant=`default`）、80 templates、280 template_versions、201 mapping_revs、67 source_files、42 jobs、2175 job_items、1675 artifacts、29 job_runs、82 job_events、29 idempotency、2 api_keys |
| 账户数据 | `users` 0、`memberships` 0、`sessions` 0、`passkeys` 0、`totp_secrets` 0、`oidc_keys` 0 |
| `default` 工作空间 | `kind=legacy`、`status=pending_claim`、**0 成员**——历史数据仍待认领，本轮保持原状 |
| blob 存储 | `data/blobs/`，695 个文件 47.6 MB；每个文件内容的 SHA-256 **等于**其文件名（已逐个核对） |
| 密钥材料 | 全部在数据库内（`meta` 的服务端根密钥、`oidc_keys` 表）。**无 `.env`、无外部密钥文件** |
| 运行中的进程 | 无（`data/docweave.sqlite3` 无持有者；WAL 为 0 字节） |

## 2. 迁移前备份（已完成）

位置：`/Users/lihiko/repo/wegreen/.platform-restructure-backup-20260923/`（在 Git 之外，不进仓库）

| 文件 | 内容 | 核验 |
|---|---|---|
| `docweave-all-refs.bundle` | `git bundle --all` | `git bundle verify` → "records a complete history" |
| `HEAD.txt` / `refs.txt` / `remotes.txt` / `status-before.txt` | 迁移前的 HEAD、全部引用、remote、工作区状态 | — |
| `data/docweave.sqlite3` | **SQLite 在线备份 API**（`sqlite3.Connection.backup`），不是复制主文件 | `pragma integrity_check` → `ok`；`foreign_key_check` → 0 行 |
| `data/docweave.sqlite3.inventory.json` | 备份库 44 张表的行数 | 与源库逐表比对 |
| `data/blobs/` | 695 个 blob 的完整副本 | 文件数一致 |
| `blobs.sha256` | 695 条内容摘要清单 | 每条摘要 == 文件名 |

只复制 `.sqlite3` 主文件而不走备份 API，在有 WAL 的情况下会丢掉尚未 checkpoint 的事务；这里用的是
备份 API，并对副本做了完整性检查，所以"备份完整"是核验过的结论而不是假设。

## 3. 仓库根目录处理

```
/Users/lihiko/repo/wegreen/DocWeave  ──►  /Users/lihiko/repo/wegreen/platform-workspace
```

**方式**：同一文件系统内的目录改名（`mv`），`.git` 随之移动。前置条件已核实：`.git` 是普通目录、
无 `worktrees/` 登记、无子模块、`.git/config` 与内部文件不含任何绝对路径。因此改名后提交、分支、
标签与 remote 全部原样可达，不需要 `git init`、不需要重新 clone、不产生新仓库。

**不采用**的做法及原因：新建空仓库再复制代码（丢历史）、把原仓库整份塞进 `apps/docs` 再留一个
内部 `.git`（嵌套仓库）、submodule 包装（本轮目标是单仓库）。

`data/` 随根目录一起改名移动，**绝对路径因此改变**：
`…/DocWeave/data` → `…/platform-workspace/data`。这是本轮唯一一次数据位置变化，它由上面的完整备份
兜底，并在迁移后用逐表行数、ID 抽样与 blob 摘要清单核对（见 §7）。

## 4. 源码归位映射

### 4.1 Python

| 迁移前 | 迁移后 | 归属 |
|---|---|---|
| `apps/docs/docweave/store.py` | `packages/platform-core/synwevo_core/store.py` | 共享：唯一的持久化入口与**唯一的 schema 迁移执行者** |
| `apps/docs/docweave/context.py` | `packages/platform-core/synwevo_core/context.py` | 共享：租户上下文 |
| `apps/docs/docweave/services.py` 中的 `ServiceError` / `not_found` | `packages/platform-core/synwevo_core/errors.py` | 共享：跨服务错误契约 |
| `apps/docs/docweave/sessions.py` | `packages/platform-core/synwevo_core/sessions.py` | 共享：每个依赖方都持有自己的本地会话行 |
| `apps/docs/docweave/identity.py` | `packages/platform-core/synwevo_core/identity.py` | 共享：用户 / 工作空间 / 成员 / 应用资格的读写入口 |
| `apps/docs/docweave/passwords.py` | `packages/platform-core/synwevo_core/passwords.py` | 共享：Argon2id 口令哈希 |
| `apps/docs/docweave/auth.py` | `packages/platform-core/synwevo_core/auth.py` | 共享：机器密钥校验、界面认证模式、监听地址守卫、服务端根密钥 |
| `apps/docs/docweave/machine.py` | `packages/platform-core/synwevo_core/machine.py` | 共享：机器身份解析（API key / 服务账户 / 用户委托统一成同一个 principal） |
| `apps/docs/docweave/rp.py` | `packages/auth-client/python/synwevo_auth_client/rp.py` | 共享：应用接入统一身份的 OIDC 依赖方适配 |
| `apps/docs/docweave/api/rp_routes.py` | `packages/auth-client/python/synwevo_auth_client/routes.py` | 同上（`/auth/start`、`/auth/callback`、后通道登出） |
| `apps/docs/docweave/mfa.py` | `services/identity/synwevo_identity/mfa.py` | 身份服务 |
| `apps/docs/docweave/passkeys.py` | `services/identity/synwevo_identity/passkeys.py` | 身份服务 |
| `apps/docs/docweave/invites.py` | `services/identity/synwevo_identity/invites.py` | 身份服务 |
| `apps/docs/docweave/mail.py` | `services/identity/synwevo_identity/mail.py` | 身份服务 |
| `apps/docs/docweave/platform/oidc.py` | `services/identity/synwevo_identity/oidc.py` | 身份服务（授权服务器） |
| `apps/docs/docweave/platform/apps.py` | `services/identity/synwevo_identity/apps.py` | 身份服务（应用注册与资格） |
| `apps/docs/docweave/platform/routes.py` | `services/identity/synwevo_identity/provider_routes.py` | 身份服务（discovery/JWKS/authorize/token/userinfo/revocation/logout） |
| `apps/docs/docweave/api/auth_routes.py` | `services/identity/synwevo_identity/auth_routes.py` | 身份服务（登录、MFA、Passkey、找回、邀请） |
| `apps/docs/docweave/api/admin_routes.py` | `services/identity/synwevo_identity/admin_routes.py` | 身份服务（平台管理后台） |
| `python -m synwevo_docs.cli` 的 `bootstrap`/`ui-user`/`app`/`client`/`keys` | `services/identity/synwevo_identity/cli.py` | 身份服务 CLI |
| `apps/docs/docweave/api/portal_routes.py` | `apps/portal/synwevo_portal/routes.py` | 平台门户 |
| `apps/docs/docweave/engine/**` | `apps/docs/docweave/engine/**` | DocWeave 业务（模板、公式、PDF/图片、OCR、渲染） |
| `apps/docs/docweave/{services,jobs,views,definitions,locators,capabilities,builtin_service,regression,licensing,webhooks,authz}.py` | `apps/docs/docweave/…` | DocWeave 业务 |
| `apps/docs/docweave/api/{app,v1,main}.py` | `apps/docs/docweave/api/…` | DocWeave HTTP 接口 |
| `python -m synwevo_docs.cli` 的 `apikey`/`jobs`/`projects`/`openapi`/`info` | `apps/docs/docweave/cli.py` | DocWeave CLI |
| `backend/tessdata/` | `apps/docs/tessdata/` | OCR 语言数据属 DocWeave |
| `backend/run.py` | `infra/server/synwevo_server/{app,run}.py` | 部署组装：按 `DOCWEAVE_ROLE` 把上面的模块拼成一个进程 |
| `apps/docs/tests/**` | 按归属拆到 `apps/docs/tests/`、`services/identity/tests/`、`tests/integration/` | 测试边界跟模块边界一致 |

### 4.2 前端

| 迁移前 | 迁移后 | 归属 |
|---|---|---|
| `frontend/index.html`、`src/main.tsx`、`src/pages/{Home,Workspace,JobPage,About,Regression,Capabilities,DocEditor,SheetEditor}.tsx`、`src/views/**`、业务组件 | `apps/docs/web/` | DocWeave 工作台 |
| `frontend/portal.html`、`src/portal/**` | `apps/portal/web/` | 平台门户 |
| `frontend/accounts.html`、`src/accounts/**`、`src/pages/{Login,Account,Invite,Recover}.tsx`、`src/components/{MfaSection,PasskeySection}.tsx` | `services/identity/web/` | 账户与安全：**单一权威实现**，不复制第二套 |
| `src/styles.css`、`src/lib/{ui.tsx,theme.ts,useFit.ts}`、`src/components/PlatformNav.tsx` | `packages/ui/`（`@synwevo/ui`） | 三个外壳真实共用的视觉规范与基础组件 |
| `src/lib/{api.ts,session.tsx}` | `packages/auth-client/web/`（`@synwevo/auth-client`） | 应用接入统一身份的浏览器端适配 |
| `src/lib/{types.ts,describe.ts,latest.ts,status.ts}`、其余业务组件 | `apps/docs/web/src/…` | DocWeave 专属 |

**账户页面归属带来的一处行为调整**：此前 DocWeave 的 SPA 自己也打包了 `/login`、`/account`、
`/invite`、`/forgot-password`、`/reset-password` 这几条路由，单进程部署（`role=all`）靠它渲染。
把账户 UI 收进身份服务后，这些路径在**任何角色**下都由账户外壳（`accounts.html`）提供。单进程部署
里三个外壳由同一个进程、同一个源站提供，所以跳转仍是同源整页导航，Cookie、CSRF 与接口完全不变；
换来的是账户与安全界面只有一份实现。这条改动单独验证（见 §7 "单进程部署"）。

### 4.3 其余

| 迁移前 | 迁移后 |
|---|---|
| `Dockerfile`、`docker-compose.yml` | `infra/` |
| `scripts/platform_local.py` | `scripts/platform.py`（根目录统一启动/停止/状态/日志） |
| `scripts/{make_fixtures,benchmark,seed_samples,regression,blob_audit,snapshot,ui_perf,ui_e2e*}.py` | `apps/docs/scripts/` |
| `scripts/{verify_sso,migration_rehearsal,automation_example}.py` | `tests/integration/` |
| `regression/` | `apps/docs/regression/` |
| `README.md`、`NOTICE`、`.gitignore`、`.env.example`、CI 配置 | 仓库根目录 |
| `docs/**` | 仓库根目录 `docs/**`（平台级） |

## 5. 数据与配置：与源码移动分开处理

- `data/` **不跟随源码归位**。它留在仓库根目录，由 `DOCWEAVE_DATA_DIR` 显式指向；解析顺序为
  「命令行 `--data` → `DOCWEAVE_DATA_DIR` → 仓库根目录 `data/`」，其中"仓库根目录"由模块自身位置
  推出而**不依赖进程 cwd**，所以从平台根目录启动与从 `apps/docs/` 启动加载的是同一套数据。
- 数据库里**没有**指向旧目录的绝对路径：blob 按内容寻址（文件名即 SHA-256），任务快照与产物只存
  blob id 与文件名。因此本轮不需要、也不会对数据库做字符串替换。已在迁移后逐条核对（§7）。
- 密钥材料全在库内（服务端根密钥、`oidc_keys`），不重新生成、不新建第二套账户服务。
- issuer、client_id、回调白名单、Passkey RP ID、Cookie 名称与作用域均由配置给出，本轮**不改**：
  `accounts.localhost:8770` / `portal.localhost:8769` / `docweave.localhost:8765`，Cookie 仍是
  host-only 的 `dw_session`。
- 忽略规则跟着根目录一起保留：`data/`、`.env`、`.venv`、`node_modules`、`dist`、`testdata/`、
  `.local/` 继续不进 Git；迁移后重新核对实际被跟踪的文件。

## 6. 阶段与失败处理

| 阶段 | 内容 | 失败时的恢复 |
|---|---|---|
| A | 写下本文件并提交 | 无风险 |
| B | 目录改名 `DocWeave` → `platform-workspace` | `mv` 回原名；`.git` 完好 |
| C | Python 纯移动（`git mv`）+ 导入修复 + 包边界 | `git reset --keep` 到 B 的提交（工作区无用户改动，§1 已核实）；数据不受影响 |
| D | 前端拆分与构建 | 同上 |
| E | 运行整合（角色组装、根目录命令）、文档 | 同上 |
| 任何阶段 | 数据损坏 | 从 §2 备份恢复 `data/`，再按 §7 核对 |

每个阶段结束后跑该阶段对应的验证，失败就地修复再继续；不通过删库、丢弃修改或覆盖目标目录推进。

## 7. 迁移后核验（执行结果见本文件末尾"执行记录"）

1. Git：HEAD 与迁移前一致、两个分支与 remote 未变、`git log` 可达旧提交、`git status` 无丢失。
2. 数据：44 张表逐表行数与备份清单一致；69 个 project 的 id 与归属抽样比对；695 个 blob 摘要清单重算比对。
3. 配置：从平台根目录与从 `apps/docs/` 两个 cwd 启动，`manage`/`info` 报告同一个数据目录与同一套计数。
4. 依赖边界：自动检查"应用不得导入另一个应用的内部源码"。
5. 运行：根目录统一启动、三个模块分别启动、停止、日志；三条主链路（平台登录 / DocWeave 直达 / 机器自动化）。
6. 单进程部署（`role=all`）：登录 → 工作台 → 账户页仍可用。
7. 恢复演练：在隔离副本上从 §2 备份恢复代码 + 数据 + 配置，核对可一致恢复。

---

## 执行记录

### A —— 写下计划（提交 `676a6a3`）

本文件的 §1–§7 先于任何移动写成并提交。

### B —— 目录改名（无提交；改的是仓库自身的位置）

```
mv /Users/lihiko/repo/wegreen/DocWeave /Users/lihiko/repo/wegreen/platform-workspace
```

改名后立即核对：

| 检查 | 结果 |
|---|---|
| `git rev-parse --show-toplevel` | `/Users/lihiko/repo/wegreen/platform-workspace` |
| HEAD | `676a6a3`（与改名前一致） |
| 分支 | `upgrade/automation-api-ui`（另有 `main`、`origin/upgrade/automation-api-ui`） |
| remote | `origin git@github.com:WeGreen-AI/DocWeave.git`，**未修改** |
| 旧提交可达 | `git cat-file -t e292a3e…` → commit；`git log` 完整 |
| `git fsck` | 无输出（无错误） |
| `git status` | 干净 |
| 数据库 | `pragma integrity_check` → ok；44 张表行数与备份清单一致 |
| blob | 695 个文件摘要清单与迁移前**逐字节一致** |

### C —— 纯移动（提交 `63811c5`）

213 个文件全部经 `git mv`，`git show --stat` 中**每一个都显示为重命名**（`{旧 => 新}`），
没有一行内容改动。这个提交上的代码**不能运行**——这是有意的：一个既搬动 213 个文件又重写其
导入的提交，没有人能审；之后出问题时也分不清是哪一半造成的。

### D —— 边界与运行（提交 `e1ae385`）

导入修复、五个可安装发行版、角色组装层、前端拆成三个构建 + 两个共享包、CLI 一分为二、
容器构建与源代码提供清单按新布局更新。

**这一步里做出的、不只是移动的改动**（逐条列出，因为它们改变了行为）：

1. **`ServiceError`、`Store`、`Identity`、`Sessions`、`Apps`、机器身份、OIDC 令牌存储移入共享层。**
   理由不是"两边都用到"，而是具体的：验证令牌的一方不是签发它的一方；判断"是不是这个工作空间的
   成员"的一方不该 import 账户服务。
2. **`/api/auth/state`、`/me`、`/logout`、`/workspace` 移入 `synwevo_auth_client.session_routes`。**
   三个角色都要提供这四条；写两遍的那一遍会忘记重新核对工作空间成员资格。
3. **账户与安全界面收归账户服务，工作台不再打包第二份。** 单进程部署下两个外壳由同一进程、同一
   源站提供（静态资源目录不同名，互不遮盖）；依赖方上这些路径改为服务端 303 重定向。
4. **角色在构建应用时确定，不在每次请求时读环境变量。** 因为现在是按角色条件挂载路由：进程里
   存在哪些路由，必须和网关按角色拒绝哪些路径是同一个答案。
5. **数据目录解析改为从模块位置向上找工作区标记**，找不到就报错而不是猜（见 §5）。
6. **`up` 在数据目录没有数据库时拒绝启动**，除非显式 `--init`。
7. **源代码提供清单（AGPL §13）按新布局重写**：正在运行的程序不再只是 DocWeave 自己的目录，
   共享包、账户服务与组装层都在里面；同时排除了 `data/`、`testdata/`、`.egg-info` 等不该进去的内容
   （实测归档 275 个条目，0 个泄漏项）。

### 旧目录的处理

`backend/`（只剩旧虚拟环境与缓存，Git 里已无任何跟踪文件）与 `frontend/` 已删除。
仓库里**不存在**两套都能运行的项目，也没有指回旧路径的符号链接——
`python scripts/check_boundaries.py` 会检查这一点。

`scripts/setup.sh` 与 `scripts/start.sh` 由 `python scripts/platform.py setup` / `up` 取代：
它们写死了 `backend/.venv` 与 `backend/run.py`，留下就是两条会坏掉的入口。

### 数据处理结果

| 项 | 迁移前 | 迁移后 |
|---|---|---|
| 数据库位置 | `…/DocWeave/data/docweave.sqlite3` | `…/platform-workspace/data/docweave.sqlite3`（随根目录改名移动） |
| 44 张表行数 | 见 §1 | **完全一致** |
| 69 个 project id | — | 逐一相同 |
| `default` 工作空间 | `legacy / pending_claim / 0 成员` | **原样** |
| 695 个 blob | 摘要清单 | **逐字节一致** |
| 密钥材料 | 库内（服务端根密钥、`oidc_keys`） | 未重新生成、未移动 |
| issuer / client_id / 回调 / RP ID / Cookie 名 | — | **未改**（`accounts.localhost:8770` 等照旧） |
| Docker 卷 | `./data:/data` | `../data:/data`（构建上下文改为仓库根目录，具名路径仍指向同一个 `data/`） |

数据库里没有任何指向旧目录的绝对路径（blob 按内容寻址，任务快照只存 blob id 与文件名），
因此**没有、也不需要**对数据库做字符串替换。

### 回退方式

| 想回到 | 怎么做 |
|---|---|
| 重组前的代码 | `git checkout 676a6a3`（或 `e292a3e`，本轮之前的 HEAD），历史完整可达 |
| 重组前的目录名 | `mv platform-workspace DocWeave`；`.git` 是普通目录，改名无副作用 |
| 重组前的数据 | `/Users/lihiko/repo/wegreen/.platform-restructure-backup-20260923/`：一致性数据库备份 + 695 个 blob + 摘要清单 + `git bundle`。**Git 回退不等于业务数据恢复**，这两件事分开准备、分开验证 |
| 任意时点的完整实例 | `python scripts/platform.py backup` → `verify-restore`（本轮实测 20/20） |

### 验证结果

见 [`docs/quality/verification.md`](../quality/verification.md) 的「平台单仓库重组轮」一节：
后端 650 passed / 1 skipped、边界检查 0 越界、统一登录 38/38、真实库迁移演练 15/15、
恢复演练 20/20、干净检出安装成功、三条主链路与浏览器实跑。
