# 依赖组件、许可证与部署要求

Synwevo 自身以 **AGPL-3.0-or-later** 许可，`apps/docs/sdk/` 下的 SDK 为 Apache-2.0，名称、Logo 与图标不在许可之内（见根目录 `LICENSE`、`NOTICE`、`TRADEMARKS.md`）。下列第三方组件均为开源组件。文档的解析、转换、文字识别与生成都在本机 / 用户自有服务器完成；例外是可选的个人 Agent：它用官方 SDK（`anthropic`）调用所配置的模型提供方 Anthropic，需要该提供方的账号与 API 密钥并按用量计费，对话以及用户选择发送的文件内容会发给它（见 1.2 节）。版本与许可证取自实际安装包的元数据（`pip show` / `package-lock.json`），核对日期 2026-09-23，1.2 节与第 3 节的补充条目核对于 2026-10-25。完整的运行时清单在根目录 `NOTICE`（与 `apps/docs/synwevo_docs/licensing.py` 的 `THIRD_PARTY` 逐条一致），`apps/docs/tests/test_licensing.py` 对照 `infra/server/requirements.lock` 与 `package-lock.json` 检查它没有漏项。

## 1. 服务端（Python，`apps/docs/requirements.txt`）

| 组件 | 版本 | 许可证 | 费用 | 用途 / 处理能力 | 限制与说明 |
|---|---|---|---|---|---|
| FastAPI / Starlette / Uvicorn | 0.141.1 / 1.6.0 / 0.53.0 | MIT / BSD-3 / BSD-3 | 免费 | HTTP API、静态界面、上传下载 | 单进程运行（后台任务线程在进程内） |
| python-multipart | 0.0.32 | Apache-2.0 | 免费 | 多文件上传 | 上传大小由 `SYNWEVO_DOCS_MAX_FILE_MB` 限制 |
| websockets | 15.0.1 | BSD-3-Clause | 免费 | Uvicorn 的 WebSocket 协议实现（纯 `uvicorn` 不带任何一种）；Docs 实时协同的连接靠它握手 | 未安装时 uvicorn 对升级请求答 404 并打警告，编辑器退回单人模式 |
| openpyxl | 3.1.5 | MIT | 免费 | **读取** XLSX 值/格式、预览渲染、构建内置表格模板 | 其保存会丢失图表等对象，因此**填充不经 openpyxl 保存**（见 xlsx_doc.py） |
| python-docx | 1.2.0 | MIT | 免费 | 构建内置文档模板、重新打开校验输出 | 字段标记/填充使用 lxml 直接处理 XML |
| python-pptx | 1.0.2 | MIT | 免费 | 重新打开校验 PPTX 输出、生成测试数据 | PPTX 填充使用 lxml 直接处理 XML |
| lxml | 6.1.3 | BSD-3 | 免费 | OOXML 解析与改写（禁用外部实体与网络） | — |
| **PyMuPDF** | 1.28.2 | **AGPL-3.0 或 Artifex 商业许可（双许可）** | 开源使用免费；商业闭源分发需向 Artifex 购买许可（价格需询价） | PDF 解析、渲染、文字坐标、redaction 删除原文字、写入文字、表单字段、字体子集化 | **注意 AGPL**：它是唯一的第三方 AGPL 组件，默认构建（镜像、CI、`scripts/platform.py setup`）都安装它。向网络用户提供服务时按 AGPL-3.0 第 13 条提供对应源代码（`/api/source-code`，见第 6 节）；Synwevo 本身也是 AGPL-3.0-or-later。`SYNWEVO_DOCS_PDF_ENGINE=pdfium` 只切换引擎，不会把它从镜像中去掉；目前没有经过验证的不含它的平台构建（第 6 节） |
| **pypdfium2** | 5.13.0 | **Apache-2.0 / BSD-3-Clause** | 免费 | pdfium 引擎模式下的 PDF **读取**引擎：页面几何与旋转、文字坐标、全文提取、页面渲染、OCR 前处理 | 只读：不提供 redaction、写入文字与表单扁平化，因此该模式下 PDF / 图片**模板**不可用（见第 6 节） |
| fontTools | 4.65.0 | MIT | 免费 | PDF 中文字体子集化（输出从 3.5 MB 降到约 10 KB） | — |
| Pillow | 12.3.0 | MIT-CMU | 免费 | 图片读取、EXIF 方向校正、OCR 前处理 | 像素上限 `SYNWEVO_DOCS_MAX_IMAGE_MPIX` |
| charset-normalizer | 3.5.1 | MIT | 免费 | CSV/TXT 编码猜测（兜底） | 优先按 BOM / UTF-8 / GB18030 判断 |
| xlrd | 2.0.2 | BSD | 免费 | 无 LibreOffice 时直接读取 XLS | 只支持 .xls |
| pycrdt | 0.14.6 | MIT | 免费 | 内置文档模板的多人实时协同：服务端的共享文档（y-crdt 的 Python 绑定），与浏览器端 Yjs 同一协议 | 房间在 Docs 进程内存中，状态持久化到唯一数据库；多 worker 部署需要粘性路由（见 docs/platform/docs-collaboration.md） |
| olefile | 0.47 | BSD | 免费 | 识别 DOC/XLS/PPT 与加密的 OOXML | — |

