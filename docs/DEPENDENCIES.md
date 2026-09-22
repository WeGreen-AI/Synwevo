# 依赖组件、许可证与部署要求

所有组件均为开源、**无需付费**；未使用任何商业 SDK、外部账号或云服务，所有文件处理在本机 / 用户自有服务器完成。版本与许可证取自实际安装包的元数据（`pip show` / `package.json`），核对日期 2026-09-23。

## 1. 服务端（Python，`backend/requirements.txt`）

| 组件 | 版本 | 许可证 | 费用 | 用途 / 处理能力 | 限制与说明 |
|---|---|---|---|---|---|
| FastAPI / Starlette / Uvicorn | 0.141.1 / 1.6.0 / 0.53.0 | MIT / BSD-3 / BSD-3 | 免费 | HTTP API、静态界面、上传下载 | 单进程运行（后台任务线程在进程内） |
| python-multipart | 0.0.32 | Apache-2.0 | 免费 | 多文件上传 | 上传大小由 `DOCWEAVE_MAX_FILE_MB` 限制 |
| openpyxl | 3.1.5 | MIT | 免费 | **读取** XLSX 值/格式、预览渲染、构建内置表格模板 | 其保存会丢失图表等对象，因此**填充不经 openpyxl 保存**（见 xlsx_doc.py） |
| python-docx | 1.2.0 | MIT | 免费 | 构建内置文档模板、重新打开校验输出 | 字段标记/填充使用 lxml 直接处理 XML |
| python-pptx | 1.0.2 | MIT | 免费 | 重新打开校验 PPTX 输出、生成测试数据 | PPTX 填充使用 lxml 直接处理 XML |
| lxml | 6.1.3 | BSD-3 | 免费 | OOXML 解析与改写（禁用外部实体与网络） | — |
| **PyMuPDF** | 1.28.2 | **AGPL-3.0 或 Artifex 商业许可（双许可）** | 开源使用免费；商业闭源分发需向 Artifex 购买许可（价格需询价） | PDF 解析、渲染、文字坐标、redaction 删除原文字、写入文字、表单字段、字体子集化 | **注意 AGPL**：若把本软件作为网络服务提供给第三方，且修改了源代码，需要按 AGPL 向用户提供对应源代码；仅在组织内部自用通常可满足。若无法接受 AGPL，可购买 Artifex 商业许可，或替换为 pypdf（BSD，能读取文字/表单，但**不能**真正删除页面文字）+ 其他渲染器，届时 PDF 模板「删除原值」能力将不可用 |
| fontTools | 4.65.0 | MIT | 免费 | PDF 中文字体子集化（输出从 3.5 MB 降到约 10 KB） | — |
| Pillow | 12.3.0 | MIT-CMU | 免费 | 图片读取、EXIF 方向校正、OCR 前处理 | 像素上限 `DOCWEAVE_MAX_IMAGE_MPIX` |
| charset-normalizer | 3.5.1 | MIT | 免费 | CSV/TXT 编码猜测（兜底） | 优先按 BOM / UTF-8 / GB18030 判断 |
| xlrd | 2.0.2 | BSD | 免费 | 无 LibreOffice 时直接读取 XLS | 只支持 .xls |
| olefile | 0.47 | BSD | 免费 | 识别 DOC/XLS/PPT 与加密的 OOXML | — |

## 2. 系统组件（可选，但决定部分能力）

| 组件 | 验证版本 | 许可证 | 费用 | 用途 | 未安装时 |
|---|---|---|---|---|---|
| **LibreOffice** | 26.8.0.3 | MPL-2.0 | 免费 | DOC/XLS/PPT/ODT/ODS/ODP/RTF 转换；DOCX/XLSX/PPTX → PDF 导出；内置公式引擎不支持的函数的后备计算 | 这些功能在界面标为「缺依赖」且不可选择，其余功能正常；XLS 仍可用 xlrd 读取 |
| **Tesseract OCR** | 5.5.0 | Apache-2.0 | 免费 | 扫描 PDF / 图片识别 | OCR 按钮提示未安装；扫描件无法取值 |
| tessdata_fast（chi_sim / eng / osd） | 4.x 模型 | Apache-2.0 | 免费 | 中文简体 + 英文识别模型（约 17 MB，放在 `backend/tessdata`） | 缺少时只能用系统自带语言 |
| Droid Sans Fallback（随 PyMuPDF 提供） | — | Apache-2.0 | 免费 | PDF 写入中文 | — |
| 中文字体（Linux 部署建议 `fonts-noto-cjk`） | — | SIL OFL | 免费 | LibreOffice 导出 PDF 时显示中文 | PDF 导出中文可能显示为方框；模板使用的字体未安装时上传即提示 |

## 3. 前端（`frontend/package.json`，构建后为静态文件）

| 组件 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| React / React DOM | 19.3.0 | MIT | 界面 |
| React Router | 7.18.4 | MIT | 路由 |
| TipTap（react、starter-kit、table、text-style、text-align、image、highlight、pm） | 3.31.3 | MIT | 内置文档模板编辑器（仅使用开源扩展，未使用 TipTap Pro） |
| Vite / TypeScript（仅构建） | 8.3.0 / 7.0.2 | MIT / Apache-2.0 | 构建工具 |

## 4. 开发与测试（`backend/requirements-dev.txt`）

| 组件 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| pytest / httpx | 9.1.1 / 0.28.1 | MIT / BSD-3 | 自动化测试 |
| Playwright（Python） | 1.63.0 | Apache-2.0 | 浏览器端到端测试，使用本机已安装的 Google Chrome（不下载浏览器） |

## 5. 部署要求

* Python 3.12 或更高（已在 3.14 验证），Node.js 20+（仅构建界面时需要）。
* 内存：实测 5 000 份 DOCX 的批处理全过程进程峰值约 440 MB（见 `docs/ACCEPTANCE.md`）；建议至少 2 GB，启用 LibreOffice 转换时建议 4 GB。
* 磁盘：原文件、模板版本与输出以内容哈希存放在 `DOCWEAVE_DATA_DIR`，重复内容只存一份；需按生成量预留空间。
* 不需要外部网络（安装依赖与下载 OCR 模型时除外）。
