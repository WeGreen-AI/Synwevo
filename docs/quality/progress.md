# 进度记录（用于接续）

最后更新：2026-09-23（第三轮进行中）

## 当前位置

| 项 | 值 |
|---|---|
| 分支 | `upgrade/automation-api-ui` |
| 基线提交 | `08f2e81`（升级前快照，可回滚到此） |
| 最新提交 | `1d54e40` |
| 相对基线 | 50 个文件，+6413 / −314 行 |
| 远端 | `WeGreen-AI/DocWeave`：当前环境无凭据，`git ls-remote` 认证失败；**尚未推送**，全部提交只在本地 |

## 本轮已解决

| 批次 | 提交 | 内容 |
|---|---|---|
| 任务可靠性 | `44c9b70` | 原子认领 + 租约、事务化发布、取消裁决、运行记录、事件、持久化幂等（DW-001…009、011） |
| 机器接口 | `a7d0db9` | `/api/v1`（39 路由）、API 密钥 + 租户 + 权限范围、声明式定义、健康检查（DW-010、012、030、031） |
| 文档与调用 | `68778cd` | Webhook、`scripts/automation_example.py`、`docs/integration/*`、OpenAPI、运维文档（DW-032） |
| 界面与操作 | `73d8e84` | 命令面板、映射列表键盘与批量操作、主题、记忆布局、可调面板（DW-043…046） |
| 性能 | `5a0c62b` | 大表分页复用已解析工作簿、去掉重复窗口请求、测试路径修复（DW-040…042） |
| 文档与 E2E | `9ac6764` | `ui_e2e_keyboard.py`、`docs/design.md`、更正过时的能力声明 |
| 安全 | `4b3bbd0` | 监听守卫真正生效（DW-047，**P0**：此前 `DOCWEAVE_HOST=0.0.0.0` 可无鉴权对外提供整个界面） |
| 接口契约 | `cd7c6cf` | 鉴权失败的响应也带 `X-Request-ID`（DW-048） |
| 验证记录 | `9fb975a` | `docs/quality/verification.md` |
| 帧时间实测 | `099a926` | `scripts/ui_perf.py`，可见窗口真实帧计时 |
| 许可证 | *(本轮)* | 无 AGPL 部署模式实测并写入 `docs/DEPENDENCIES.md` 第 6 节 |
| 枚举静默回落 | *(本轮)* | DW-049 `layout`、DW-050 字段枚举、**DW-051 合并方式（错填数据）** |

详细证据见 [issues.md](issues.md) 与 [verification.md](verification.md)。

## 当前状态

* 后端测试 **202 passed, 1 skipped**；前端类型检查与生产构建通过；三套浏览器 E2E 通过；无界面调用与 OpenAPI 同步检查通过。
* 用户列出的六项限制已逐项核验，结论见 `verification.md` 第 12 节：四项在代码中早已解决（旧 README 过时），分页有可检测+自动适配的处理，AGPL 有实测可用的无 AGPL 部署模式；只剩「无真实样本」属外部阻碍。
* 范围内已确认缺陷为 0；发布判断见 `verification.md` 第 14 节。

## 第三轮已完成（本轮）

| 项 | 提交 | 证据 |
|---|---|---|
| 唯一跳过项（§7.2） | `04d301f` | 确认是 `test_regression_cases.py::test_committed_cases_still_pass`——`regression/` 为空导致「已提交用例」链路在 CI 中等于没有覆盖。播种一个**明确标注为合成**的用例（每组一份 + 重复明细，15 样本，44 KB）。现在 **203 passed, 0 skipped**。并证明它不是空测试：把期望值改掉后 `run()` 返回 `ok=False` 并给出逐字段 diff |
| 测试污染根因（§6.1） | `0f6b307` | 数据目录在**创建时**记录 `instance_kind` 并由 `/healthz` 暴露；只在目录还为空时写入，因此对已有真实项目的目录设置 `DOCWEAVE_TEST_INSTANCE` **永远无法**把它改成测试实例（有测试锁定）。`scripts/_testenv.py` 要么自动起一个隔离实例，要么拒绝任何 `/healthz` 不报 `instance=test` 的服务器（localhost 或 8765 端口都不算证据）。三套 E2E 默认隔离运行并通过；指向正式实例被拒绝 |
| **DW-052（P0 崩溃）** | `0ecda3b` | 无 PyMuPDF 部署下并发读 PDF **让整个进程 SIGBUS 崩溃**（退出码 138，无 traceback）。8 线程 × 200 次读取复现。修复：`pdfium_shim.LOCK` 进程级可重入锁。修复后 `ok=200/200`、退出码 0。回归测试跑在**子进程**里，去掉锁即失败 |
| DW-053 / DW-054 | `1d54e40` | 文档里的 `DOCWEAVE_LO_PROFILE` 固定路径会让多进程共用 LibreOffice 配置目录（`_LO_LOCK` 跨进程无效）→ 改为父目录语义，始终 `<父目录>/profile-<PID>`；图片模板忽略可配置像素上限且未加载解压炸弹防护 → 改走 `LIMITS` |