### 1.1 Model Context Protocol（`apps/suite/requirements.txt`，锁定在 `infra/server/requirements.lock`）

工作空间源站上的 MCP 服务端与 Agent 的 MCP 客户端（[`platform/mcp.md`](platform/mcp.md)）用官方 Python SDK；版本与许可证取自
`.venv` 中已安装包的元数据，核对日期 2026-10-24。选择理由与研究记录在 `platform/mcp.md` §2.5。

| 组件 | 版本 | 许可证 | 费用 | 用途 | 限制与说明 |
|---|---|---|---|---|---|
| **mcp** | 2.3.0（2026-10-02 发布，`Requires-Python >=3.10`） | MIT | 免费 | 官方 MCP Python SDK：入站用低阶 `Server` + `StreamableHTTPSessionManager`（无状态），出站用 `Client` + `streamable_http_client`；实现修订 2026-07-28，并在同一服务端上服务 2025-03-26 / 2025-06-18 / 2025-11-25 的客户端 | v1.x 已进入维护期（只修安全问题），不要回退；只用 Streamable HTTP，不用 stdio |
| mcp-types | 2.3.0 | MIT | 免费 | SDK 的协议类型（`Tool`、`CallToolResult`、`ProtectedResourceMetadata` 等） | 与 `mcp` 同版本 |
| httpx2 | 2.13.1 | BSD-3-Clause | 免费 | SDK 的 HTTP 客户端；出站的钉住传输（`PinnedTransport`）建在它上面 | 引入 MCP 之前已在锁文件中，版本未变 |
| jsonschema | 4.26.0 | MIT | 免费 | 出站：按外部服务公布的 JSON Schema（Draft 2020-12）校验模型给的工具输入 | 依赖 jsonschema-specifications、referencing、rpds-py、attrs |
| jsonschema-specifications / referencing / rpds-py / attrs | 2025.9.1 / 0.37.0 / 2026.9.1 / 26.1.0 | MIT / MIT / MIT / MIT | 免费 | jsonschema 的依赖（元 schema、`$ref` 解析、持久化数据结构） | — |
| sse-starlette | 3.5.0 | BSD-3-Clause | 免费 | SDK 服务端的请求级 SSE 响应 | — |
| opentelemetry-api | 1.45.1 | Apache-2.0 | 免费 | SDK 的可观测性接口（只有 API 包，没有 exporter，不向任何地方发送数据） | 本仓库不配置 exporter |
| PyJWT | 2.15.1 | MIT | 免费 | SDK 授权模块的依赖 | 本仓库的令牌校验不用它（走账户服务的 `Grants`） |

去掉这一组：移除 `synwevo_suite/app.py` 与 `agent/__init__.py` 里的两处 `install` 调用即可，表与列是加法，数据不丢（`platform/mcp.md` §6）。

### 1.2 平台的其余运行时组件（`infra/server/requirements.lock`，核对日期 2026-10-25）

| 组件 | 版本 | 许可证 | 用途 | 说明 |
|---|---|---|---|---|
| anthropic（含 jiter、docstring-parser） | 1.9.0 / 0.17.0 / 0.18.0 | MIT | 个人 Agent 调用模型提供方 Anthropic 的官方 SDK | 可选功能；需要部署方或用户自己的 API 密钥，按用量计费，请求发往 `api.anthropic.com` |
| py-vapid / http-ece | 1.9.4 / 1.2.1 | **MPL-2.0** / MIT | Web 推送的 VAPID 签名与载荷加密（`synwevo_core/push.py`） | MPL-2.0 是文件级弱著作权保护，未修改、作为独立包使用 |
| certifi / truststore | 2026.7.22 / 0.10.4 | **MPL-2.0** / MIT | 出站 HTTPS 的根证书与系统证书库（HTTP 客户端的依赖） | 未修改、作为独立包使用 |
| XlsxWriter / et-xmlfile | 3.2.9 / 2.0.0 | BSD-2-Clause / MIT | python-pptx 与 openpyxl 的依赖 | — |
| typing_extensions | 4.16.0 | PSF-2.0 | 类型标注的运行时支持 | — |

