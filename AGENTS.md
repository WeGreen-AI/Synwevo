# 在 Synwevo 仓库里工作

一个 Git 仓库，里面清楚分开 **Synwevo Workspace**（门户）、**Synwevo Account**（统一账户）、
**Synwevo Docs**（文档应用）和确有共用的包。
先读 `docs/architecture/monorepo.md`（目录、依赖、数据所有权），再动手。

Synwevo 的产品定位是**工作流程自动化平台**；Synwevo Docs 是其中的文档自动化应用。
公开首页围绕平台、协作与自动化介绍，具体文档能力放在应用区；不把未实现的应用或编排器写成已有能力。

## 从哪个目录开始

| 你要做的 | 在哪里启动 |
|---|---|
| 平台级或跨模块的改动（身份、门户、共享包、运行方式） | 仓库根目录 |
| 只改 Synwevo Docs 的业务实现 | `apps/docs/`（仍然遵守本文件的规则） |

不要让人每次去猜在哪个目录跑哪条命令：根目录的命令在根目录能跑通，模块目录的命令在模块目录能跑通。

## 边界（这是本仓库存在的理由，不是风格偏好）

```
synwevo_core ───────┐             共享：唯一的持久化入口、租户上下文、身份与会话、
synwevo_auth_client ┤                   机器身份、OIDC 令牌存储、配置兼容、HTTP 骨架
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
synwevo_identity  synwevo_portal  synwevo_docs     ← 互相不可见
    └───────────────┼───────────────┘
                    ▼
             synwevo_server                       ← 唯一可以同时导入三者的地方（部署组装）
```

* **应用不得导入另一个应用的内部实现。** `synwevo_docs` 不 import `synwevo_portal`，反之亦然。
* **应用不得导入账户服务的实现。** Synwevo Docs 是账户服务的依赖方，通过 OIDC 与它交互；
  它需要的读侧能力（成员、资格、令牌校验）在 `synwevo_core` 里。
* **共享包不反向依赖应用。** `synwevo_core` 里出现 `import synwevo_docs` 就意味着账户服务再也不能
  在没有文档引擎的机器上启动。
* **只有真实共用的东西才进共享包。** 模板渲染、公式、PDF/图片、OCR 属于 Synwevo Docs，不要搬进去。
* **不用 `sys.path` 改写、机器绝对路径或指向旧目录的符号链接**来让 import 成立。包是装出来的。

机械检查：

```bash
python scripts/check_boundaries.py    # 跨模块导入、sys.path 改写、机器绝对路径、符号链接
python scripts/check_brand.py         # 旧品牌残留：每一处要么改掉，要么写清为什么保留
python scripts/check_design.py        # 设计系统：字面颜色只允许出现在共享令牌里，无私有色板、无深色主题残留
python scripts/check_component_names.py # 自有界面只写能力名（排版引擎、PDF 引擎、本地文字识别…），不写底层组件名；例外表要写理由
python scripts/brand_assets.py --check # Logo：衍生文件必须由确认原图生成，不手改（见 docs/design.md §2.5）
```

**改这两个文件不能替代让检查通过。**

`check_brand.py` 不数次数，它分类。有些字符串看着像旧品牌，实际上是别人系统里的主键——
app_id 写在已签发的令牌里，MFA 的域分隔标签决定了库里的种子还能不能解开。要保留一处旧标识，
在 `STABLE` 里加一条并写清理由；理由本身就是产出。

## 验证

```bash
python scripts/platform.py test            # 三个套件
python scripts/platform.py test docs   # 只跑 Synwevo Docs
python scripts/platform.py test identity   # 只跑账户服务
python scripts/platform.py test integration
python scripts/check_boundaries.py
python scripts/check_design.py
python scripts/check_component_names.py
python scripts/brand_assets.py --check
npm run typecheck && npm run build         # 三个前端外壳
```

测试套件的位置跟模块归属一致：

| 套件 | 内容 | 用哪个工厂 |
|---|---|---|
| `apps/docs/tests/` | Synwevo Docs 业务与它自己的 HTTP 表面 | `synwevo_docs.api.app.create_app` |
| `services/identity/tests/` | 登录仪式、MFA、Passkey、邀请、找回、管理后台、OIDC | `synwevo_identity.app.create_app` |
| `tests/integration/` | 跨模块：统一登录链路、机器身份、跨工作空间隔离、外壳归属 | `synwevo_server.app.create_app` |

一个测试如果需要另一个模块的路由才能通过，它就属于 `tests/integration/`——不要为了让它留在原地
而把另一个模块挂进来。

## 数据与安全

* 用户数据在仓库根目录的 `data/`，由 `SYNWEVO_DATA_DIR` 指向；解析不依赖进程 cwd。
  **不要把它提交、打包或写进日志。**
* 数据库只有一个，`synwevo_core.store.Store` 是唯一的打开入口，也是唯一执行 schema 迁移的地方。
* 密钥材料（服务端根密钥、OIDC 签名密钥、MFA 种子）在库内。**不要重新生成**，不要在文档或提交里
  写出真实值，占位符即可。
* 平台管理员治理账户与平台配置，**默认读不到任何业务文档**。新增接口不得成为绕过 `authz.py` 的旁路。
* 管理员 API 平台是明确授权的产品界面：仅有效平台管理员的 `api_platform.manage` 可进入控制台及管理接口。
  组织/空间 owner、admin 不因此获得控制台权限；普通 Docs 业务 API 保持正常授权。
  控制台管理权不授予业务内容访问：服务凭据必须受当前有效的空间批准、资源范围与 scope 交集限制。
  CLI、SDK 和控制台复用同一服务层；秘密仅创建/轮换时展示，调试不继承管理员会话的数据权限。
* 不要用扩大 Cookie 作用域、开放跨域或万能管理员凭据让页面"能打开"。

## 不要做的事

* 不要建假应用、空服务、占位仪表盘，或只为看起来完整的目录。
* 不要 Mock、假登录、空壳按钮、假进度、假数据看板。测试夹具要标明是夹具。
* 不要把「暂时能跑」的虚拟环境或 `node_modules` 当成可复现安装；从锁文件重建。
* 不要在没有实测的情况下宣称"零错误"。
