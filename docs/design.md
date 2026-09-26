# Synwevo 设计系统：白蓝视觉体系、组件与交互规则

本文是整个平台（Synwevo Workspace 门户、Synwevo Account、Synwevo Docs、平台管理后台、管理员 API
控制台）共用的设计契约。内容对应实际代码，不是提案：令牌在 `packages/ui/src/styles.css`，基础组件在
`packages/ui/src/ui.tsx`，守卫脚本是 `scripts/check_design.py`（CI 与本机验证都跑）。

更新于 2026-09-25：本轮把此前分散的配色（门户独立的绿色主题、Docs 的浅/深色切换、API 台的兜底色值）
收敛为**一套白色为主、蓝色为品牌色的浅色体系**。深色主题随之退役（§2.4）。

---

## 1. 原则

1. **白与浅中性色构成页面，蓝只用于操作与强调。** 主表面、卡片、输入区是 `#FFFFFF`；页面背景是
   `#F8FAFC`；品牌蓝 `#2563EB` 只出现在主要操作、链接、选中、焦点和少量提示背景上；Logo 保持确认原图的墨色，不染品牌蓝（§2.5）。
   不把正文、卡片、标签整体染蓝，不用大面积深色背景。
2. **一处定义，处处引用。** 颜色、字号、间距、圆角、阴影、动效时长全部是 `:root` 令牌；应用样式表
   只写布局，不定义自己的色板、不写字面颜色。守卫脚本把每一处例外归到有名字的理由里（§7）。
3. **语义色克制，且永远配文字或图标。** 成功/警告/错误/信息各一组（主色 + 淡底），不用颜色作为
   唯一信号。映射、重复区块等确需区分的地方用编号、名称与边框辅助（§5）。
4. **软件外壳与用户文档分开。** 白蓝规则作用于导航、面板、控件、对话框；用户上传的文档、表格、
   PDF、图片、模板与生成产物保留自身颜色与排版（`--paper*` 令牌，§2.3）。
5. **对比度是可量的验收，不是审美。** 正文 ≥ 4.5:1，大字 ≥ 3:1，交互控件边界 ≥ 3:1（WCAG 2.2 AA，
   1.4.3 / 1.4.11）。数值写在令牌旁边（§2.1）。

## 2. 令牌

### 2.1 颜色

| 令牌 | 值 | 用途 | 对比度（对白 / 对 `--bg`） |
|---|---|---|---|
| `--bg` | `#F8FAFC` | 页面背景、侧栏、表头条 | — |
| `--panel` `--surface` | `#FFFFFF` | 卡片、面板、输入、弹窗 | — |
| `--surface-hover` / `--surface-active` | `#F1F5F9` / `#E2E8F0` | 悬停、按下、次级填充 | — |
| `--line` | `#E2E8F0` | **装饰**分隔线、卡片描边 | 1.3:1（不用于控件边界） |
| `--line-strong` | `#8894A8` | 输入框、按钮、复选框等**控件边界** | 3.1:1 |
| `--text` | `#0F172A` | 主要文字 | 17.9:1 |
| `--text-2` | `#475569` | 次要文字、标签 | 7.6:1 |
| `--muted` | `#5B6B82` | 辅助说明（仍是文字） | 5.4:1（在 `--surface-hover` 上 4.9:1） |
| `--accent` | `#2563EB` | 品牌主色：主要操作、链接、选中、焦点环 | 白字 5.2:1；作文字对白 4.6:1 |
| `--accent-strong` / `--accent-pressed` | `#1D4ED8` / `#1E40AF` | 主操作 hover / pressed；作文字用 strong | 白字 6.6:1 / 8.6:1 |
| `--accent-weak` | `#EFF6FF` | 轻选中、信息提示底、当前项底 | 搭配 `--accent-strong` 文字 6.3:1 |
| `--accent-line` | `#93C5FD` | 选中态描边、输入聚焦环 | — |
| `--ok` / `--ok-weak` | `#15803D` / `#ECFDF5` | 成功 | 5.0:1 |
| `--warn` / `--warn-weak` | `#B45309` / `#FFFBEB` | 警告 | 5.1:1 |
| `--err` / `--err-weak` / `--err-line` | `#B91C1C` / `#FEF2F2` / `#FCA5A5` | 错误、危险操作 | 6.5:1 |
| `--block` | `#7C3AED` | 类别标记：重复区块 / 明细组（配编号与名称） | 5.5:1 |
| `--paper` / `--paper-text` / `--paper-line` | `#FFFFFF` / `#111418` / `#E5E7EB` | 文档纸面，**不随主题** | — |
| `--overlay` | `rgba(15,23,42,.4)` | 弹窗遮罩 | — |