其余传递依赖（pydantic-core、anyio、h11、idna、click、httpcore 等）与许可证见 `NOTICE`。

## 2. 系统组件（可选，但决定部分能力）

| 组件 | 验证版本 | 许可证 | 费用 | 用途 | 未安装时 |
|---|---|---|---|---|---|
| **LibreOffice** | 26.8.0.3 | MPL-2.0 | 免费 | DOC/XLS/PPT/ODT/ODS/ODP/RTF 转换；DOCX/XLSX/PPTX → PDF 导出；内置公式引擎不支持的函数的后备计算 | 这些功能在界面标为「缺依赖」且不可选择，其余功能正常；XLS 仍可用 xlrd 读取 |
| **Tesseract OCR** | 5.5.0 | Apache-2.0 | 免费 | 扫描 PDF / 图片识别 | OCR 按钮提示未安装；扫描件无法取值 |
| tessdata_fast（chi_sim / eng / osd） | 4.x 模型 | Apache-2.0 | 免费 | 中文简体 + 英文识别模型（约 17 MB，放在 `apps/docs/tessdata`，与上游逐字节一致，来源、哈希与许可证见该目录的 `README.md` 与 `LICENSE`）；本地开发使用，镜像改用 Debian 的语言包 | 缺少时只能用系统自带语言 |
| Droid Sans Fallback（随 PyMuPDF 提供） | — | Apache-2.0 | 免费 | PDF 写入中文 | — |
| 中文字体（Linux 部署建议 `fonts-noto-cjk`） | — | SIL OFL | 免费 | LibreOffice 导出 PDF 时显示中文 | PDF 导出中文可能显示为方框；模板使用的字体未安装时上传即提示 |
| ClamAV（生产部署的独立容器 `clamav/clamav`） | 1.4 | GPL-2.0-only | 免费 | 上传文件的恶意软件扫描，经网络（clamd）调用，不与本程序链接；容器每小时向病毒库更新服务器取更新 | 不配置 `SYNWEVO_CLAMD_ADDRESS` 时界面明确显示未配置扫描引擎；生产设置 `SYNWEVO_MALWARE_SCAN=required`（见 `docs/operations.md`） |
| nginx（生产部署的反向代理容器） | 1.28 | BSD-2-Clause | 免费 | TLS 终止与反向代理（`infra/production/nginx.conf`） | 本地开发不需要 |

## 3. 前端（各外壳的 `package.json`，版本锁定在根目录 `package-lock.json`，构建后为静态文件）

| 组件 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| React / React DOM | 19.3.0 | MIT | 界面 |
| React Router | 7.18.4 | MIT | 路由 |
| TipTap（react、starter-kit、table、text-style、text-align、image、highlight、pm） | 3.31.3 | MIT | 内置文档模板编辑器（仅使用开源扩展，未使用 TipTap Pro） |
| Yjs / y-websocket / y-protocols / lib0 / @tiptap/y-tiptap | 13.6.33 / 3.1.0 / 1.0.7 / 0.2.118 / 3.0.9 | MIT | Docs 文档实时协同的浏览器端（与服务端 pycrdt 同一协议） |
| qrcode | 1.5.4 | MIT | 账户页 TOTP 绑定二维码 |
| Vite / TypeScript（仅构建） | 8.3.0 / 7.0.2 | MIT / Apache-2.0 | 构建工具 |

其余随界面打包的传递依赖均为 MIT 或 ISC（`apps/docs/tests/test_licensing.py` 检查锁文件中的每个非开发依赖）。

## 4. 开发与测试（`requirements-dev.txt`）

| 组件 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| pytest / httpx | 9.1.1 / 0.28.1 | MIT / BSD-3 | 自动化测试 |
| Playwright（Python） | 1.63.0 | Apache-2.0 | 浏览器端到端测试，使用本机已安装的 Google Chrome（不下载浏览器） |

## 5. 部署要求

* Python 3.12 或更高（已在 3.14 验证），Node.js 20+（仅构建界面时需要）。
* 内存：实测 5 000 份 DOCX 的批处理全过程进程峰值约 440 MB（见 `docs/ACCEPTANCE.md`）；建议至少 2 GB，启用 LibreOffice 转换时建议 4 GB。
* 磁盘：原文件、模板版本与输出以内容哈希存放在 `SYNWEVO_DATA_DIR`，重复内容只存一份；需按生成量预留空间。
* 文档处理不需要外部网络（安装依赖时除外）。用到外部网络的都是可选功能：个人 Agent 调用模型提供方、Agent 连接外部 MCP 服务、Web 推送经浏览器厂商的推送服务、邮件经所配置的 SMTP 服务；生产的 ClamAV 容器定时更新病毒库。

## 6. pdfium 引擎与不含 PyMuPDF 的构建

