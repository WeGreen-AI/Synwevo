# 当前开发接续记录

更新时间：2026-09-24。用户随后明确授权 Vultr Tokyo 部署、域名切换和 CI/CD；当前部署进展以
[deployment.md](deployment.md) 为准。广泛产品改进仍暂停，完成本次部署交接后停止，不启动自动续跑。
下方是部署前开发轮的验收历史，不能覆盖新的部署状态，也不代表总体产品目标全部完成。

## 工作区与边界

- 根目录 `/Users/lihiko/repo/wegreen/synwevo`；当前分支 `main`，跟踪 `origin/main`；origin `git@github.com:WeGreen-AI/Synwevo.git`。确切 HEAD 见 `git log -1`。
- Claude 阶段和 Codex 接续的修改已经整合并提交到 main；部署修复随后增量提交。没有 reset、clean、stash、强推或删除历史。
- 用户已经完成 GitHub CLI 登录，授权的生产 CI/CD 已配置；通过验证的 main 提交可以部署到 Vultr Tokyo。旧的“未登录 / main 未整合 / 不部署”记录已被此授权替代。
- 源码接续快照 `/private/tmp/synwevo-codex-resume/source-at-resume.tar.gz` 不含真实数据。测试只用隔离、明确标记的合成样本、临时库及本机服务，未触碰真实 `data/`、身份密钥或用户文件。
- API、机器身份、Webhook、端口及开发者文档没有新增产品 UI 入口。

## 本轮主线已验证完成

日志无目录前缀时位于 `/private/tmp/synwevo-codex-resume/`。下列测试组互有重叠，**不能相加成为总测试数**；均为局部验收，不代表整个脏工作区全绿。

| 改动与实际行为 | 本轮证据 |
|---|---|
| 已消费上传创建请求的幂等重放返回原上传身份，但继续校验当前消费对象权限；完成前保留原 part，丢失/短 part 拒绝，不能扩展零字节 | `upload-crash.log/xml`：36 passed、0 failed/skipped；真实子进程 `os._exit(88)` 覆盖 blob 复制中、完成提交前、提交后清理前，重试后校验字节及 SHA；过期/中止清理不删其他活动上传 |
| 恢复补丁完成主线审阅；缺少撤销 journal 时失效 key、服务/机密客户端、会话/令牌，停任务及外发；最后 owner 丢失冻结空间，不自动晋升 | `restore-main-review.log/xml`：63 passed、0 failed/skipped；根密钥及签名材料不更换；详见 operations / permissions |
| 来源 14 个真实机器操作：结构、视图、搜索、块、词、记录、配置、纠正、代表来源、排序、原文件、定位结果、PNG 页和结构化 locator；UI/API 共用引擎与实际内容边界检查 | `source-contract-final-2.log/xml`：80 passed、0 failed/skipped（来源、契约、定义及生命周期）；较早相关格式/引擎组 `source-selection-local.log/xml`：106 passed，范围不同且重叠 |
| PNG 渲染后重验凭据、导出权、来源版本与项目存活；非法/跨对象选区拒绝，不静默裁切 | 独立审查 `/private/tmp/synwevo-codex-source-review/final-report.md`：快照 64 passed，35 HTTP 补测；不是对之后全部修改的独立背书 |
| 修复 OpenAPI 内联 oneOf 留下 discriminator 隐式失效引用；8 种必填 kind 常量保留 | `source-discriminator-before.log` 先复现断言失败，之后上述 80 项通过；`discriminator-final.log` 用 OAS31Validator 0.6.3 验证当前导出契约两种合法输入通过、未知 kind 拒绝 |
| Python SDK/CLI 补齐来源 14 操作、类型、分页、流式 PNG/原件下载与校验 | 独立安装 wheel 的 19 项测试通过：`sdk-sources-installed.log`；真实 Account client_credentials → Docs，无 Cookie、无管理员 scope，SDK/CLI 各 12 DOCX、各 1 预览，逐份独立读回，134 业务 HTTP 请求；`/private/tmp/synwevo-codex-python-sdk/sources/run-1/acceptance.json` |
| 非 BMP 文字选区统一为 Unicode 码点；DOM UTF-16 在边界转换，DOCX 切片/高亮、TXT/PPTX 选区和保存映射一致 | `unicode-typecheck.log`、`unicode-build.log` 成功；`unicode-browser-3/results.json`：Chrome 153.0.8010.53，DOCX/TXT/PPTX 三种 `😀Alpha` 合成来源，选 Alpha 发 1..6、保存后刷新、真实 DOCX 预览下载读回 Alpha；0 pageerror，截图及 3 份文件同目录 |

Unicode 浏览器测试通过 DOM Range 精确选择跨 run 文本，再走真实 React mouseup、HTTP locator 和映射对话框；不是手工鼠标精度、OS 输入法或认证测试。前两次脚本断言把映射编号装饰算进文档文字而失败，日志 `unicode-browser.log` / `unicode-browser-2.log` 保留；最终按真实 parser run 节点精确比较，没有移除内容断言。API 边界没有放宽。

契约清单 `apps/docs/sdk/python/operation-coverage.json`：**82 operation IDs / 67 paths；80 有 SDK 方法、70 有 CLI 入口、55 在两组 HTTP 验收中观察到成功响应**。readyz 与签名令牌下载未封装；“有方法”“收到成功响应”均不等于该操作所有行为经过端到端验收。PNG/locator 已交付，OCR 执行机器接口仍未交付。