对比度以 sRGB 相对亮度计算，写入 `styles.css` 头部注释；改任何一个值都要重新核对这一列。

### 2.2 字体、字号、间距、形状、动效

* **字体**：`--font` 系统无衬线（PingFang SC/TC、Microsoft YaHei/JhengHei、Noto Sans CJK、Segoe UI…），
  `--mono` 等宽。应用不得自选字体族（守卫禁止 `font-family: Georgia` 之类）。
* **字号阶**：`--fs-1..7` = 11 / 12 / 13 / 14 / 16 / 20 / 24 px。工作台正文 13px，公开首页与
  账户流程 14–16px，标题 20/24 px 或首页 `clamp()`。行高正文 1.5，长段落 1.6–1.8。
* **间距**：`--sp-1..6` = 4 / 8 / 12 / 16 / 24 / 32 px；内容宽度 `--content-w` 1120px。
* **圆角**：`--radius-sm` 6、`--radius` 8、`--radius-lg` 12、`--radius-xl` 16；控件 6px，卡片 8–12px。
* **阴影**：`--shadow` 卡片、`--shadow-lg` 浮层；不用彩色大阴影。
* **焦点**：`--focus` 双环（2px 白 + 4px 蓝）或 `outline: 2px solid var(--accent)`，所有可聚焦元素
  都有可见焦点（`:where(...)` 兜底规则，零优先级，组件可覆盖但不能取消）。
* **动效**：`--dur-1` 120ms（按钮/条目反馈）、`--dur-2` 200ms（面板、弹窗出现）、`--dur-3` 280ms
  （布局折叠）；`--ease` `cubic-bezier(.2,.8,.2,1)`。`prefers-reduced-motion: reduce` 把三者置 0 并
  把所有动画压到 1ms——只在共享样式表里写一次，应用样式表不再各写一份。
* **强制高对比 / 系统高对比**：`@media (forced-colors: active)` 给控件补系统色边框，焦点用
  `Highlight`。强制色不会给位图重新着色，所以 Logo 以自身图形为蒙版、用 `CanvasText` 绘制（§2.5）。

### 2.3 纸面：文档内容不被主题化

`.doc-page .editor-page .pdf-page .sheet .grid .tline .slide-page` 使用 `--paper*`，用户文件里的
颜色（OOXML 命名色、单元格底色/边框、字段文字色）按原样渲染；预览与产物一致，主题永远不写进
生成的文件。守卫把这些位置归入「文档内容」例外（§7）。

### 2.4 主题：只有一套

浅/深色/跟随系统的切换已退役。`theme.ts` 里 `storedTheme()` 永远返回 `light`，`data-theme` 永远是
`light`，共享样式表对任何其他值一视同仁（`:root[data-theme="dark"] { color-scheme: light }`），
`<meta name="color-scheme" content="light">` 和 `theme-color #ffffff` 让浏览器外壳也保持一致。
旧版本存下的偏好照旧可读、不会抛错也不会把旧配色带回来；其他账户设置一律不动。
E2E `apps/docs/scripts/ui_e2e_keyboard.py` 的 G 流程验证：新旧键写入 dark/system、模拟系统深色，
外壳与纸面都不变。

### 2.5 Logo：三种确认版本

| 版本 | 内容 | 用在哪里 |
|---|---|---|
| 完整版 `full` | 编织式 S 图形 + Synwevo 字标 | 三个外壳的顶栏、登录/注册框架、状态页顶栏、账户恢复卡片；README 页首 |
| 图形版 `mark` | 只有 S 图形 | 顶栏在窄屏放不下完整版时（Docs 工作台与任务页 ≤900px、账户顶栏 ≤720px）；整页的载入/跳转/错误状态（`PageState`）；favicon 与主屏图标 |
| 字标版 `wordmark` | 只有 Synwevo 字样 | 门户页脚、登录框架页脚、公开首页流程示意卡的标题 |

