# 测试方法

## 1. 测试数据

```bash
.venv/bin/python apps/docs/scripts/make_fixtures.py        # 生成到 testdata/（pytest 首次运行时也会自动生成）
```

全部为合成数据（姓名以「测试」开头、编号虚构），包括：100 条客户 XLSX、列顺序变化/重名列变体、合同 DOCX 模板（跨 run 文字、页眉页脚、图片、表格）、20 份同结构检测表 DOCX 与报告模板、100 条订单明细（15 个订单，行号被打乱）与 DOCX/XLSX 订单模板、3×4 区域来源与模板、文本 PDF、含旋转页的 PDF 模板、AcroForm 模板、扫描 PNG/PDF、PPTX 模板、GB18030/UTF-8 CSV（含引号内换行）、损坏文件与不支持文件。

## 2. 自动化测试（pytest）

```bash
python scripts/platform.py setup                      # 或手动：.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                          # 四个套件：Synwevo Docs、账户服务、Synwevo Message、跨模块集成
.venv/bin/python -m pytest apps/docs/tests      # 只跑 Synwevo Docs
.venv/bin/python -m pytest services/identity/tests  # 只跑账户服务
.venv/bin/python -m pytest apps/messages/tests      # 只跑 Synwevo Message（会话由 Sessions 直接铸造：messages 角色没有 /api/auth/login）
.venv/bin/python -m pytest tests/integration        # 只跑跨模块验证
.venv/bin/python scripts/check_boundaries.py        # 模块依赖边界
.venv/bin/python scripts/check_design.py            # 统一白蓝设计系统：颜色只来自共享令牌，无私有色板/深色残留
npm test --workspaces --if-present                 # 语言资源、UI/会话/二进制请求竞态及账户组件契约
```

| 文件 | 内容 |
|---|---|
| `test_engine.py` | 数字格式渲染、值的真实性（前导零、长数字、0 与空、布尔、日期）、CSV 编码与引号换行、受控转换、格式识别、引用平移规则、DOCX 跨 run 字段、XLSX 插入行、PDF 四种旋转坐标与 redaction、溢出报错、文件名安全与去重 |
| `test_acceptance_1_4.py` | 验收场景 1–4（含 XLSX 分组 + 公式） |
| `test_acceptance_5_8.py` | 验收场景 5–8 |
| `test_acceptance_9_14.py` | 验收场景 9–14（含服务重启恢复、任务快照隔离、LibreOffice 打开校验） |
| `test_formats.py` | PPTX、XLS/ODS/DOC/ODT/RTF/PPT、HTML/TXT/MD、CSV 导出、内置编辑器、上传防护 |
| `test_claims.py` | 能力矩阵中具体声明的佐证（表格对象/打印区域/图表/批注平移，扫描 PDF 像素清除） |
| `test_workflow.py` | 单个文件重新定位（覆盖定位）、撤销/重做、删除字段级联删除映射、预览失效、并发修改冲突、模板复制隔离与原始模板不变 |

依赖 LibreOffice 与 Tesseract 的测试需要这两个工具已安装。

## 3. 浏览器端到端（真实页面操作）

需要本机安装 Google Chrome。只对初始化时标记为 test 的隔离实例运行修改数据的浏览器脚本；不要使用根目录真实 `data/`。本轮三语言/组织/API 台验收见 [quality/handoff.md](quality/handoff.md)。

在新建的私有临时目录配置 `SYNWEVO_DATA_DIR`、`SYNWEVO_TEST_INSTANCE=1`、`SYNWEVO_MAIL_MODE=file`、`SYNWEVO_MAIL_DIR` 和 `SYNWEVO_REGISTRATION=open`，按 [operations.md](operations.md) 启动。文件邮件目录权限0700；测试不向真实地址发送邮件。

