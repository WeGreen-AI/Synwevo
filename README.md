# Synwevo

一个 Git 仓库，装着 **Synwevo** 平台和它上面的应用：**Synwevo Account**（统一账户）、
**Synwevo Workspace**（登录后的第一页），以及第一个业务应用 **Synwevo Docs**（数据映射与批量
文档生成）。三者分开构建、分开启动、分开测试，共用一个账户权威和一套设计规范。

仓库：<https://github.com/WeGreen-AI/Synwevo>

```
仓库根目录
├─ apps/
│   ├─ docs/          Synwevo Docs：数据映射与批量文档生成（后端 + 工作台前端 + 测试 + 脚本）
│   └─ portal/        Synwevo Workspace：登录之后的第一页，告诉你有哪些应用
├─ services/
│   └─ identity/      Synwevo Account：登录、MFA、Passkey、邀请与找回、平台管理、OIDC 授权服务器
├─ packages/
│   ├─ core/          共享：唯一的持久化入口、租户上下文、身份与会话、机器身份、HTTP 骨架
│   ├─ auth-client/   应用接入统一身份：OIDC 依赖方、共用会话路由、浏览器端会话上下文
│   └─ ui/            三个外壳共用的设计变量、主题与基础组件
├─ infra/             容器构建、单进程部署组装（synwevo_server）
├─ scripts/           根目录命令：安装、启动、停止、状态、日志、测试、备份、边界检查
├─ tests/integration/ 跨模块验证：统一登录链路、机器身份、跨工作空间隔离、外壳归属
├─ docs/              平台架构、权限、开发、迁移、接入文档
└─ data/              运行数据（数据库与 blob）—— 不进 Git
```

**为什么这样分。** 账户服务必须能在文档引擎缺席时启动，Synwevo Docs 必须能在门户停掉时继续工作。
这两句话要么是可机械检查的事实，要么只是一句话——分成独立的包、各自声明依赖，是让它变成前者的
办法。检查它的命令是 `python scripts/check_boundaries.py`。

## 最短可执行的本机启动

需要 Python 3.12+ 与 Node 22+。可选系统工具：LibreOffice（旧格式转换、PDF 导出、公式计算）
与 Tesseract（OCR）——macOS：`brew install --cask libreoffice && brew install tesseract`。

```bash
git clone git@github.com:WeGreen-AI/Synwevo.git synwevo && cd synwevo
python scripts/platform.py setup      # 建 .venv、按固定版本装依赖、构建三个前端
python scripts/platform.py up         # 启动账户服务、门户、Synwevo Docs，登记 OIDC 客户端
```

打开：

| 服务 | 地址 |
|---|---|
| Synwevo Workspace | http://portal.localhost:8769 |
| Synwevo Docs | http://docweave.localhost:8765 &nbsp;<sub>（主机名保持不变：它是注册过的 OIDC 回调与 Passkey 可信 origin）</sub> |
| Synwevo Account | http://accounts.localhost:8770/login |

首次使用需要一个平台管理员：

```bash
.venv/bin/python -m synwevo_identity.cli bootstrap token   # 一次性令牌，在登录页粘贴
```

停止：`python scripts/platform.py down`；状态与日志：`status` / `logs <服务>`。
完整说明见 [docs/development/local-platform.md](docs/development/local-platform.md)。

**三个主机名而不是三个端口**，因为 **端口不隔离 Cookie**：`localhost:8770` 设置的 Cookie 也会
发给 `localhost:8765`。`*.localhost` 全部解析到回环地址且不需要改 hosts 文件，于是得到三个真正
互不相通的 Cookie 作用域。

## 常用命令

```bash
python scripts/platform.py up [accounts|portal|docs …]   # 全部或其中几个
python scripts/platform.py down / restart / status / logs <服务> [-n 200]
python scripts/platform.py test [docs|identity|integration|all]
python scripts/check_boundaries.py                            # 模块依赖边界
python scripts/platform.py backup                             # 一致性备份
python scripts/platform.py verify-restore <归档>              # 恢复演练（隔离副本）
npm run build        # 三个前端外壳       npm run typecheck
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture/monorepo.md](docs/architecture/monorepo.md) | **目录、模块依赖、数据所有权、独立运行边界** |
| [docs/architecture/platform.md](docs/architecture/platform.md) | 平台账户方案与本机拓扑的选型依据 |
| [docs/development/local-platform.md](docs/development/local-platform.md) | 依赖、配置、启动/停止、日志、故障排查 |
| [docs/migrations/synwevo-rebrand.md](docs/migrations/synwevo-rebrand.md) | **Synwevo 品牌统一与仓库迁移**：名称/路径映射、配置兼容、保留标识、remote 切换、回退 |
| [docs/migrations/platform-restructure.md](docs/migrations/platform-restructure.md) | 上一轮单仓库重组：路径映射、Git 保全、数据处理、恢复记录（历史记录，沿用当时的名字） |
| [docs/integration/ports.md](docs/integration/ports.md) | 实测端口、绑定地址、鉴权与健康检查 |
| [docs/integration/app-onboarding.md](docs/integration/app-onboarding.md) | 新应用如何接入统一身份、门户与权限 |
| [docs/integration/api.md](docs/integration/api.md) | 机器接口 `/api/v1`：鉴权、任务语义、幂等、事件与 Webhook |
| [docs/security/permissions.md](docs/security/permissions.md) | 权限分层与逐对象授权 |
| [docs/security/sessions.md](docs/security/sessions.md) | 会话、重认证、撤销与跨应用登出 |
| [docs/operations.md](docs/operations.md) | 生产启动、备份恢复、迁移与回滚 |
| [docs/quality/progress.md](docs/quality/progress.md) | 各区块的真实进度与本轮验证结果 |
| [apps/docs/README.md](apps/docs/README.md) | **Synwevo Docs 产品说明**：功能、格式能力、限制 |
| `AGENTS.md`（根目录与各模块） | 工作边界、验证命令、依赖规则 |
| `.env.example` | 所有配置项与上限 |

## 许可与数据

* 处理全部在本机或自有服务器完成，不向外部服务发送文件。
* PyMuPDF 为 AGPL-3.0（或 Artifex 商业许可）。启用它并对外提供服务时，AGPL-3.0 §13 的对应源代码
  提供义务适用；`/api/source-code` 由**正在运行的文件**生成该压缩包。详见
  [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) 与 `NOTICE`。
* 用户数据、数据库、密钥与备份不进 Git、不进镜像上下文、不写入日志。

## 后续开发从哪里开始

* 平台级或跨模块的改动 → **仓库根目录**。
* 只改 Synwevo Docs → `apps/docs/`，同时遵守根目录 `AGENTS.md` 的依赖规则。