* **来源**：三张确认原图原样保存在 `packages/ui/brand/originals/`。它们彼此并不逐像素一致（独立图形版与
  完整版中的图形 IoU≈0.83，独立字标与完整版中的字标 IoU≈0.78，字距不同），因此**以完整版为准**：
  图形版与字标版都从完整版的像素里裁出，不重绘、不重排字、不描摹。
* **生成**：`python scripts/brand_assets.py` 生成全部衍生文件与 `packages/ui/brand/assets.json`（每个文件的
  来源哈希、裁切框、尺寸、哈希与处理步骤）；`--check` 在内存里重建并与已提交文件逐像素比较。允许的处理只有：
  裁去透明留白（保留原图 4px 抗锯齿边）、在预乘 alpha 上等比例 Lanczos 缩放、把图形放进图标底板、把前景改成白色
  （只改 RGB，alpha 不变）。全部是位图，不提供"内嵌位图的 SVG"。
* **组件**：只通过 `Logo` / `Brand` / `BrandMark` / `PageState` 使用，页面不直接引用图片路径。尺寸以 CSS 高度给出
  （顶栏 24px、`lg` 32px、页脚字标 13–14px），宽度随原图比例；`srcSet` 提供 1×–3× 屏幕的清晰度，
  `width/height` 属性预留空间，`object-fit: contain` 保证不拉伸。
* **不要**：用字体重新输入 Synwevo 冒充字标；在完整版或字标版旁再写一遍 "Synwevo"（产品名 Docs / Account /
  Workspace 可以跟在完整版后面）；在列表卡片、对话框、按钮或每个列表项上重复放 Logo（公开首页流程示意卡原本就有一个
  品牌名称标题，它换成字标版，是一处既有品牌位而不是重复）；用 Logo 替代组织头像、用户头像或
  外部身份提供商标记；给用户文档、模板、生成产物加平台水印。
* **窄屏**：先去掉产品名（`lg` 品牌在 ≤380px 默认如此，完整版保持可读），仍放不下时换图形版（`markBelow`），
  不把完整版缩小到字标无法辨认。
* **可访问名称**：品牌链接的名称以可见文字开头（例如「Synwevo Account · 返回平台首页」）；链接已有名称时图片
  `alt=""`，避免重复朗读；`PageState` 里的图形是装饰（`alt=""`），状态文字负责说明。
* **浏览器图标**：由图形版生成：16/32px PNG 与 180px `apple-touch-icon` 经 Vite 指纹化进各外壳的资源目录（缓存一年，
  换图即换名）；`favicon.ico`（16/32/48）放在各外壳根目录，供没有声明图标的响应（例如新标签页里的 JSON）。
  标签页图标带白色圆角底板：浏览器标签栏可能是深色，而 Logo 墨色接近黑色。没有 PWA 与 manifest，因此不提供
  maskable 图标。
* **白色版本**：`packages/ui/brand/derived/*-white.png` 只用于深色背景上的平台自有文档（GitHub 深色模式下的 README）；
  外壳只有浅色主题，界面里不使用。
* **邮件**：系统邮件是纯文本（§6），不放图片 Logo。

## 3. 基础组件（`@synwevo/ui`）

