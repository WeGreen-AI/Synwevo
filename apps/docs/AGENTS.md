# Synwevo Docs（`apps/docs/`）

平台上的第一个业务应用：上传数据源与模板，建立映射，批量生成文档。
**先读仓库根目录的 `AGENTS.md`**——这里只写这个模块自己的事。

## 这个模块拥有什么

| 目录 | 内容 |
|---|---|
| `synwevo_docs/engine/` | 文档引擎：模板、公式、映射解析、DOCX/XLSX/PPTX/PDF/图片、OCR、排版 |
| `synwevo_docs/api/` | HTTP：工作台接口与 `/api/v1` 机器接口 |
| `synwevo_docs/{services,jobs,views,authz,…}.py` | 业务服务、任务调度、渲染、逐对象授权 |
| `synwevo_docs/cli.py` | 运维 CLI：机器密钥、OpenAPI 导出、任务与项目维护 |
| `web/` | 工作台前端（独立 Vite 构建，`assets/`） |
| `tests/` | 这个模块的测试 |
| `scripts/` | 这个模块的运维与验证脚本（夹具、基准、blob 审计、快照、端到端） |
| `tessdata/` | OCR 语言数据 |
| `regression/` | 回归用例 |

## 这个模块**不**拥有什么

登录、口令、MFA、Passkey、邀请、找回、账户与安全页面、平台管理后台、应用注册与 OIDC 授权服务器
都在 `services/identity/`。Synwevo Docs 是它的**依赖方**：

* 没有会话的人被送去 `/auth/start`，不是送去一个本地口令框——`role=app` 根本不提供 `/api/auth/login`。
* `/login`、`/account` 等路径在这里是**重定向**，由 `synwevo_auth_client` 提供，不是页面。
* 需要判断"这个人是不是这个工作空间的成员/有没有这个应用的资格"时，用 `synwevo_core`，
  不要 import `synwevo_identity`。

## 独立运行

账户服务与必需基础设施正常时，**门户停掉，Synwevo Docs 仍然可用**：

```bash
# 从仓库根目录
python scripts/platform.py up accounts docs
python scripts/platform.py down portal          # 门户停掉
# http://docweave.localhost:8765/ 仍可登录、工作、提交任务
```

单独启动（不经组装层）：`python -m uvicorn synwevo_docs.api.main:app`，或用根目录的
`python -m synwevo_server.run` 配 `SYNWEVO_ROLE=app`。

## 验证

```bash
python -m pytest apps/docs/tests            # 从仓库根目录
python -m pytest tests                          # 在 apps/docs/ 里
npm run build -w @synwevo/docs-web
python apps/docs/scripts/ui_e2e.py          # 真实浏览器
```

## 数据

不要在这个模块里新增第二个数据库或第二份账户表。数据目录与 schema 迁移的唯一入口是
`synwevo_core.store.Store`。