## 第三轮调查结论（6 个并行探针，已完成）

* **图片模板脱离 PyMuPDF：可行（yes）**。包装 PDF 对图片模板毫无贡献（实测包装页文字数 0、矢量图形数 0）；10 个函数里只有 4 个真正用到 PyMuPDF，且都只用于容器构造/栅格化/子区域渲染。Pillow 原型与现有链路对比：背景色、字号、断行完全一致，填充框外**逐像素相同**，框内 0.26% 差异且全是抗锯齿。**唯一真正的阻碍是中文字体来源**——目前全产品唯一的 CJK 字体是 `pymupdf.Font('cjk')`（嵌在 PyMuPDF 二进制里），`fonts.py` 只解析字体族名、从不返回文件路径。
* **pdfium 写入 PDF：部分可行（partial）**。真删除与中文写入**已实测跑通**（定位文字对象→移除→重建内容流→用两条独立路径确认旧文字消失；`FPDFText_LoadFont(cid=True)` 嵌入中文 TTF 后中文可渲染可提取）。阻碍：PDFium **不做字体子集化**（单文件 1.7 MB vs 现在 17 KB）、**没有排版引擎**（换行/缩放/对齐/旋转要重写）、**非线程安全**、需要在仓库内自带 CJK 字体。
* **版式（§3）**：`fit` 只按页数验收，不检查字段丢失/裁切/重叠；报告的 `factor` 是请求的步进值而非 clamp 后的实际值；**被测量的字节与被交付的字节不是同一份**；失败时版式报告被丢弃（恰恰是最需要它的时候）。
* **首页（§6.2）**：无搜索/排序/分页/虚拟滚动；卡片不可键盘访问；中文界面里直接显示英文枚举 `completed_with_errors`；失败任务与取消/部分失败同为琥珀色；`per_block` 不在标签表内；列表接口对 40 个项目要 81 次 SQLite 往返。归档需要新列（`deleted` 已被软删除占用）。
* **测试污染（§6.1）**：真实数据目录是 `<repo>/data`，共 68 个项目；其中 36 个没有任何任务，**无法追溯来源**；另有 3 个测试命名的项目被用户在脚本运行数小时后手工改动过——**不能删**。2157 个 blob 引用指向 694 个去重 blob，回收磁盘需要全局 mark-and-sweep。
  **⚠ 这 2157/694 只数了 6 个直接列**（`source_files.{blob,parsed_blob,working_blob}`、`templates.original_blob`、`template_versions.working_blob`、`artifacts.blob`）。另有两类引用藏在 JSON 里，按列扫描看不见：`jobs.snapshot` 的 `$.template.working_blob` / `$.template.fill_options.image.original_blob` / `$.sources[*].{blob,working_blob,parsed_blob}`（真实库里另有 126 处引用、38 个去重 blob），以及 `templates.info.$.image.original_blob`（每个图片模板都会写）。**按 6 列做 mark-and-sweep 会删掉快照仍在引用的 blob，任务重跑/重试随之失效。**
  另外三点在实现前必须处理：① 248 个 blob 被多个项目共享（63/69 个项目都有共享 blob），所以只能按可达性回收，不能按归属级联删除；② `put_blob` 先写文件、后提交引用行，且内容重复时**不重写文件**，所以 mtime 是首次写入时间、永远不更新——真实库里 131 个 blob 在首次写入 1 小时后才获得新引用，单靠 mtime 宽限期判断陈旧是错的；③ `idempotency.request_hash` 与 `api_keys.secret_hash` 也是 64 位十六进制但不是 blob id，全表正则扫描会误标。安全做法：先列磁盘、再读引用、mtime 宽限、在 `BEGIN IMMEDIATE` 内复核候选集、用改名隔离而非直接删除。

## 剩余事项（按价值排序）

1. **真实业务样本**：仍为外部阻碍（未取得脱敏真实文件）。拿到后用界面「保存为回归用例」补充回归集。
2. **Chrome 以外的浏览器**：未测试，不声称兼容。
3. **多进程共享数据目录的长时间并发**：只验证过同进程并发与单次中断恢复。
4. **小屏编辑能力**：当前定位为「查看 + 任务下载」，未做移动端编辑，也未在文档中声称。
5. 少量无调用点的历史辅助函数（`anon_id`、`docx_check`、`preview_removed_text`、`clear_cell_text`、`refers_to_sheet`、`fmt_path`、`norm_text`、`text_color`、`remove_fields`）。已确认没有文档声称它们的行为（与 DW-047 不同），属于可清理项，优先级最低。

## 下一条实际行动

本轮交付条件已满足，「枚举静默回落」缺陷类已排查完毕（`combine`/`aggregate`/`transforms`/`layout`/字段枚举/样本模式/`error_policy`/`formats` 全部确认）。若继续：在 60 Hz 或更低配置的机器上重跑 `scripts/ui_perf.py`，或在拿到脱敏真实文件后用界面「保存为回归用例」补充回归集。