| 组件 / 类 | 规则 |
|---|---|
| `Brand` / `BrandMark` | 唯一的品牌位：完整版 Logo（24px；`size="lg"` 32px）+ 灰色产品名（Workspace / Account / Docs）。三个外壳的顶栏、登录框架、门户首页都用它；`markBelow` / `productBelow` 控制窄屏（§2.5） |
| `Logo` | 三种确认版本之一（`full` / `mark` / `wordmark`），按高度等比显示；页脚与流程示意卡的字标用它 |
| `PageState` | 整页只有一条消息的状态（载入中、正在前往登录、会话或路由错误）：图形版在上，消息在下 |
| `.btn` | 30px 高、6px 圆角、`--line-strong` 边界；`primary`（蓝底白字，hover strong，active pressed）、`danger`（红字红边）、`ghost`、`sm`/`xs`/`lg`/`icon`。`<a class="btn">` 与 `<button>` 外观一致 |
| `.input` `select.input` `textarea.input` | 30px、`--line-strong` 边界，聚焦蓝边 + 淡蓝环；占位符用 `--muted`；复选框/单选 `accent-color: var(--accent)` |
| `.badge` | 中性 / `ok` / `warn` / `err` / `info`，文字始终随色 |
| `.notice` | 中性 / `info`（淡蓝底）/ `ok` / `warn` / `err`，用于页面内说明与错误 |
| `.card` `.card-h` `.card-b` | 白底、`--line` 描边、`--shadow` |
| `table.tbl` / `table.gov` | 表头 `--panel-2`、行分隔 `--line`、悬停 `--surface-hover`、选中 `--accent-weak` + 左侧 3px 蓝条 |
| `Modal` `DialogProvider`（confirm / prompt） | 白底 10px 圆角、遮罩 `--overlay`；焦点陷阱、Esc 只关最上层、关闭后焦点归还；破坏性确认默认聚焦「取消」 |
| `ToastProvider` | 白底卡片，左侧 4px 状态条（蓝 / 绿 / 红），不再用深色块；悬停/聚焦暂停倒计时，会避开被聚焦的控件 |
| `.menu` `.menu-item` / `MoreMenu` | 白底浮层、`--shadow-lg`；键盘上下、Home/End、Esc 回到触发按钮 |
| `.tabs` / `.acct-tabs` / `.step` | 选中项蓝色下划线或淡蓝底 + 蓝字；当前步骤蓝圆点 |
| `.empty` | 居中说明 + 一个明确的下一步动作，不放假数据 |
| `LanguageMenu` | 三语言选择器，`select.input.sm`，所有外壳同一位置（顶栏右侧） |
| `.kbd` `.cmdk` | 快捷键与命令面板（Docs） |

组件的键盘、IME（`isImeKey`）、焦点管理与动效规则见 `packages/ui/src/a11y.ts` 与 `ui.tsx` 的注释。

## 4. 页面模式

* **全局顶栏**：白底、底部 `--line`、48px（门户 56px）；左侧品牌标记，中间页标题/当前项目，右侧
  导航按钮（`.btn.ghost.sm`）、语言菜单、账户、退出。账户与治理页复用 `Topbar`，Docs 的三种页面
  （项目列表、工作台、任务页）复用同一 `.topbar`。
* **范围指示**：门户左栏列出工作空间并标记当前项（淡蓝底 + 左侧蓝条）；Docs 顶栏的 `PlatformNav`
  显示当前空间与切换器；账户页、组织页、平台管理各自有清楚的作用域标题，互不混用。
* **页标题与主操作**：标题左、主操作右（`btn primary`），次要操作 `btn`/`ghost`，危险操作
  `btn danger` 并要求确认。
* **公开首页**：56px 白色顶栏 + 品牌标记；主视觉一栏文案、一栏淡蓝底的流程示意（三阶段，中间
  阶段蓝底白字）；能力条、三步流程、应用卡片（白底 + 阴影）、数据与隐私（白卡）、服务说明。
  按钮直接用共享 `.btn` / `.btn.primary.lg`。
* **登录 / 注册 / 邀请 / 找回**：`AuthFrame`——左侧品牌叙述（窄屏隐藏）、右侧白卡；口令可见性切换、
  自动填充属性、错误块在字段上方且不移动提交按钮；「其他登录方式」分隔下依次是安全密钥、邮箱
  验证码、已配置的外部提供商（统一 `.btn.flow-provider` + 单色标记，不嵌第三方美术资源）。
* **账户中心**：左侧锚点导航，右侧卡片分区（资料、邮箱与口令、登录与安全 [Passkey / 外部登录 /
  两步验证]、登录的设备、工作空间、安全活动、数据与账户）；敏感操作在卡片内要求当前口令。
* **平台管理 / API 控制台**：`Topbar` + 页签或按钮组导航；真实计数与空态，不画假曲线。
* **404 / 已退出 / 失败**：共享 `StatusPage`（顶栏品牌 + 语言菜单，居中标题、一句说明、一个明确的去处）；
  门户、账户、Docs 的未知地址都走它，门户在其外壳内渲染同一结构；外部登录失败带 `ext_error` 回到登录页，
  用同一错误块解释。

## 5. 交互规则

