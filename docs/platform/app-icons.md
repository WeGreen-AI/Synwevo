# 应用图标接入记录

2026-09-30。使用指定 **Synwevo_App_Icons_v1 / 1.0.0**，未重新设计素材、增加业务应用、改动认证权限或部署生产。

## 素材与单一来源

包内 `SHA256SUMS.txt` 的 15 条记录全部核对通过；12 张图均为 1254×1254 RGBA PNG，alpha 范围 0–255。
正式保留的原图在 `packages/ui/app-icons/originals/`，其后相对路径与包中 `source_file` 一致；
`packages/ui/app-icons/source-manifest.json` 保留指定包的元数据和 SHA-256。
原包、说明、提示词和原图不进入 public 或运行时构建。没有 SVG、可编辑矢量源文件。

唯一手写映射是 `packages/ui/app-icons/registry.json`。`scripts/app_icon_assets.py` 从它和原图生成
`packages/ui/src/app-icons/assets.ts`、透明 PNG 变体（2026-10-04 起 36 张）及 `derived-manifest.json`（来源哈希、输出哈希、尺寸、字节数）。
`@synwevo/ui` 导出的 `AppIcon` 是页面唯一图片入口；不再按 `icon: doc/chat` 或显示名称判断身份。
服务端注册表、稳定 ID、安装身份及授权关系均不变。

## 已实现入口的映射

表中“原图”相对于 `packages/ui/app-icons/originals/`；“正式变体”相对于 `packages/ui/src/app-icons/`。

| 产品/入口 | 真实 ID 或模块路径 | 原图 | 正式变体 | 实际引用位置 |
|---|---|---|---|---|
| Docs | `docweave`（注册表主键，必须保留） | `icons/apps/synwevo-docs.png` | `synwevo-docs-{32,64,168}.png` | 公开首页与工作空间应用卡；共享应用切换器；账户返回应用链接；工作空间成员页的应用资格标题；Message 应用卡、应用发送者、关联对象面板 |
| Message | `messages`（注册表主键） | `icons/apps/synwevo-message.png` | `synwevo-message-{32,64,168}.png` | 公开首页与工作空间应用卡；共享应用切换器；四外壳消息入口及原有未读角标；账户返回应用链接 |
| Workspace | `platform`（OIDC 平台 ID，不是业务 apps 表的一行） | `icons/platform/synwevo-workspace.png` | `synwevo-workspace-{32,64,96}.png` | 门户工作空间侧栏标题；登录后的公开首页工作空间入口；共享平台导航/应用切换器；账户桌面返回链接及移动抽屉 |
| Accounts | `/account`（账户服务现有模块，无业务 app ID） | `icons/platform/synwevo-accounts.png` | `synwevo-accounts-{32,64,96}.png` | 门户账户链接/移动菜单；共享平台导航；账户页标题、桌面导航和移动抽屉 |
| Admin | `/admin`（账户服务治理模块，无业务 app ID） | `icons/platform/synwevo-admin.png` | `synwevo-admin-{32,64,96}.png` | 门户管理链接/移动菜单；账户桌面导航、移动抽屉和平台管理标题 |
| API 控制中心 | `/admin/api-platform`（Admin 内部模块） | `icons/platform/synwevo-api.png` | `synwevo-api-{32,64,96}.png` | 门户 API 链接/移动菜单；账户桌面导航、移动抽屉和 API 控制台标题 |

主要调用文件：门户 `Portal.tsx` / `PublicHome.tsx`；共享 `PlatformNav.tsx` / `MessagesLink.tsx`；
账户 `components/Topbar.tsx`、`admin/AdminLayout.tsx`（平台管理与 API 控制台共用的布局，标题位按当前页取图）、`pages/Account.tsx` / `Members.tsx`；
Message `components/cards.tsx` / `MessageItem.tsx` / `InfoPanel.tsx`。Docs 通过共享导航接入。
页面没有硬编码 PNG 地址。治理模块使用路径作为明确的图标键，不从当前 URL、语言或动态工作空间 ID 猜测。

## 业务应用（2026-10-04 接入）