安装 wheel 位于 `/private/tmp/synwevo-codex-python-sdk/sources/wheel/synwevo_docs_sdk-0.1.0-py3-none-any.whl`，SHA-256 `f63dde3c2996517d343bd9c98aa9de799ed527adae8cde6a6811e251c1871358`。SDK 运行代码与该包一致；之后 README 只补充 Unicode 契约。

## 本轮较早通过、不要误作最新全量回归

- 上传修复后的 Python 完整链路：`/private/tmp/synwevo-codex-python-sdk/upload-replay/run-1`，24 上传模板 DOCX、48 内建 DOCX/XLSX、7 预览；300 项取消/default retry。来源新增操作另由上面的 sources/run-1 验收。
- TypeScript / CLI / n8n 2.40.5（Node 24）完整链路：`/private/tmp/synwevo-codex-ts-n8n/run-14-upload-replay`，三渠道各生成 12 DOCX；Webhook 接收器再下载同组 12 份，不重复计数。该链路未覆盖后来新增的来源专用客户端包装。
- 账户/门户真实 Chrome、虚拟 Passkey、成员分页/角色和重认证：`/private/tmp/synwevo-codex-account-ui/phase2/final-report.md`、phase3 和 phase4 记录；Docs 编辑/键盘/100 行性能：`/private/tmp/synwevo-codex-docs-ui/phase3/report.md`。
- 隔离锁文件安装、三个外壳构建、三角色本机启动：`/private/tmp/synwevo-codex-infra/report.md`，早于最新修改，Docker/Linux 未跑。
- 较早完整后端 `integrated.log/xml` 是 **1418 passed、1 failed、1 skipped**；失败的代理跳数用例随后针对性 5 项通过。没有再次运行最终整合全套，不能写成全部通过。
- 初次在仓库 venv 收集 SDK 测试因未安装 SDK 出现 2 个 collection errors；正确的独立安装环境随后 19 项通过。首次 wheel 构建缺 setuptools，改用已具备构建工具的 SDK 隔离环境成功。保留失败日志，不当作产品功能失败或跳过。

## 复现入口与记录

在仓库根运行，SDK 使用单独安装环境。完整启动与固定业务端口见 `docs/operations.md`；测试动态选用 loopback 空闲端口，真实端口在每个 run 的日志里。最终 Unicode 实例为 `127.0.0.1:52863`，已关闭并清理临时库；不要把它写作常驻入口。其他 SDK 验收的上下文同样负责关闭自己启动的服务。

```bash
.venv/bin/python -m pytest apps/docs/tests/test_upload_durability.py apps/docs/tests/test_upload_replay.py apps/docs/tests/test_api_contract_uploads.py
.venv/bin/python -m pytest tests/integration/test_security_journal.py apps/docs/tests/test_snapshot.py
.venv/bin/python -m pytest apps/docs/tests/test_api_source_operations.py apps/docs/tests/test_api_docs.py apps/docs/tests/test_api_contract_definitions.py apps/docs/tests/test_source_lifecycle.py
/private/tmp/synwevo-codex-python-sdk/venv/bin/python -m unittest discover -s apps/docs/sdk/python/tests -v
.venv/bin/python tests/integration/sdk_acceptance/python_sources.py --sdk-python /private/tmp/synwevo-codex-python-sdk/venv/bin/python --evidence /private/tmp/synwevo-source-next-run
npm run typecheck -w @synwevo/docs-web
npm run build -w @synwevo/docs-web
.venv/bin/python apps/docs/web/tests/browser_unicode_selection.py --evidence /private/tmp/synwevo-unicode-next-run
.venv/bin/python scripts/check_boundaries.py
.venv/bin/python scripts/check_brand.py
```

SDK 验收目录必须为空。机器调用/安装文档在 `apps/docs/sdk/python/README.md`、`docs/integration/api.md`；新应用和模块边界在 `docs/architecture/monorepo.md`。临时证据可能被系统清理，仓库保留可复现脚本和当前状态；没有提交数据库或文档样本。

## 暂停后尚未完成的本机工作（不是外部阻塞）

1. 继续按 `docs/engineering/review-coverage.json` 补读部分/索引项；当前工作区全部自有代码审閱仍未完成，历史 288 文件全读不覆盖最新修改。未完成项绝不改记为“已读”。
2. OCR 仍缺完整受限、可恢复的机器执行链路；现有 `Views.run_ocr` 的完成发布阶段还需来源/项目存活、版本与当前授权复核。不要仅挂同步路由就宣称完成。
3. TypeScript 新来源接口包装/类型及对应 CLI/n8n 验收未完成；现有 .mjs 客户端不是完整带声明的 TypeScript SDK。
4. 恢复无法从不存在的 journal 重建人类成员撤销、删除和密码历史；外部 journal 追加与数据库提交没有跨介质原子性，恢复放行仍需操作核对。blob 最终 GC、安全隔离、完整资源限制等应按原问题清单逐项闭环。
5. 代码稳定后完成最终整合后端、三个前端外壳、门户/深链接/自动化三链路、备份恢复与负面安全的同版本回归。不能把局部通过当综合交付。
6. main 整合和授权推送已完成；部署验收及剩余人工配置见 deployment.md。后续产品工作仍需遵守模块边界，不混入无关修改或强推。

## 仍需用户材料或外部条件

- 脱敏真实复杂业务文档用于格式/排版与性能验收；当前只使用合成/公开兼容样本，未声称覆盖真实客户环境。
- 物理 Passkey/生物认证设备及互操作；此前虚拟认证器不等于真实设备验证。
- 云工作流账号与相应外部测试授权；部署公网服务本身不等于完成 Make 等云工作流验收。

**产品路线图暂停；本次授权部署的真实状态、管理员初始化与邮件限制统一记录在 deployment.md。**