* 同一对象的创建、编辑、保存、取消、返回、删除、归档、撤销、重试在各应用中同一套词与同一种反馈：
  可逆操作（归档、禁用）不弹确认，只给可撤销的提示；不可逆操作（删除）弹 `alertdialog`，默认焦点在
  「取消」；批量操作走同一事务，一次撤销整体回滚。
* 保存语义明确：Docs 工作台常驻「已自动保存 / 保存中 / 保存失败」；表单类页面显式保存；4xx
  校验失败就地说明，不显示为保存失败。
* 进度来自真实任务状态；总量未知时显示阶段，不用计时器假百分比。
* 多组映射：颜色 + `#编号` + 名称 + 边框同时出现；查找高亮用琥珀色（`FIND_MARK`）避免与映射混淆；
  重复区块用 `--block` 紫色虚线框 + 标签。这些是「统一规则」内的类别色，不是应用私有主题。
* 键盘：所有主要操作有键盘路径（命令面板 ⌘K、列表 j/k、批量选择、分隔条方向键）；全局按键先查
  IME 组合状态；弹层管理保证 Esc 只关最上层并归还焦点。
  对话框与抽屉（`useModalFocus`）每次 Tab 自行按 DOM 顺序移动焦点（Safari 默认 Tab 跳过按钮/链接，交给浏览器会跳出），
  关闭后回到打开者——`activeElement` 为 body 时回到指针最后按下的元素（WebKit 点击不聚焦按钮）。回归：`ui_e2e_shell.py` 键盘段。
* 按钮状态：`.btn.primary` 的 hover/active 必须重申 `color: var(--on-accent)`——通用 `.btn:hover` 选择器优先级更高，否则主按钮悬停时深字压深蓝（CI-16 实测 2.66:1）。新增按钮变体时同样检查四态对比度。
* 弹层：所有菜单、下拉与提示层用 `@synwevo/ui` 的 `Menu` / `useAnchoredLayer`——`createPortal` 到
  `document.body`、以视口坐标定位（`layer.ts` 的纯函数决定翻转/贴边/限高），滚动、缩放与旋转时重算，锚点
  离开视口即关闭。**不允许**在滚动容器里用 `position:absolute` 的菜单，也不允许用 `z-index: 99999` 解决
  裁切。菜单是 `role=menu`，抽屉与对话框是 `role=dialog`，不给所有弹层套同一个 role。Esc 在任意焦点位置
  关闭并把焦点还给触发按钮（WebKit 点击按钮不聚焦，所以监听在 `document`）。
* 窄屏（内容驱动断点：账户 720px、门户 760px、Docs 900px；在 320/360/390/430/768 上核对）：
  - 导航收进 `Drawer`（右侧、焦点陷阱、背景不可交互、Esc/遮罩关闭）；返回工作的链接留在栏内。
  - 表格保留列并在 `.list-wrap` 内横向滚动，选择列 sticky 左、操作列 sticky 右；批量条贴底并加安全区。
  - 目标尺寸：所有指针目标 ≥ 24×24 CSS px（WCAG 2.2 SC 2.5.8，句中内联链接除外）——独立操作链接、单选/复选行、文件输入也算；
    `@media (pointer: coarse)` 下 `.btn`/主要操作/提交/菜单项/页签 ≥ 44px（SC 2.5.5、Fluent 2 iOS/Web 44），`.btn.sm` ≥ 40px，链接与勾选行 ≥ 32px；不改鼠标下的外观。
    回归：`services/identity/scripts/ui_mobile_audit.py`（320/375/768）。
  - Docs 工作台 ≤ 900px：「文件 / 来源 / 目标 / 映射」一次只显示一个面板，占满剩余高度；选择栏跨面板保留
    两侧选择，「建立映射」始终可达；侧面板不渲染 34px 折叠条。手机能完成上传→映射→预览→生成→下载
    （`apps/docs/scripts/ui_e2e_mobile.py`）。
  - 不禁止浏览器缩放，不全局拦截触摸滚动；用 `100dvh` 与 `env(safe-area-inset-bottom)`。
  - 提示条（toast）在 ≤620 px 置顶、整行宽：手机上对话框底栏就是主按钮所在，底部提示条会盖住它（CI-09）。容器不拦截指针。

## 6. 覆盖清单（本轮迁移）

