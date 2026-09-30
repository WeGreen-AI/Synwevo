# 业务应用与个人 Agent：证据

每条注明提交、命令或路径、结果。只记录实际运行过的检查；未运行的写“未运行”。

## 2026-09-29 首轮现场核查（主检出 `main` @ 8266035）

* Git：remote `git@github.com:WeGreen-AI/Synwevo.git`；工作树有另一会话（API 平台 API-4 OAuth 同意）的未提交修改，
  本专项不触碰；同目录另有运行中的会话「Synwevo 平台统一体系与完整交付」（管理员平台主线）。
* 部署：线上 aadcb9e（2026-09-28 12:47 UTC，run 36421027638）；此后 `main` 的运行都被 GitHub Actions 账单拒绝
  （组织每月 2 000 分钟、支出上限 $0，2026-09-28 用尽；10 月 1 日重置或持有人提高上限）。
* 部署机制：`.github/workflows/production.yml` verify → deploy（`git archive` 经受限 SSH 送到 `receive.py`，主机构建
  镜像、备份并校验、`compose up --wait`、`/readyz`）。`nginx.conf` 与 `compose.yaml` 不随部署下发。
* 模型提供方：仓库无任何模型调用；`.venv` 无 `anthropic`/`openai`；本机无产品可用的模型密钥。
* OCR：Docs 内的 Tesseract 子进程（`apps/docs/synwevo_docs/engine/ocr.py`，chi_sim+eng，单词归一化坐标）。
* 编排：不存在通用流程引擎（调查结论，代码核对）。

## 2026-09-30 WP0 + WP1（功能分支 `feat/business-apps` @ 51614b4，worktree `.claude/worktrees/business-apps`）

* 全量回归（worktree 代码，PYTHONPATH 指向 worktree；四个外壳全部构建）：2 905 项收集，2 项跳过，0 失败/错误
  （`pytest -q`，在最后一次 Python 改动之后运行）。其间发现并修复：单源站只构建了新外壳时回落到不存在的外壳返回 500
  （改为 404）；`filling.upload` 写 blob 未登记；`starlette` 未声明；`platform.py test` 新套件。
* 新套件 `apps/suite/tests`：能力层 15 项（显式空间、角色、幂等冲突与并发、事务回滚记失败、人工决定不可自动化、
  深度上限、个人操作、未声明事件、停用应用、两阶段运行转 `unknown`、HTTP 与 CSRF、目录）、Forms 11 项（版本固定、
  三个独立请求、隐藏字段清空与金额精度、草稿≠提交、补件修订、到期/撤销/换链接、公开链接填写人隔离与附件归属、
  附件类型/大小/导出权限、要求登录的专属链接、跨空间隔离与复制不带数据、请求幂等）、共享规则向量 10 项（Python 与
  浏览器两侧各跑一遍，均通过）。集成：`test_suite_mount.py` 7 项（portal/all 提供，app/accounts/messages 按名拒绝，
  未发布即停用，Docs 路由在 all 不受影响）。
* 守卫：check_boundaries（含新增“兄弟应用不得互相导入”规则，已用探针文件证实会报错）、check_brand、check_design、
  check_component_names、brand_assets --check 全部通过；`npm run typecheck`（全部工作区）通过；
  `npm test --workspaces` 通过（账户外壳的拒绝目录测试一度因存储导入能力层而失败，已把运行表 DDL 移入
  `suite_schema/runs.py` 修复）。
* 浏览器（内置浏览器，隔离实例：scratchpad `live/`，端口 33769/33770，文件邮件，夹具账户）：登录 → Forms 库 →
  新建 → 编辑器加字段/改名/必填/单选选项/显示条件（草稿自动保存，刷新后保留）→ 预览中条件显示生效 → 发布版本 1 →
  分享面板发出专属请求 → 请求详情复制链接 → 填写页：必填校验并聚焦、自动保存“草稿已保存 00:29”、刷新恢复草稿、
  切换选项后提交 → 提交详情中隐藏字段未入库 → 要求补充（逐项说明）→ 填写页显示说明并带入上次答案 → 第 2 次修订 →
  确认 → 修订历史。窄屏（646 px）布局。过程中修复：工作空间绑定使在途响应失效导致列表一直“加载中”（先绑定再渲染）、
  描述标签与输入框并排、必填勾选框布局、字段卡片与品牌链接缺少可访问名称、抽屉内按钮被共享样式拉成整行。
* 未做：英文/繁体与宽屏的浏览器走查、API 机器凭据调用（WP9）、Docs/Message 联动（WP7）。