2026-10-04 起业务应用已在代码中注册（`synwevo_core.apps.bootstrap_suite_app`，ID 与下表一致），生产按
`SYNWEVO_SUITE_APPS` 开放 Forms、Tables、DataPrep、Approvals、Extract；Agent 尚未开放。它们的图标经同一
`registry.json` 生成，光学校正按与上表相同的规则取得：偏移抵消原图实质轮廓（alpha ≥ 128）中心偏差，缩放使
较长一边约占画布 0.775，不缩小到 1.0 以下。

| 产品 | 注册表 ID | 原图 | 正式变体 | 实际引用位置 |
|---|---|---|---|---|
| Forms | `forms` | `icons/apps/synwevo-forms.png` | `synwevo-forms-{32,64,168}.png` | 工作空间应用卡（含一句用途说明）；共享应用切换器 |
| Tables | `tables` | `icons/apps/synwevo-tables.png` | `synwevo-tables-{32,64,168}.png` | 同上 |
| DataPrep | `dataprep` | `icons/apps/synwevo-dataprep.png` | `synwevo-dataprep-{32,64,168}.png` | 同上 |
| Approvals | `approvals` | `icons/apps/synwevo-approvals.png` | `synwevo-approvals-{32,64,168}.png` | 同上；现有治理审批操作仍不等于 Approvals 应用 |
| Extract | `extract` | `icons/apps/synwevo-extract.png` | `synwevo-extract-{32,64,168}.png` | 同上 |
| Agent | `agent` | `icons/apps/synwevo-agent.png` | `synwevo-agent-{32,64,168}.png` | 变体已生成；应用开放后由应用卡与切换器按 ID 使用，未开放时不出现 |

业务应用页面顶栏的品牌位仍是母品牌 `Brand` 加产品名，与 Docs、Message 一致；应用身份在应用卡与切换器中。
账户顶栏的横排导航只用文字（加图标后 1280px 英文会折成两行）；图标用于手机抽屉等纵向列表，无图标的项用同宽占位对齐；
窄屏的返回链接不带图标，避免挤出页宽（2026-10-05 复核）。
变体总计 36 张、195,812 字节。

## 尺寸、视觉与部署

- 完整保留原画布和低透明度像素。曾观察到 alpha > 0 的边界远大于实质轮廓，不能据此自动裁切。
  用与现有品牌流水线相同的 Pillow 预乘 alpha + Lanczos，从 1254px 原图直接缩小；无裁切、染色或放大。
- 光学校正在 registry 中统一配置：Docs 1.15、Message 1.12、Workspace 1.04、Accounts 1.05、Admin 1.03、API 1.00；
  小幅平移补偿原图中心偏差。组件固定正方形容器、不参与 flex 压缩、`object-fit: contain`，不裁断图形。
- 实际 CSS 尺寸：16px 应用卡/关联对象/成员资格，20px 导航/菜单，24px 入口标题，32px 应用发送者，48px 应用卡片。
  没有实际 64px 身份位，因此没有添加 64px 产品 UI 或大套尺寸变体。
- 2026-09-30 首批 18 张变体总计 85,400 字节；加入业务应用后 36 张、**195,812 字节**；单张约 1.2–13 KB。`srcSet`/`sizes` 按光学校正后的显示宽度选择分辨率，
  浏览器复用同 URL 缓存；图标元素有 `width`/`height`，不下载 316–515 KB 原图。
- Vite `?no-inline` 资源导入产生带内容指纹的独立 PNG；分别进入 `assets/`、`portal-assets/`、
  `account-assets/`、`messages-assets/`。沿用现有缓存策略，图片更新改变 URL。
  2026-09-30 时四个生产构建已实地加载（业务应用的第五个构建见“业务应用”一节）；额外的 `/icon-check/` base 和示例 CDN base 构建均逐一核对 18 个 URL 与产物。
  CDN 仅验证构建 URL，不声称真实 CDN 的传输/CSP 配置已经部署验收。
- 默认装饰性 `aria-hidden` + 空 alt，有名称的控件不重复朗读；需要独立说明时组件支持本地化 `label`。
  当前/禁用/hover/focus 沿用所属控件；未新增动效，也未修改角标定位。未知 ID 与图片失败均为同尺寸中性网格。