| 范围 | 状态 |
|---|---|
| 共享令牌、按钮、输入、徽章、提示、卡片、表格、弹窗、菜单、Toast、命令面板、步骤条 | 已按 §2 收敛；深色块删除 |
| 门户：公开首页、登录后门户、状态页（载入 / 未登录 / 失败） | 从独立绿色主题迁移到共享令牌与共享按钮；品牌标记统一 |
| 账户：登录（口令 / 安全密钥 / 邮箱验证码 / 外部提供商 / 两步验证 / 关联要约）、注册、验证邮箱、邀请、找回、账户中心、组织、成员、平台管理、API 控制台 | 使用共享令牌；API 台去掉兜底色值；顶栏与登录框架改用 `Brand` |
| Docs：项目列表、工作台、任务页、能力矩阵、内置文档/表格编辑器、对话框 | 主题切换退役；内联字面色改为令牌或 `color-mix()`；纸面规则不变 |
| 邮件 | 纯文本模板（无 HTML 配色），三语言；验证码行在发出后脱敏 |
| 浏览器外壳 | 三个 `index.html`：`lang="zh-Hans"`、`theme-color #ffffff`、`color-scheme light`、由确认 Logo 图形版生成的 favicon 与 apple-touch-icon（§2.5） |
| 未纳入 | 邮件客户端与系统原生认证弹窗（Passkey 提示）的外观由系统决定，不受本体系控制 |

## 7. 守卫：`scripts/check_design.py`

扫描三个外壳与共享包的 `.css/.ts/.tsx/.html`：

* 共享令牌文件之外的字面颜色（`#rgb(a)`、`rgb()/hsl()`）必须落入 `ALLOWED` 中一条有名字的理由
  （文档内容、标记色板、透明叠加、浏览器标签栏颜色）；否则退出码非零。Logo 是图片文件，不在扫描范围内，
  由 `scripts/brand_assets.py --check` 保证与确认原图一致。
* 禁止：`data-theme="dark"`、`prefers-color-scheme: dark`、门户旧色板变量、自选字体族。
* 应用样式表不得定义 `:root` 令牌，`@media` 之外不得用 `!important` 盖住共享规则。
* 三个入口必须引入 `@synwevo/ui/styles.css`；令牌文件必须包含 §2.1 的基线值且没有深色块。

新增确有理由的例外时，在 `ALLOWED` 里加一条并写清"为什么它不是界面外壳的颜色"。

## 8. 参考与取舍

只参考官方规范与实际交互行为，不复制品牌资产（访问日期 2026-09-25）：

| 来源 | 借鉴 | 不采用 |
|---|---|---|
| [Fluent 2 · Color](https://fluent2.microsoft.design/color) | 中性色承载层级、品牌色只做强调；语义色成组（主色 + 淡底） | Fluent 的多主题/深色与品牌渐变 |
| [Fluent 2 · Motion](https://fluent2.microsoft.design/motion) | 短反馈 100–200ms、面板 200–300ms、退出快于进入；减少动效时保留状态可读 | 大幅位移与弹性曲线 |
| [WCAG 2.2 · 1.4.3 / 1.4.11](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) | 正文 4.5:1、大字 3:1、控件边界 3:1 作为令牌验收 | — |
| Linear / Notion（产品行为） | 紧凑列表、游标与多选分离、可撤销操作、命令面板 | 深色默认主题 |

## 9. 验证方式

* `python scripts/check_design.py`（CI 同步执行）。
* `npm run typecheck && npm run build`；`npm test --workspaces --if-present`（共享 UI 11、auth-client 31、
  Docs 45 项测试与账户外壳 19 项校验，其中包含"减少动效来自共享样式表"的检查）。
* 浏览器实跑：三个外壳桌面 1440 与窄屏 390 的截图与对比度抽样见 `docs/quality/handoff.md`。
* 弹层与移动端：`services/identity/scripts/ui_e2e_shell.py`（Chromium + WebKit；末行菜单在视口内、抽屉、
  返回链接、探测次数、门户重聚焦、公开首页三态）与 `apps/docs/scripts/ui_e2e_mobile.py`（390×844 主链路）。
  它们是浏览器引擎证据，不是 iOS Safari / Android Chrome 真机证据。