> **现状（2026-10-25）：没有经过验证的不含 PyMuPDF 的平台构建。** 默认构建（`infra/Dockerfile`、CI、`scripts/platform.py setup`）都安装 PyMuPDF。`SYNWEVO_DOCS_PDF_ENGINE=pdfium` 只切换引擎，MuPDF 二进制仍在镜像里，它的 AGPL 义务仍然适用。`apps/docs/requirements-pdfium.txt` 只覆盖 Synwevo Docs，缺少 2026-09-26 起实时协同必需的 `pycrdt`（`collab.py` 在模块级导入它），也没有任何构建或 CI 从它安装。下面 2026-09-23 的实测早于 pycrdt 的引入，只说明引擎切换本身的能力边界。另外，Synwevo 本身以 AGPL-3.0-or-later 许可：去掉 PyMuPDF 去掉的是第三方 AGPL 组件，不是 Synwevo 自己的许可条款。

PyMuPDF 是唯一的第三方 AGPL 组件。切换到 pdfium 引擎不需要修改代码：

```bash
SYNWEVO_DOCS_PDF_ENGINE=pdfium .venv/bin/python -m synwevo_server.run
# 不安装 PyMuPDF 时引擎会自动回落到 pdfium（但见上面的现状：目前没有这样的平台构建）
```

此模式下 `pypdfium2`（Apache-2.0 / BSD-3）承担全部 PDF 读取工作。

| 能力 | pdfium 引擎 |
|---|---|
| XLSX / DOCX / PPTX 模板与生成（含公式计算、重复明细、重复内容块） | **完全可用** |
| PDF 来源文件：解析、页面预览、文字坐标、全文查找、OCR、取值 | **完全可用** |
| DOCX / XLSX → PDF 导出 | **可用**（由 LibreOffice 转换，与 PyMuPDF 无关） |
| PDF 模板、图片模板（需要删除原文字、写入文字、表单扁平化） | **不可用**，接口返回 `engine_unavailable` 并说明原因 |

实测（2026-09-23，用导入拦截器让 `import pymupdf` 真正失败，模拟未安装）：

* 服务正常启动，`/readyz` = `{"database":"ok","storage":"ok","worker":"ok"}`；
* `/api/capabilities` 报告 `pdf_engine: pdfium`、`pdf_engine_license: Apache-2.0 / BSD-3-Clause`，并把 PDF/图片模板行标为 `unavailable` 且写明原因；
* 按 `docs/integration/automation.md` 跑通完整无界面流程：10 条样本 → 10 份 DOCX，任务 `completed`，产物校验通过；
* 上传 PDF 来源：2 页几何正确、页面文字与中文词坐标正确（`姓名：/测试赵一/电话：/13900000001`）、全文查找命中 3 处并返回坐标、页面渲染 PNG 200（38 452 字节）；
* 上传 PDF / 图片作为**模板**被拒绝：`{"error":"engine_unavailable","message":"当前部署使用 pdfium 引擎…如需 PDF / 图片模板，请按 docs/DEPENDENCIES.md 启用 PyMuPDF"}`，带请求追踪 ID，不是崩溃也不是静默失败。

回归覆盖：`apps/docs/tests/test_pdfium_engine.py` 对同一批 PDF 用两个引擎交叉比对页面几何与词坐标（要求 80% 以上的词一致、坐标差 < 0.02），并断言模板类操作被明确拒绝、能力矩阵把对应行标为 `unavailable`。

### 对应源代码（AGPL-3.0 第 13 条）

Synwevo 本身是 AGPL-3.0-or-later，默认构建又包含 PyMuPDF。服务内置了「对应源代码」提供方式，用于 AGPL 第 13 条的网络分发义务：

```bash
curl -O http://<你的部署地址>/api/source-code      # 运行中的服务按自身安装目录打包
```

实测（2026-09-23）：返回 479 296 字节的 tar.gz，153 个条目。2026-10-25 在本地检出上重测（`licensing.source_archive()`）：约 37 MB、2 206 个条目，含全部应用（Docs、门户、Message、`apps/suite` 的业务应用与个人 Agent）、平台共享包与前端包、账户服务、部署组装、脚本、测试、文档、依赖锁文件、`LICENSE` / `NOTICE` / `TRADEMARKS.md` 和 `SOURCE_OFFER.txt` 说明；**不包含** `data/`（用户文件）、`.venv/`、`node_modules/`、编译产物与 tessdata 模型文件。压缩包的路径清单与 `infra/Dockerfile` 的 COPY 行双向核对（`apps/docs/tests/test_licensing.py`）。
产品界面「关于与许可」页展示第三方组件与许可证；源代码包由登录用户从 `/api/source-code` 下载。