- 母品牌完整 Logo/图形 Logo/字标、登录品牌、邮件品牌头、共享 favicon 保持原样。
  账户现有 manifest 仍命名为 Synwevo 并引用共享 favicon，本轮没有把它改造成独立 Accounts 安装身份。

## 实测与限制

全部浏览器验收使用本地四个真实角色服务、生产构建和新建的隔离数据库，`/healthz` 标为 test。
测试账户、组织、项目、评论明确标记“图标验收夹具”；通过真实口令登录及 OIDC 跨域跳转。
Docs 评论通过真实 API 写入，再经 Message bridge worker 生成应用卡。没有 mock 页面、伪造 API 或访问用户 `data/`。

| 检查 | 本轮结果 |
|---|---|
| `npm run typecheck`、`npm run build` | 四个外壳通过 |
| `npm test -w @synwevo/ui` | 19 项通过 |
| `pytest tests/integration/test_shells.py tests/integration/test_portal_public.py -q` | 37 项先通过；1 项被沙箱本机端口限制阻止，获得本地权限单独重跑通过，共 38 项 |
| boundaries / design / component_names / brand 守卫 | 通过；未修改守卫豁免规则 |
| `brand_assets.py --check` | 22 份母品牌资源匹配确认原图 |
| `app_icon_assets.py --check` | 12 原图哈希、衍生文件及生成映射通过（2026-10-04：36 份；CI 的 verify 也运行它） |
| 桌面 1440px / 移动 375px，DPR 1 / 3 | 16 个真实页面/菜单视图通过：Workspace、Account、Admin、API、Docs、Message、切换器、门户菜单与账户抽屉 |
| DPR 2 | 公开应用卡、门户、账户、Message 应用卡实测并截图 |
| 320px，简体/繁体/英文 | 真实公开页均显示正确 Docs/Message 图标，没有横向溢出 |
| 交互与布局 | 菜单当前应用禁用；hover/focus；Esc 焦点归还、移动抽屉 Tab 陷阱通过；图标布局尺寸稳定，未观察到图标导致的 layout-shift |
| 深色/动效 | 现有产品只有浅色主题；模拟系统 dark 仍显示 light；reduced-motion 下检查通过，未创建新主题 |
| 网络/控制台 | 16 视图矩阵未观察到 HTTP >= 400 响应或页面脚本异常；PNG 加载成功、object-fit 为 contain、filter 为 none |
| 图片失败 | 单独故障注入：阻断 Docs PNG 请求；出现中性网格、48px 占位不变、Message 不受影响 |
| 映射/未知 ID | Vite SSR 加载真实 AppIcon：6 个稳定入口正确出图；7 个未知/缺失/错误包键（含继承属性名称）均为网格；具名/装饰语义和固定尺寸通过 |
| 16 / 32px 卡片 | 真实 Message 应用发送者、卡片和关联对象面板在 1×/3× 均加载正确；工作空间成员资格身份位也实测 |

截图和结构化观测在本机 `output/playwright/app-icons/`（按仓库约定忽略，不公开测试账户页面）：
`workspace-desktop.png`、`public-applications.png`、`account-drawer-mobile-3x.png`、
`app-switcher-desktop-1x.png`、`message-application-card.png`、`message-card-1x.png`，
以及 `browser-matrix.json` / `edge-checks.json` / `card-verification.json`。
截图前需等待图片 `decode()`：首轮立即截取菜单时图片尚未绘制，等待解码后已重新采集，不把空白截图当作通过依据。

16px/1× 下细白缝和内部细节会变软，实际验收保留相邻文字，不宣称小尺寸无损，也未重绘素材。
未进行 Safari/Firefox、真实手机硬件、真实读屏器和真实 CDN 的验证；未运行与图标无关的全量文档引擎套件或生产部署。

## 维护命令

在仓库根目录运行（项目依赖按现有锁文件安装）：

```bash
.venv/bin/python scripts/app_icon_assets.py         # 变更映射/尺寸后再生成
.venv/bin/python scripts/app_icon_assets.py --check
.venv/bin/python scripts/brand_assets.py --check
npm run typecheck
npm run build
```

以后接入新应用，先实现并确认稳定 ID，再更新 registry；不能因为这里有一张原图就建立业务入口。