```bash
python scripts/platform.py up --init  # 仅在上述全新隔离目录中初始化，命令自行管理服务进程
.venv/bin/python apps/docs/scripts/ui_e2e.py           # 新建项目→上传→设模板→选文字建字段→定义样本→点选单元格建映射→预览→生成 100 份→下载 ZIP 核对→刷新→手机宽度查看任务
.venv/bin/python apps/docs/scripts/ui_e2e_more.py      # 分组+XLSX 明细（拖选区域）、PDF 鼠标框选字段、内置文档编辑器、内置表格编辑器
.venv/bin/python apps/docs/scripts/ui_e2e_keyboard.py  # 命令面板（⌘K）、映射列表键盘与批量操作+撤销、主题与文档纸张、布局记忆与减少动效
.venv/bin/python apps/docs/scripts/ui_perf.py <项目ID>   # 可见窗口中的真实帧时间：首个可操作视图、选区/键盘响应、滚动与拖拽帧间隔、长任务、内存
```

旧 Docs E2E 的截图路径由各脚本说明；本轮账户/API 截图存放在私有临时目录，不提交凭据、邮件或测试数据库。

```bash
.venv/bin/python -m services.identity.tests.browser_onboarding_round \
  --accounts http://accounts.localhost:29770 --portal http://portal.localhost:29769 \
  --docs http://docweave.localhost:29765 --mail-dir "$SYNWEVO_MAIL_DIR" \
  --output-dir /private/tmp/synwevo-onboarding-new-run
.venv/bin/python apps/docs/web/tests/browser_i18n_artifacts.py --run
.venv/bin/python apps/docs/web/tests/browser_i18n_artifacts.py --run \
  --base-url http://docweave.localhost:29765 \
  --accounts-url http://accounts.localhost:29770 \
  --portal-url http://portal.localhost:29769 \
  --mail-dir "$SYNWEVO_MAIL_DIR" --evidence /private/tmp/synwevo-docs-new-run
```

输出目录必须新建且私有。第一个脚本从公开注册创建独立合成账户，通过本地真实邮件和浏览器验证，拒绝非 test 实例。
Docs 脚本默认运行自己的无账户测试实例，验证三语言业务界面和真实产物；这个模式不构成 SSO 证据。
带三个源站及邮件目录的模式从公开注册开始，消费自身合成账户的本地邮件、完成登录和个人空间引导，
从门户进入 Docs 后，三语言分别上传、映射、预览、生成及下载。读回 XLSX 原文、数值与公式缓存；
不导出浏览器 Cookie 或令牌，不以数据库写入预置业务对象。
API 台浏览器脚本 `tests/integration/api_platform_browser_acceptance.py` 需要0600夹具文件指向已经通过真实注册验证的隔离管理员/所有者；
管理员资格通过受控测试引导准备，不由公开注册决定。脚本不伪造业务响应、不把管理员 Cookie 当机器凭据。

`python -m services.identity.tests.browser_security_round --mail-dir "$SYNWEVO_MAIL_DIR"`
使用同样的三个 `--accounts`/`--portal`/`--docs` 参数（默认297xx）。每种语言都公开注册自己的合成账户、
完成个人引导，再实际操作 Passkey 登记/改名/登录/移除、TOTP 正负验证和恢复码单次消费。
Chrome CDP 虚拟 CTAP2 是测试设施，不是实体认证器验证；秘密只留内存，截图遮盖凭据输入，报告不含恢复码或种子。
反复执行注册脚本会触发真实限流；按测试场景使用独立初始化的数据目录，不关闭限流或重置用户库。

## 4. 性能

```bash
.venv/bin/python apps/docs/scripts/benchmark.py
```

输出 JSON，包含各场景耗时与进程峰值内存（结果记录在 `docs/ACCEPTANCE.md`）。

## 5. 人工检查清单（可选）

1. 用 Microsoft Word / Excel / PowerPoint 打开生成的文件，确认无修复提示（本次结果见 `docs/ACCEPTANCE.md` 第 2 节）。
2. 用真实（脱敏）业务文件重复主流程，并把问题文件加入 `testdata/` 与回归测试。
