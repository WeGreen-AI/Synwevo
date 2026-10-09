# 文件接纳链：先隔离、检查，再进入业务处理

> 状态：2026-09-25 实现（CI-20）。覆盖 Synwevo Docs 的全部文件入口；账户服务的头像走另一条已存在的
> 净化路径（重编码为 PNG，见 §6）。2026-09-28 补上扫描上线前存入文件的补扫命令与隔离区（CI-90，§8.1）。
> 2026-09-29 补上加密文件（需求 §8.7「加密文件……不能当作通过」）：设有密码的压缩包、Office 文档与 PDF 不再被当作干净（§3.1）。
> 本文写的是**当前代码的行为**，"未做"的部分明确列在 §9。

## 0. 出发点：现状有什么、没有什么（实现前）

| 已有控制 | 位置 | 缺口 |
|---|---|---|
| 上传字节上限（默认 100 MiB，`SYNWEVO_DOCS_MAX_FILE_MB`） | `api/app.py:_read_limited`、`services.add_source` | 只限制字节，不看内容 |
| 结构检查：真实格式识别、ZIP 解压比与条目数、页数/单元格/像素预算 | `engine/detect.py`、`engine/limits.py` | 拒绝炸弹与伪装类型，但不识别恶意软件 |
| LibreOffice 转换在独立进程、受限目录与超时内运行 | `engine/convert.py` | 解析器仍会打开任何通过结构检查的文件 |
| 头像重编码为固定尺寸 PNG（丢弃原始容器与元数据） | `synwevo_identity`：`canonical_avatar` | 已经是内容净化（CDR），本轮不改 |
| 下载路径有对象授权、`no-store`、`X-Content-SHA256` | `api/app.py:source_download`、`v1_sources.content` | 没有"是否已接纳"的判断 |

没有：恶意软件扫描引擎、"隔离中/已拒绝"的状态、对同一字节的二次拒绝、扫描器故障时的策略。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 任何人（网页、API/SDK、分片上传、服务器路径导入）上传的字节都会立刻被解析器打开并可下载。结构检查能拒绝炸弹，不能识别已知恶意软件；而 Docs 的用户会把来源文件转发给同事。 |
| 成熟参考 | OWASP File Upload Cheat Sheet（cheatsheetseries.owasp.org，读取 2026-09-25）："Run the file through an antivirus or a sandbox if available"、"store on a different server / not in webroot / generated filename"；ClamAV 官方文档 clamd 协议（docs.clamav.net → Manual → Usage → Scanning → clamd，读取 2026-09-25）：`INSTREAM` 分块流式扫描、`StreamMaxLength`、`PING`/`VERSION`；EICAR 标准测试文件（eicar.org，读取 2026-09-25）：68 字节 ASCII，每个合规引擎都以 `Eicar-Test-Signature` 类签名命中，用于验证检测链路而不需要真病毒。 |
| 对比与选择 | (a) ClamAV 本地守护进程：GPLv2、独立容器、无外部数据外流、签名库每小时自动更新，官方镜像 `clamav/clamav`；(b) VirusTotal 等外部平台：会把用户文档发送给第三方——**未授权，不采用**；(c) 云厂商托管扫描：项目当前只有 Cloudflare 边缘与一台 VPS，没有对象存储，不适用。选 (a)。协议自己实现（`malware.py`，约 180 行），不引入 `clamd` Python 包：需要的只有 4 个命令，而现有的包要么不维护要么带着不需要的依赖。 |
| 预期变化 | 来源文件状态多出 `scanning`→`parsing` 与两个终态 `rejected`/`scan_failed`；模板上传变成同步扫描；下载/内容接口对未接纳文件返回 409/410；`scan` 对象随文件与模板返回；`/readyz` 多一个 `malware_scanner` 组件；SDK 的等待函数把 `scanning`/`scan_failed` 视为"仍在等"。 |
| 边界与风险 | 引擎只识别**已知**恶意软件，"clean" 是"未检出已知威胁"；EICAR 只在文件（或压缩包成员）偏移 0 处是签名，嵌在 CSV 中间就是普通文本——测试样本必须按标准放置；超过 `StreamMaxLength` 的文件不扫描也不接纳（默认 25 MiB，可与上传上限一起调大）；ClamAV 常驻约 1.3–1.5 GiB 内存，生产 VPS 需要先确认余量；amd64 官方镜像在 Apple Silicon 上只能以模拟方式运行（仅影响开发机）。 |
| 验收 | §7 的九项：EICAR 被拒绝并隔离、同字节再上传直接拒绝、干净文件照常解析、引擎离线时文件停在隔离态并在恢复后自动接纳、超限文件不放行、模板 400/503/413、下载/内容接口对隔离文件 409、对拒绝文件 410、未配置扫描器时状态是 `not_configured` 而不是"干净"。 |
| 发布与恢复 | schema v7→v8 只加两列（`source_files.scan`、`templates.scan`），旧行读作 `unscanned`；应用先于扫描器上线时 `scan.status=not_configured`，行为与旧版一致；开启 `SYNWEVO_CLAMD_ADDRESS` 后新文件走扫描，历史文件按 §8 补扫。回滚：去掉环境变量即恢复旧行为，列保留。 |

## 2. 入口覆盖：所有字节汇聚到一个函数

2026-10-09 CR-073：Docs新上传先由`App.require_upload_size`检查文件上限与启用的扫描上限，再哈希、存储、排队或解析。已知超限返回413，不新增`scan_failed`行。工作台、机器capabilities/usage、分片创建/写入/完成和直接服务调用共享该规则；分片重放回执、历史已接纳文件保持原语义。扫描器实际限制比配置更低时，仍保留失败即拒绝的`too_large`结果，不能假定配置代表实际扫描成功。工作台跟踪scanning→parsing及有retry_at的恢复，永久拒绝不无限轮询。

| 入口 | 路径 | 汇聚点 |
|---|---|---|
| 工作台多文件上传 | `POST /api/projects/{pid}/sources` | `App.add_source` |
| 机器 API 单文件上传 | `POST /api/v1/projects/{pid}/files`（multipart） | `App.add_source` |
| 机器 API 服务器路径导入 | `POST /api/v1/projects/{pid}/files`（JSON `path`，仅 `SYNWEVO_DOCS_IMPORT_ROOTS`） | `App.add_source` |
| 分片/断点续传 | `POST /api/v1/uploads` … `/complete`（校验 sha256 后） | `App.add_source` |
| 模板上传 | `POST /api/templates/upload`、`POST /api/v1/templates` | `App.upload_template` → `scan_template_bytes` |
| 头像 | 账户服务 `PUT /api/auth/avatar` | `canonical_avatar` 重编码（§6） |

同一条闸门也用在 Docs 之外：Synwevo Message 的附件与账户服务的成员导入都经过 `synwevo_core.member_import.accept_upload`
（同步扫描：感染 400、加密 400 `encrypted_file`、引擎不可用 503、超限 413），组织共享模板的"使用"在没有干净记录时经过
`scan_template_bytes`。

`add_source` 在写入任何行与字节之前先查（CI-90 起先算摘要、再落 blob：被判定的字节不会被重新写回存储）：
这些字节的摘要是否已有一条 `rejected` 记录——有就 400
`malware_detected`，不再落库、不再扫描。写入时行状态为 `scanning`，`scan={"status":"pending"}`，
再把 `_scan_then_parse` 交给解析线程池。设有密码的文件同样在这里判定：先问引擎，再看容器（§3.1）。
没有配置扫描器时状态直接是 `parsing`，
`scan={"status":"not_configured"}`——**缺失是可见的，不是静默的**。

## 3. 状态机

```
upload
   │                 ┌──────────── clean ────────────┐
   └─▶ scanning ─────┼─ infected ─▶ rejected（字节已删除）  ▼
                     ├─ encrypted ─▶ error（scan.status=encrypted；终态，不解析、不提供；签名优先，见上一行）
                     └─ error / too_large ─▶ scan_failed ─┐   parsing ─▶ ready | needs_ocr | error | unsupported
                              ▲   自动重试 30s→60s→2m→5m→10m │
                              └────────────────────────────┘（共最多6次尝试；too_large不重试，修正文件后重传）
```

* 一轮最多6次尝试（首次及30/60/120/300/600秒后的5次重试）。`scan.attempts`为累计已开始次数，`cycle_attempts`为本轮次数；调用扫描器前持久化，进程中断不会重置预算。`retry_at`跨重启保留；`too_large`不因重启重试。耗尽时`retry_exhausted=true`且不再有`retry_at`，旧无限重试记录会保守停止。
* 文件编辑者可在工作台点击“重试安全检查”，或经`POST /api/v1/files/{fid}/scan/retry`携带当前`version`启动新一轮。原子递增来源版本并记录操作者/空间/累计次数审计，过期或重复请求409；不是自动HTTP重试。只恢复扫描服务故障，不恢复恶意、加密、超限或已删除文件。关闭扫描器不放行之前隔离的文件；新上传在显式off部署仍标记not_configured。
* `scanning`、`scan_failed`、`rejected` 的行不解析、不预览、不映射、不下载：`_parse_source` 对这三个状态直接返回；
  `require_accepted` 在两个下载路径上分别回 409 `not_yet_accepted`（隔离中）与 410 `rejected_malware`（已拒绝）。
* 结论绑定内容：`scan.sha256` 与行的 `blob` 相同才发布结果；`_publish_scan` 在写回前重新读行，版本或 blob 变了就丢弃结论。
* 引擎与病毒库版本写在 `scan.engine`（`ClamAV 1.4.x/<db 版本>/<db 日期>`），审计时能回答"当时用什么判的"。
* `rejected` 时 `_quarantine_blob`：同一 blob 的所有未拒绝行一起标记 `rejected`（同样的字节、同样的结论），然后
  `unlink` blob 文件——不保留恶意字节，也没有任何"仍然处理"的按钮。这是**新上传**的规则（上传者手里还有文件）；
  扫描上线前存入、事后补扫才判定的历史文件不删除，而是移入隔离区（§8.1）。
* 被拒绝的来源在所有读取路径都不可读（CI-90 补齐）：`_doc_from_row`（查看、搜索、页面图、文字层、OCR、定位、映射）
  对 `rejected`/`scanning`/`scan_failed` 分别回 410/409；来源视图对这三种状态只给 `pending`/`error`；
  脱敏跳过它们；`authz.blob_readable` 不再放行这些行的 `blob`/`parsed_blob`/`working_blob`——补扫拒绝的历史文件
  往往早已解析，转换副本与解析结果仍在存储里，只凭"原字节已移走"不够。
* 模板是同步的：`scan_template_bytes` 在 `upload_template` 的第一行运行，命中 400 `malware_detected`
  （`detail.signature`），加密 400 `encrypted_file`，引擎不可用 503 `scan_unavailable`（`detail.retry_after=30`），超限 413 `scan_limit`。
  模板不能"隔离后异步接纳"，因为上传模板的下一步就是打开它做槽位分析。
* 改解析选项（`set_source_options`，重新解析）先过 `require_accepted`：此前对 `scanning`/`scan_failed` 的行改选项会把它
  直接变成 `parsing`——解析器打开了未扫描的字节、文件变得可下载，扫描结论又因版本变化被丢弃（2026-09-29 修复，
  `test_encrypted_files.py::test_options_cannot_reparse_a_file_the_scan_has_not_accepted`）。

### 3.1 加密文件：不是干净，也不是恶意软件（需求 §8.7）

ClamAV 看不到它解不开的内容；不开 `AlertEncrypted*` 时它对加密的压缩包、Office 文档与 PDF 回答 `OK`，而结构检查也会放行
加密的 XLSX（它是完好的复合文档）。于是这些文件曾被当作"干净"接纳——最坏的是 Synwevo Message 的附件：原样分享给会话里的
其他成员。现在：

| 环节 | 行为 |
|---|---|
| 识别 | `synwevo_core.encrypted.encrypted_kind(bytes)` → `archive` / `office` / `pdf` / `None`（`inspect` 另外说明是"确认加密"还是"无法检查"）。**两条规则**（2026-09-29 安全审查后）：① **失败即拒绝**——某格式的结构已经在（ZIP 头、复合文档头、7z/RAR 签名、PDF 里的 `/Encrypt` 名字），却因解析失败、不支持的编码器或任何内部上限而得不出结论，就按"无法检查"报出该种类，与加密一样不接纳；`None` 只表示"看过了，不用口令就能打开"或"这些格式都不在"。② **每种格式都在阅读器会找的地方找**——ZIP 前面有垃圾或存根（自解压、多格式拼接）、7z/RAR 在可执行存根后面、PDF 没有文件头或偏移错位，阅读器照样打开，所以每种格式在整个文件里找，所有检查都跑。ZIP（含 OOXML、OpenDocument、以不压缩方式存在别的文件里的压缩包）：文件里每个本地头与中央目录头（按签名找、查合理性，不用 `zipfile`——它把整个中央目录读成对象，精心构造的文件能让它耗掉几 GB）的通用标志位 0（ZipCrypto 与 WinZip AES 都设）；设了这一位的头（本地或中央）就拒绝该文件：中央目录印证时按"确认加密"（`archive`）报出，只有本地头、没有中央目录印证时按"无法检查"报出——后者阅读器仍会沿本地头尝试解密。一个廉价、保守的字段检查（版本低字节 ≤ 6.3、已知压缩方法、能放进文件的合理文件名长度）只丢掉明显不是头的四个签名字节（机器码里偶然出现的，如第二轮在一个真实 DLL 里找到的），**不**做结构自洽判断（大小、附加字段、高版本字节）——阅读器可能接受不自洽的头，靠这些去"放行"一个设了加密位的头会漏掉真加密条目（第三轮审查：libarchive 会打开这类头）；OpenDocument 另看 `META-INF/manifest.xml` 的 `encryption-data`（它加密部件时不设 ZIP 标志），清单按流解压、有字节预算。OLE2 复合文档：按 MS-CFB 解析头、经 DIFAT 的 FAT（按需读取，不整表展开）与目录链（不做全文字节搜索——旧版 `.doc` 以 UTF-16 存正文，正文里出现这些词不算），名字**不分大小写**比较（MS-CFB 的规定，阅读器也找得到 `encryptedpackage`），只看根存储的子项：`EncryptedPackage`/`EncryptionInfo`（加密或权限管理的 OOXML）、Excel `Workbook`/`Book` 流首个 `BOF` 之后（可隔一个 `WriteProtect`）的 `FilePass`——**用 Excel 的默认口令 "VelvetSweatshop" 能打开的除外**（只保护工作簿结构的 XLS 就是这样写的，不输入口令就能打开；XOR 混淆、RC4、RC4 CryptoAPI 三种都按 MS-OFFCRYPTO 验证）、Word FIB 的 `fEncrypted` 位、PowerPoint 的 `EncryptedSummary` 流或 `Current User` 头标记 `0xF3D1C4DF`。PDF（在文件的任何位置——阅读器会修复缺失的文件头与错位的偏移）：只看含 `/Encrypt` 名字（含 `#xx` 转义写法）**且有 PDF 结构**的文件——文件头在阅读器找它的位置（前 1 KiB），或者（给会修复无头文件的阅读器）有间接对象（`N G obj` … `endobj`）和交叉引用关键字；只是提到 `/Encrypt` 的笔记、CSV、源代码、PDF 库的 jar 不是 PDF（第二轮审查）；由阅读器会用的尾部字典决定——`startxref` 链（沿 `/Prev`），链损坏时是末尾附近的 `trailer` 字典、交叉引用流字典与包住每个 `/Encrypt` 的字典；词法分析只受记号预算约束（不再有 256 KiB 的窗口），名字在、却没有一个读得出的尾部字典带着它：有文件头时按"无法检查"拒绝；没有文件头时 `None`——只有修复型阅读器会打开它，而没有尾部字典带着加密字典时它们不会要口令，读到的是明文（嵌着 PDF 片段的程序、只是引用 PDF 语法的文本落在这里）。但只要有尾部字典带着一个无法求值的加密字典（缺 `/O`、`/U`，未知修订号，引用不存在的对象），就按"无法检查"拒绝，**有没有文件头都一样**（第三轮审查：MuPDF 能用口令打开一部分无头、加密字典无法求值的文件，所以失败即拒绝；代价是嵌着这类字典的程序库，例如 Ghostscript 的动态库，也被拒）。标准安全处理器**用空的用户口令打不开**时是 `pdf`：修订 2–4 按 ISO 32000-1 算法 2/4/5（MD5 + 自带 RC4），修订 5/6 按 SHA-256 与 ISO 32000-2 算法 2.B（`cryptography` 的 AES-128-CBC）；只设权限（所有者）口令的 PDF 不输入口令就能打开，**照常接纳**；公钥等其他处理器、无法求值的加密字典 → `pdf`。7z（文件里每个签名，例如自解压存根之后）：头里任一 folder 的编码器含 AES（`06 F1 07 01`）；压缩过的头用一个 Copy、Deflate、LZMA 或 LZMA2 编码器时解包（字典大小按输出长度截断，文件要 4 GiB 也不给），其他编码器或多编码器链 → 无法检查。RAR 4/5（每个签名）：主头口令位、文件头口令位 / 归档加密头、文件或服务头的加密记录。**时间与内存都有界**（与输入成线性、各有固定上限）：每种格式的头数、7z 的编码器与 folder 总数（所有签名共用一个预算）、RAR 块数、复合文档目录项、PDF 的解析记号数与对象查找次数、候选尾部字典数、清单与 7z 头的解压字节；超出上限 = 无法检查。只在扫描上限以内运行（`ClamdScanner.scan` 先把更大的文件判为 `too_large`），从不抛异常。 |
| 结论 | `ClamdScanner.scan`（调用方都在线程池里跑它，不占事件循环）：先问引擎（引擎认得的真实签名优先——clamd 默认 `HeuristicScanPrecedence no` 时签名先于启发式报出），回答 `OK` 后若识别器报出种类 → `ScanResult("encrypted", reason=种类, checked=…)`：`checked=false` 表示"结构无法检查"，`detail` 记下是什么让检查停下（例如 "packed header layout not evaluated"），都写进扫描记录与接纳日志（`scan.encrypted … checked=False detail=…`），接口的 `scan.checked` 也给出；引擎回答 `Heuristics.Encrypted.*`（`AlertEncryptedArchive`/`AlertEncryptedDoc`）→ 同样是 `encrypted`（带 `signature`），**永远不是 `infected`**。只有开启扫描（策略 `required`）才有这一步；未配置扫描器时一切照旧（`not_configured` 照样可见）。 |
| Docs 来源 | 与恶意软件扫描同一条异步路径（网页多文件上传、机器接口、可续传上传的使用、服务器路径导入都一样）：`_scan_then_parse` 先问引擎、再看容器——同一份字节上的签名优先，按恶意软件拒绝并隔离（与历史行共用的字节按 CI-90 移入隔离区）；得到 `encrypted` 时行变为 `error`（终态，同一句错误），`scan.status=encrypted`；`_parse_source` 不解析，`require_accepted`（下载、视图、搜索、页面图、定位、脱敏……）回 410 `encrypted_file`，`authz.blob_readable` 不放行它的 blob id，改解析选项 410。字节留在行指向的位置、任何路径都读不到（与超限文件相同）；回收无人引用的字节是 blob 回收的职责，不由闸门临时决定（`docs/platform/blob-reclamation.md`）。第一版曾在落库前同步识别，审查指出它排在引擎之前（签名命中的加密文件只被当作"加密"、不隔离），并且在事件循环里运行，已改回这条路。 |
| 其他入口 | 模板上传与组织共享模板的"使用"（`scan_template_bytes`）、消息附件与成员导入（`accept_upload`）：400 `encrypted_file`，不建模板/附件/导入。多数情况什么字节都不存；**可续传上传例外**：`/complete` 校验通过时字节已经进了 blob 存储，拒绝发生在"使用"这一步——那份字节只被未消费的上传行引用，没有任何读取路径能取到它，随上传过期由 blob 回收处理。网页模板上传、机器接口的来源与模板上传、成员导入都在线程池里运行检查（此前在事件循环里，一个慢请求会卡住整个单进程服务）。 |
| 不是恶意软件 | 不进隔离区、不留"已拒绝的摘要"、不发 `docs.file_rejected`：同样的字节再上传会再次按加密拒绝，去掉密码后的文件照常接纳。 |
| 提示 | 一句话说清发生了什么、该怎么做：加密是「文件设有密码或含有加密内容，安全检查无法查看其中的内容，因此未被接纳；请移除密码（或另存一份不加密的副本）后重新上传」（代码 `encrypted_file`）；结构无法检查是「文件的结构无法安全地完成检查（可能已损坏、被截断或结构异常），因此未被接纳；如果它没有设密码，请用生成它的程序重新保存（或拆分成较小的文件）后重新上传」（代码 `unchecked_file`，`detail.checked=false`）——第二轮审查指出，读者多半根本没有密码可去。三个外壳的拒绝目录都有三语言译文（`refusals.test.ts`/`.mjs` 校验），来源行的错误句由 `diagnosticText` 翻译；组织审批页的复制清单把 `encrypted` 显示为「设有密码，无法检查」。 |
| 补扫 | 见 §8.1：扫描上线前存入的加密文件只报告、不改访问（由所有者决定）；`quarantine release` 遇到加密内容不恢复。 |

判定依据（2026-09-29 本机实测，§7.4）：ClamAV 1.5.4 开 `AlertEncrypted yes` 时，**只设所有者口令、不需要口令就能打开的 PDF
（MuPDF 写的修订 6）也被报为 `Heuristics.Encrypted.PDF`**，而设了打开密码的 ODT 仍回 `OK`——所以不依赖引擎的文档启发式：
生产只开 `AlertEncryptedArchive`（§8.2），文档与 PDF 由应用自己判断。误开 `AlertEncrypted`/`AlertEncryptedDoc` 时，引擎报出的
`Heuristics.Encrypted.*` 一律按"加密、不接纳"处理（宁可拒绝，不猜），这样的 PDF 会被拒绝——所以不要开。

## 4. 引擎适配：`apps/docs/synwevo_docs/malware.py`

* 每个命令一条连接；`zINSTREAM\0` + 4 字节大端长度前缀的 64 KiB 分块 + 零长度块；回复 `stream: OK` /
  `stream: <签名> FOUND` / `... ERROR`。`INSTREAM size limit exceeded` 映射为 `too_large`，其它 ERROR 与
  连接失败/超时抛 `ScanUnavailable`——**没有"默认干净"的分支**。
* 超过 `SYNWEVO_CLAMD_MAX_BYTES` 的数据根本不发送（守护进程会在中途拒绝，白白传一遍）。
* `OK` 之后看容器是否加密（§3.1）：加密的是 `encrypted`，不是 `clean`；`Heuristics.Encrypted.*` 是 `encrypted`，不是 `infected`。
* `policy()` 拒绝矛盾配置：`SYNWEVO_MALWARE_SCAN=required` 却没有地址 → 启动失败，而不是静默变成不扫描。
* `health()` 供 `/readyz`：`ok` / `off` / `down: <原因>`。扫描器不可达**不会**让 Docs 变为未就绪（compose 的
  healthcheck 会因此重启容器，而重启对已接纳的文件毫无帮助）；它是"可降级组件"，在 `checks` 里可见。

## 5. 隔离的解析与转换

接纳只是第一道门。接纳之后的解析仍然按既有隔离运行：LibreOffice 独立进程、受限工作目录、超时与内存预算；
XLSX/DOCX 只读结构，不执行宏或 DDE（openpyxl/python-docx 不实现它们，模板填充也不复制 `vbaProject.bin`）；
PDF 由 pypdfium2 渲染，不执行 JavaScript；图片在解码前先检查像素预算。这些在 `engine/limits.py` 与
`docs/security/permissions.md` §7 已有记录，本轮没有改动。

## 6. 头像与 SVG/HTML 预览

头像在账户服务里被解码并重编码为固定尺寸 PNG（`canonical_avatar`）：原始容器、元数据与任何脚本都不会到达浏览器。
Docs 的预览只输出服务端渲染的 PNG（页面图）与文本，不回放上传的 SVG/HTML；因此本轮不给头像加病毒扫描——
重编码本身就是比签名扫描更强的净化，而扫描一张 256×256 的 PNG 不会带来新的保护。

## 7. 验收证据

### 7.1 协议替身（`apps/docs/tests/test_malware_scan.py`，8 项，本机通过）

替身是一个真正监听 TCP 的 clamd 协议实现：按 EICAR 标记判定、可被"关掉"、可设流上限。它证明的是集成——
分块框架、结论处理、隔离、重试、下载门——不是引擎本身。

| 项 | 断言 |
|---|---|
| 适配器 | 多块流式、`OK`/`FOUND`/`size limit` 三种回复、超限不发送、连接失败抛 `ScanUnavailable` |
| 策略 | 无地址→off；有地址→required；`required` 无地址→拒绝启动 |
| 干净来源 | `scanning`→`ready`，`scan.status=clean`，`scan.sha256==sha256`，可下载 |
| EICAR 来源 | `rejected`、签名 `Win.Test.EICAR_HDB-1`、下载 410、blob 已删除、同字节再上传 400 |
| 引擎离线 | `scan_failed`（attempts=1）、下载 409；引擎恢复后自动重试到 `ready`（attempts≥2） |
| 超限 | `scan_failed` + `too_large`，下载 409 |
| 模板 | 干净 200 带 `scan.clean`；EICAR 400 `malware_detected`；引擎离线 503 `scan_unavailable` |
| 未配置 | 来源 `parsing`+`not_configured`；模板 `not_configured` |

### 7.2 真实引擎（本机 Docker，`clamav/clamav` 官方镜像）

`docs/quality/verification.md` §16.20：ClamAV 1.5.4（签名库 main 63 / daily 28134，3 628 083 条）——EICAR 裸文件与
ZIP 内的 EICAR 命中 `Eicar-Test-Signature`，三种仓库夹具 `OK`，守护进程的 `INSTREAM size limit exceeded` 被解析为
`too_large`；HTTP 链上干净文件 `scanning`→`ready`、EICAR `rejected`（下载 410、blob 已删、再上传 400）、模板 200/400，
`/readyz` 的 `malware_scanner: ok`。常驻内存 950 MiB。EICAR 命中只证明检测链路可用，不证明能拦截所有恶意文件。

### 7.3 补扫（CI-90，协议替身，本机通过）

`apps/docs/tests/test_rescan.py`（35 项）、`apps/messages/tests/test_attachment_rescan.py`（8 项）、
`tests/integration/test_rescan_shared_bytes.py`（3 项）。每项先在没有扫描器的实例上存入文件（`not_configured`，可读），
再开扫描、在进程内运行补扫命令（各有一项经真实入口 `python -m …cli`）。

| 项 | 断言 |
|---|---|
| 干净 | 只换扫描记录（`clean`、`rescan`、`previous`），状态、`updated_at`、版本不变；每份内容只扫一次；第二次运行无事可做 |
| 感染的来源 | 共用字节的两行都 `rejected`；字节移入隔离区（摘要一致）且不在存储；索引记录行 id 与原状态、无文件名；下载 410、视图 `error`、搜索 410、解析结果的 blob id 404、脱敏拒绝、不再解析；上传者收到 `docs.file_rejected`、机器上传无人可通知；同字节再上传 400 且不写回存储；`blob_audit` 不把它算缺失 |
| 感染的模板 | 详情/视图/下载/复制/预览 410；生成 410；已建任务在下一片停下；干净版本的 blob 也不可经 id 读取；项目仍能打开并带 `template_rejected`；列表显示结论；解除绑定后可删除 |
| 引擎离线 | 开始时不可达或未开启 → 退出码 3，什么都没改；预览也报 3 |
| 超限 / 出错 | `too_large` 记录上限、不发送、同一上限不重写、调大后变干净；`error` 记录次数、下次重试、恢复后干净；行一直可读 |
| 摘要不符 | 报告、退出码 1、什么都不改 |
| 预览 / 分批 / 工作空间 | 只统计、不扫描不写入；`--limit` 分批且下一次续上；未知工作空间退出码 2 |
| 复核 | 仍判感染时拒绝恢复；签名撤回后恢复为原状态并可下载、索引 `released`；`purge` 需要 `--yes`，之后行保持拒绝 |
| 回归用例副本 | 整个用例移入隔离区（列表里消失），恢复后回到原处 |
| 附件 | 干净只换记录；感染的原件与转发副本都 `rejected`；成员下载与内联 410、非成员 404、转发 410、消息里标 `rejected`；恢复后可下载 |
| 两个应用共用字节 | Docs 补扫后附件在标记前就是 410；消息补扫套用已有结论；两边各自恢复，最后一个恢复后隔离副本删除 |
| 独立审查后的补充（16 条发现逐条回归） | 用后来被拒的模板或来源生成的产物、打包下载、机器接口下载链接一律 410，由它们填出的预览不再可读；另一应用先判定、行还没标记时下载与搜索 410，字节被放回存储（恢复备份）后仍 410，同字节再建来源/模板 400（未开扫描也一样）；已完成的断点上传不能用作来源；`purge` 只删隔离副本，移在一旁的回归用例（含其他文件）原样保留；恢复时重扫模板的其他内容，另一份出错就记 `error` 不记 `clean`；两个结论下的模板恢复其一后仍拒绝，两者都恢复才可用；应用自己在途的扫描写不回已被补扫拒绝的行；超限文件排最后，`--limit` 不被卡住；`blob_audit` 只豁免本身被拒的引用、隔离副本丢失算缺失；备份带上隔离区并能恢复；组织共享模板的发布（源模板已删除也算）被补扫，感染的不能"使用"，没有干净记录的发布在"使用"时扫描；消息附件同样由结论而非字节缺失把关；恢复与补扫互斥、只标记真正恢复的行；没有行担保的回归用例文件从用例自身补扫 |
| 第二次独立审查后的补充 | 两个应用的补扫、恢复、清除互斥（一把锁）；已记为干净的行在另一应用的结论下被选中并标记，项目的其他文件照常工作；脱敏副本只随自己的来源撤回；`--limit` 数扫描次数，缺失文件不再卡住分批；回归用例文件的结论按摘要记下、不重扫；结论集合的缓存跟随索引计数器（同一时间戳下的写入也能看到）；产物拒绝只读快照里的 id 与摘要；上传闸门遇到与历史附件共用的字节时移入隔离区而不删除 |
| 第三次独立审查后的补充 | 扫描上线后重新上传同一文件被判恶意时，之前那条来源的字节移入隔离区（按原状态记录，恢复后回到 `ready`）而不是随新上传一起删除，它的上传者收到 `docs.file_rejected`；`--limit` 把出错与超时的扫描也算进去；模板被两个应用先后判定时，恢复第一份会把它登记到第二份结论下，第二份恢复后模板回来 |

### 7.4 加密文件（需求 §8.7，2026-09-29）

**安全审查（2026-09-29，c2242fc）与修复。** 独立审查确认了路由（每个入口都经过闸门、被拒的行在每条读取路径上都被拒、补扫与恢复、三语言句子），并用构造的文件找到两个必须修的问题与四个应修的问题，逐项修复，审查的生成器都变成了回归测试（修复前在 c2242fc 上全部失败）：

| 发现 | 修复 |
|---|---|
| 高：识别器把"解析失败/超出内部上限"当作"干净"，并且只按开头的魔数选格式——前缀垃圾的 ZIP、附加在 PDF 后的 ZIP、两个拼接的压缩包、trailer 超过 256 KiB 的 PDF、文件头推后或缺失的 PDF、超过 1 MiB 的 OpenDocument 清单、用 Copy/Deflate/Delta 打包头的 7z、改了大小写的复合文档流名：8 个样本经消息附件闸门被接纳并被另一成员原样下载，Docs 里 6 个可下载 | 失败即拒绝（`inspect` 报"无法检查"）、每种格式在整个文件里找、PDF 词法不再有窗口、清单按流解压、7z 头支持 Copy/Deflate/LZMA/LZMA2 其余拒绝、复合文档名不分大小写 |
| 高：一个 2.6 KB 的 7z（头解压后是 16 MiB 的编码器记录）让识别器耗时 2.1 s、约 350 MB，一次上传 10 个把事件循环占住 38 s；100 MiB 的 FAT 重复/巨型中央目录/满是 trailer 的输入各 1–4 s、约 1 GB | 所有计数与字节都有总上限（超出 = 无法检查）、FAT 按需读、不用 `zipfile`、PDF 只保留最后几个候选且对象查找有次数与字节预算；只在扫描上限内运行；所有调用方把检查放进线程池。每类对抗输入都有时间与内存预算的测试 |
| 中：只保护工作簿结构的 XLS 以默认口令写成 FilePass，却被当作加密拒绝 | 默认口令 "VelvetSweatshop" 能打开就不算加密（三种方案） |
| 低：Docs 来源在引擎之前识别，签名命中的加密文件只按"加密"拒绝、不隔离 | 来源回到异步路径：先引擎、后识别 |
| 低：7z 头的 LZMA 字典大小取自文件（最大 4 GiB） | 截断到输出长度 |
| 文档：可续传上传在拒绝前字节已入库，原文写"不存字节" | 如实写明（§3.1） |

**第二轮审查（7c94434）。** 六项修复全部成立；剩下的四项都已修复并有回归测试（在 7c94434 上全部失败）：

| 发现 | 修复 |
|---|---|
| 中（阻止合并）：只要文件里出现 `/Encrypt` 就按 PDF 检查——提到它的 .md/.csv/.py 附件被拒、Docs 里单元格提到它的 CSV 变成 `error`、PDFBox 的 jar 与 8 个 Ghostscript 动态库被拒 | 只有 PDF 结构（前 1 KiB 的文件头，或间接对象 + 交叉引用关键字）才检查；无文件头时，没有尾部字典带着加密字典、或加密字典无法求值 → 放行 |
| 低–中：真实 DLL 的机器码里出现本地头签名、版本 0x850F、方法 0、加密位，被当作已确认的加密压缩包 | 加密位要由另一种记录的同名条目印证，或头部严格自洽 |
| 低：头数上限 40 万数的是本地头加中央目录头，约 20 万个条目的普通 ZIP（18 MiB、21 万条目）被拒 | 上限提到 70 万个头（约 35 万个条目，超过 25 MiB 所能容纳），文档改为按条目说 |
| 低（体验/运维）："无法检查"用的是密码那一句，也不记录 | 扫描记录与日志记 `checked=false` 与原因；独立的三语言句子与代码 `unchecked_file`，三个外壳都已编目 |

第二轮之后，审查的两份真实文件清单（25 299 个文本/Office/ZIP/PDF、5 003 个二进制）逐个检查：**0 个被拒**，单个文件最慢 0.14 s。

**第三轮审查（3030bcd）。** 审查指出第二轮为减少误报放开的两条"接纳"规则，实际会放过真实阅读器能用口令打开的文件，于是按牵头决定**全部改回失败即拒绝**，接受随之而来的误报代价：

| 发现 | 决定 |
|---|---|
| 无头 PDF（前 1 KiB 无 `%PDF-`）而加密字典无法求值（缺 `/O` 或 `/U`、未知修订号、悬空引用）曾被放行；MuPDF 能用口令打开其中一部分 | 只要有尾部字典带着无法求值的加密字典，**有没有文件头都按"无法检查"拒绝**（保留第二轮的前置门：只有 PDF 结构才检查，所以只提到 `/Encrypt` 的笔记/CSV/代码仍放行） |
| 只有本地头、既无中央目录印证也不"自洽"的加密条目曾被放行；libarchive（`bsdtar`，含从标准输入流式读取）能用口令打开其中一部分 | 任何带加密标志的本地或中央头都拒绝（中央目录 → `encrypted`，只有本地头 → `unchecked_file`）；只保留一个不依赖阅读器宽容度的廉价垃圾过滤（版本低字节、已知方法、合理文件名长度），拿不准就拒绝 |

代价（有意接受，安全优先）：嵌着无法求值加密字典的二进制（如 Ghostscript 动态库）、机器码里带加密标志的 ZIP 式头（如某剖析器 DLL）、内嵌完整 PDF 片段并带无法求值加密字典的文本，现在都以"结构无法检查"那一句被拒；作为 Docs 来源或消息附件都很少见。保留：提高后的条目上限、`unchecked_file` 代码/句子与 `checked:false`+`detail` 记录、先引擎后识别的次序、全部时间/内存上限与线程池执行。第二轮的真实文本误报（`.md`/`.csv`/`.py`、CSV 来源）与 21 万条目的 ZIP 仍接纳。

依据：本机（2026-09-29）用真实阅读器检查这两条规则放行的代表样本时，MuPDF 与 libarchive 各有样本能用口令打开，所以两条规则都不能保留；检查到此为止，没有继续摸索阅读器的宽容度，也没有把阅读器做成测试（CI 里没有阅读器测试）。回归测试只用测试里构造的简单夹具，断言识别器的结论：`pdf_headerless_missing_o`（无头、加密字典缺 `/O` → 无法检查）、`zip_local_only_encrypted`（带加密标志、没有中央目录 → 无法检查）、`pdf_fragment_in_text` 与 `machine_code_with_zip_signature`（代价：被拒）、第二轮的文本误报与 21 万条目 ZIP（接纳）。

修复后的时间与内存（本机，每类一个进程测 `ru_maxrss` 增量）：7z 头炸弹 0.03 s / 48 MiB（16 MiB 头上限）、4 GiB 字典 0.01 s、25 MiB FAT 重复 0.02 s / 1 MiB、25 MiB 巨型中央目录 0.13 s（上限处拒绝）、25 MiB 的 trailer 关键字 0.02 s（带 `/Encrypt` 时 0.25 s）、缺失引用 0.44 s、RAR 块 0.03 s、清单炸弹 0.01 s；应用与系统自带的 13 766 个真实文件（PDF、ZIP、JAR、Office、字体、镜像等）没有一个被误判，最慢 37 ms。审查的阴性样本（只有所有者口令的 PDF、受保护的 XLSX/ODS、只保护结构的 XLS、EPUB、JAR、不压缩的 ZIP、正文提到这些标记的文档）全部接纳。

测试：`apps/docs/tests/test_encrypted_files.py`（46 项：识别器 31 项——含 13 类对抗输入的时间与内存预算、PDF 按 4 种加密方法参数化；扫描器 3 项，其一是
真实引擎，设置 `CLAMD_TEST_ADDRESS` 时运行、否则跳过；Docs 上传路径 9 项，含事件循环测试；补扫与恢复 3 项）、`apps/messages/tests/test_encrypted_attachments.py`（4 项）、
`tests/integration/test_encrypted_member_import.py`（2 项）；夹具由 `apps/docs/tests/encrypted_fixtures.py` 按格式说明用纯 Python
构造（CFB 经 `olefile` 读回，7z/RAR 经 libarchive 3.7.4 `bsdtar -tvf` 列出并报告加密）。

| 项 | 断言 |
|---|---|
| 识别器 | ZIP 标志位：中央目录带标志 → 确认加密（`archive`），只有本地头带标志 → 无法检查（中央目录损坏仍从本地头读出），机器码里带加密标志的 ZIP 式头也按无法检查拒绝，不含标志的 21 万条目 ZIP 接纳；OOXML/OpenDocument；复合文档的 `EncryptedPackage`、XLS `FilePass`（含 `WriteProtect`、迷你流与常规扇区两条路径）、DOC `fEncrypted`、PPT 头标记与 `EncryptedSummary`；正文里写着这些名字的 DOC、嵌入对象里的加密包、普通 XLS/PPT 为 `None`；MuPDF 的修订 2/3/4/6 PDF：有用户口令为 `pdf`、只有所有者口令为 `None`，对象流/交叉引用流、公钥处理器、`startxref` 损坏；加密字典无法求值（缺 `/O`/`/U`、未知修订号、悬空引用）**无论有无文件头**都按无法检查拒绝，只提到 `/Encrypt` 但没有 PDF 结构的笔记/CSV/代码、以及没有尾部字典带着加密字典的无头程序片段接纳；7z（LZMA/Copy/Deflate 头、明文头、加密头）与 RAR 4/5（文件与头加密）；审查的构造样本：前缀与附加的 ZIP、拼接的压缩包、存根后的 7z/RAR、推后或缺失文件头的 PDF、超长 trailer（空白与注释，经典与交叉引用流）、转义写法的 `/Encrypt`、超过 1 MiB 的清单、大小写不同的流名、LibreOffice 真实的只保护结构 XLS（默认口令）与 XOR/RC4/CryptoAPI 三种 FilePass；读不完的结构 → 无法检查；阴性集（EPUB、JAR、只有所有者口令的 PDF…）；13 类对抗输入各在 3 s、96 MiB 之内（本机实测都在 0.5 s、48 MiB 以内）；截断与随机改写 500 余种输入不抛异常 |
| 扫描器 | 引擎先被询问；`OK` + 加密 → `encrypted`；`Heuristics.Encrypted.*` → `encrypted`（带签名、种类）；加密字节上的真实签名 → `infected`；只有所有者口令的 PDF → `clean` |
| Docs | 来源（网页、机器接口、可续传上传、服务器路径导入）：引擎先看（每个文件都送到了替身），随后 `error` + `scan.status=encrypted`、下载 410、无隔离结论；ZIP 里同时有 EICAR 与加密条目时按恶意软件拒绝（`rejected`、字节移走、再上传 400 `malware_detected`）；模板：400 `encrypted_file`、三语言句子、`detail.kind`/`filename`，不建模板；只有所有者口令的 PDF、只保护结构的 XLS 照常接纳；检查慢一秒时，并发的其他请求不被卡住（网页与机器接口的模板上传）；引擎独有的结论：`error` + `scan.status=encrypted`，下载/搜索/选项 410、视图 `error`、blob id 不可读、未解析、无隔离结论，同字节再上传再判一次，脱敏跳过它；改解析选项不能绕过闸门（`scan_failed` 的行 409）；未配置扫描器时一切照旧 |
| 补扫 | 历史加密来源（XLS、PDF）：退出码 1、摘要列 id 不列文件名，状态与错误句不变，记录 `encrypted`/`rescan`/`access: unchanged`，下载仍 200，第二次运行无事可做；签名命中的加密字节照常隔离，签名撤回后 `release` 仍拒绝（退出码 1） |
| 消息与成员导入 | 加密 ZIP/7z/RAR/DOCX/XLS 附件与引擎报出的嵌套加密：400、什么都不存；审查的 8 类构造附件（前缀 ZIP、附加在 PDF 后的 ZIP、超长 trailer、文件头推后的 PDF、超大清单、Copy 头的 7z、大小写不同的 XLS/DOC 流名）全部 400，只保护结构的 XLS、只有所有者口令的 PDF、带超大清单的普通 ODT 照常接纳；补扫发现的历史加密附件只记录、成员仍可下载；成员导入（文档引擎）的加密 XLSX/XLS/PDF：400、不建导入、不留文件，只有所有者口令的 PDF、只保护结构的 XLS 照常导入；检查慢一秒时并发请求不被卡住 |
| 前端 | 三个外壳的拒绝目录测试读取服务端句子（消息外壳自此也读附件闸门 `accept_upload` 的拒绝，补上了此前只显示代码的恶意内容、超限与引擎不可用三句） |

浏览器（2026-09-29，隔离的临时 Docs 实例接真实 clamd，系统 Chrome 无头，只用测试夹具）：在工作台的来源文件面板上传加密的
XLSX，简体中文、English、繁體中文三种界面都在文件名下方与提示条里显示整句（不出现 `encrypted_file` 代码），文件列表仍为空。

真实引擎（本机 Homebrew ClamAV 1.5.4，病毒库 28137；`scratchpad` 下两个守护进程，只用测试夹具）：

| 样本 | `AlertEncrypted yes` 的 clamd | `AlertEncryptedArchive yes` 的 clamd | 结论（两者相同） |
|---|---|---|---|
| `zip -P`、WinZip AES、只设标志位的 ZIP | `Heuristics.Encrypted.Zip` | 同左 | `encrypted`/`archive` |
| 普通 ZIP 里套一个加密 ZIP | `Heuristics.Encrypted.Zip` | 同左 | `encrypted`/`archive`（识别器本身不看嵌套，靠引擎） |
| 同一 ZIP 里一个 EICAR 条目 + 一个加密条目 | `Eicar-Test-Signature` | 同左 | `infected`（签名优先于启发式） |
| RAR5（合成） | `Heuristics.Encrypted.RAR` | 同左 | `encrypted`/`archive` |
| 7z（合成，LZMA 头） | `OK` | `OK` | `encrypted`/`archive`（识别器） |
| LibreOffice 加密 DOCX、XLS | `Heuristics.Encrypted.OLE2` | `OK` | `encrypted`/`office` |
| LibreOffice 加密 ODT | `OK` | `OK` | `encrypted`/`office`（识别器） |
| 只有所有者口令的 PDF：MuPDF 修订 6 / LibreOffice 修订 3 | `Heuristics.Encrypted.PDF` / `OK` | `OK` / `OK` | 前者 `encrypted`、后者 `clean` / 两者 `clean` |
| 有打开口令的 PDF（MuPDF 修订 4） | `Heuristics.Encrypted.PDF` | `OK` | `encrypted`/`pdf` |
| 普通 ZIP、普通 XLSX | `OK` | `OK` | `clean` |

## 8. 历史文件与运维

### 8.1 补扫扫描上线前存入的文件（CI-90）

生产 2026-09-28 开启扫描（CI-87）。此前存入的来源文件、模板与消息附件的 `scan.status` 是 `not_configured`
（更早的行没有记录，读作 `unscanned`），照常可读。补扫是两条命令，各自只动自己应用的行：

```bash
# Synwevo Docs：来源文件 + 每个模板的全部已存内容（工作文件、图片模板的原图、每个版本的工作文件）
python -m synwevo_docs.cli rescan [--apply] [--limit N] [--workspace <工作空间 ID>] [--only sources|templates|cases]
python -m synwevo_docs.cli quarantine list | release <sha256> | purge <sha256> --yes
# Synwevo Message：消息附件（逻辑在 synwevo_core.messaging_rescan，附件表属于 synwevo_core.messaging）
python -m synwevo_messages.cli rescan [--apply] [--limit N]
python -m synwevo_messages.cli quarantine list | release <sha256> | purge <sha256> --yes
```

**不加 `--apply` 只预览**（统计将要检查的内容、报告扫描器是否可用，不扫描不写入）；`--apply` 才扫描并写入——
结论会拒绝行、移走字节、通知上传者，不应该误触发（与 `projects` 清理默认 dry-run 同一规则）。
生产上在各自容器里运行（扫描配置取容器环境）：见 `docs/operations.md` §8.1。

**选哪些行。** 在用的行（来源：未移除、项目未删除；模板：未删除；组织共享模板的每个发布——当前发布、图片模板的原图与
每一次历史发布的工作文件，源模板删除后仍在；附件：`status=accepted`），且扫描记录不是
`clean`：没有记录、`unscanned`、`not_configured`，以及上一次补扫留下的 `error`/`too_large`；另外，**不论记录写什么**，
字节上有成立的隔离结论的行（另一个应用的补扫或上传闸门判定了同一份字节）也被选中，不扫描、直接套用结论。
`scanning`/`scan_failed`（应用自己在重试）和已经 `rejected` 的行不选来扫描。所有类别排在同一个顺序里：已有结论的最先，
然后是没试过的、之前出错的（按上次尝试时间）、超过扫描上限的（只报告）。**`--limit N` 数的是扫描次数**：已有结论、
内容缺失或不符、同一上限下的超限都不需要扫描，不计入，所以分批总能往前走。
回归用例（`regression/`，工作空间可下载的逐字节副本）里没有任何"干净记录绑定同一摘要"的行担保的文件——例如来源已移除——
从用例自身的副本扫描；用例没有自己的行，结论按摘要记在 `meta`（`rescan-case:<sha256>`），干净的不再重扫。同一摘要的多行只扫一次，`--limit` 数的是"份内容"
（一个模板算一份）。`--workspace` 只筛选要检查的行；感染时同一字节的其他行（包括其他工作空间的）仍一起拒绝。

**结论绑定内容。** 读出的字节必须哈希到行记录的摘要（blob 名）；不符或内容不在存储中 → 报告，什么都不改
（退出码 1）。

| 结论 | 行的状态 | 字节 | 通知 |
|---|---|---|---|
| clean | 不变；只把扫描记录换成 `{status: clean, sha256, engine, checked_at, rescan: true, previous}`，`updated_at` 不动 | 不动 | — |
| infected | 同一字节的**所有**行：来源 `rejected`（与上传闸门同一句错误，`scan.status=infected`）；模板 `scan.status=infected`；附件 `rejected`。一切读取路径拒绝（下文） | **移入隔离区，不删除** | 来源的上传者收到 `docs.file_rejected`（站内 + 按目录发邮件）；模板没有记录上传者；附件没有合适的通知类别（`docs.file_rejected` 写的是 Docs 项目），**不另造**，摘要里写明 |
| error | 不变；记录 `status: error`、原因、时间、`attempts`，下一次运行重试 | 不动 | — |
| too_large | 不变；记录 `status: too_large` 与当时的上限；上限不变时再次运行只报告不重写，调大 `SYNWEVO_CLAMD_MAX_BYTES` 后重新检查 | 不动 | — |
| encrypted | **不变**：设有密码或含有加密内容，无法检查，但不是恶意软件。只记 `{status: encrypted, reason, rescan: true, access: unchanged, previous}`（上传闸门的拒绝只认没有 `rescan` 的记录，所以这些行照常可读）；摘要列出 id，退出码 1；之后的运行不再选中 | 不动 | 无；**撤回已在使用的文件是所有者的决定**——是否撤回、通知上传者或要求去掉密码后重新上传，由所有者看过摘要后决定，本命令不替他做。同样的字节再上传会被上传闸门拒绝；组织共享模板的发布被记为 `encrypted` 后，"使用"会当场扫描并拒绝 |

模板与发布都是一个整体：所有内容都干净才记 `clean`；任一份感染就拒绝（发布的结论记在 `info.scan`）。写入前在事务里重读（`_publish_scan` 的规则）：
字节、接纳状态、扫描记录或模板的内容集合在扫描期间变了，就不写入（报告"期间被改动"，下次再看）。
感染的判定不受此限——字节就是那份字节。

**被拒绝之后哪里都读不到。** 来源：见 §3。模板：`App.get_template` 默认拒绝 `scan.status=infected`
（410 `rejected_malware`），覆盖详情、视图、页面图、下载（工作副本与原件）、字段编辑、版本操作、复制、
发布/更新到组织、项目复制、定义导出、预览与生成（`ProjectContext` 记下原因，项目仍能打开，页面提示换模板）；
只有列表、使用情况与删除仍可用。已创建的生成任务在下一个切片停下（任务快照里的模板或来源被拒绝）；
**已经生成的产物**同样撤回：`artifact`（下载、查看、页面图）、打包下载与机器接口的下载链接回 410，产物的 blob id 不再可读；
由被拒模板填出的预览与版式 PDF 的授权随之删除（授权记录了模板 id），被拒来源所在工作空间的预览与版式授权也删除
（它们不记来源，按需重算，而被拒来源不会再被读取）——预览常与同一样本的产物逐字节相同，不删就能凭 blob id 读到产物。
脱敏副本的授权自 CI-90 起记着它的来源（`desensitized:<来源 id>`），只随那个来源撤回；此前的授权不记来源，在被拒来源所在的
工作空间里一并撤回（分不出是不是被拒文件的副本）。
组织共享模板的发布：当前发布的内容被判感染（或其字节有成立的隔离结论），"使用"回 410——只有更早的某次发布感染时，
仍可从干净的当前发布"使用"；源模板被拒后不能再切新的发布，只改说明不受影响；没有干净记录的发布在开启扫描的部署里
"使用"时当场扫描，副本带着结论开始，而不是没有扫描记录。附件：成员下载、内联显示与转发回 410，非成员仍是 404；
消息里保留附件名并标 `rejected`，界面不再给链接。

**为什么隔离而不删除。** 新上传被拒时上传者手里有文件；历史文件已经可读了几周，主人未必还有副本，而误报只能由
签名发布方撤回——删了就无法挽回。所以字节从 blob 存储移到 `<数据目录>/quarantine/<sha256>`（0700 目录、0600 文件，
没有任何路由映射这个目录），旁边是索引 `<sha256>.json`：签名、引擎与病毒库版本、时间、每一行的 id 与**被拒绝前的状态**
（状态与扫描记录——不含文件名、错误句或其他用户文本），以及操作历史。回归用例里的逐字节副本整体移到
`<sha256>.d/`。先写隔离副本与索引、再移走回归用例（失败只报告，不中断）、再标记行、最后移出存储（先核对隔离副本的摘要）：
中途崩溃时字节最多在两处，不会一处都没有。

**结论是索引，不是"字节不见了"。** 索引为 `quarantined` 或 `purged` 的摘要在任何地方都被拒绝——来源与模板的读取、
blob id、产物、附件下载、新上传、完成的断点上传、组织共享模板的"使用"——不管存储里此刻是否又有一份（恢复了旧备份、
标记与移出之间崩溃）。每次 `rescan --apply` 与 `purge` 先做 `settle`：把有成立结论却还在存储里的副本移走
（隔离副本缺失或损坏时用它补上）。

**复核之后：恢复或清除。**

1. `quarantine list` 看隔离了什么（摘要、签名、引擎、每类仍被拒绝的行数）。
2. 误报：按 ClamAV 官方流程提交（`https://www.clamav.net/reports/fp`），等签名库更新到撤回该签名的版本。
3. `quarantine release <sha256>`：用**当前**引擎重扫隔离区里的字节——仍判感染、判为加密（不能判定为干净）或扫描器不可用就拒绝恢复
   （退出码 1/3，什么都不改）；判定干净则把字节放回 blob 存储，把本应用的行恢复到索引记录的状态
   （可用的来源回到原来的 `ready`/`needs_ocr`，解析结果、OCR 与更正都还在；其余来源重新解析——解析只取决于字节与选项，状态与解析器的原句按原样回来；模板与附件恢复可用），
   扫描记录写 `clean` + `released: true`。两个应用各恢复各自的行；最后一个恢复后隔离副本删除、索引记为 `released`。
   模板与发布恢复时**重扫其余全部内容**：另一份内容处在别的隔离结论下或此刻被判感染 → 保持拒绝（记在那份结论下）；
   有内容出错、超限或缺失 → 记 `error`/`too_large`（可用，下次补扫再看），不会因为只看了一份就记 `clean`。
   索引只把真正恢复（或已不再被拒）的行标为已恢复；状态被他人改动的行留在隔离中并列出，一行都没恢复时退出码 1。
   恢复与补扫、清除共用本应用的运行锁，不会交错。
4. 确认是真的恶意：`quarantine purge <sha256> --yes` 永久删除隔离副本（不加 `--yes` 只说明将删除什么）；
   行保持 `rejected`，索引记为 `purged`，同样的字节再上传仍被拒绝。移在一旁的回归用例**不删**（其中还有别的文件），
   留在 `quarantine/<sha256>.d/` 并列出，由人处理。
5. 每次补扫、隔离、恢复、清除都写审计事件（`rescan.run`、`source.rescan_rejected`、`template.rescan_rejected`、
   `attachment.rescan_rejected`、`quarantine.released`、`quarantine.purged`），元数据只有 id、签名与引擎。

**两个应用共用字节。** blob 存储按内容寻址，同一份字节可能既是 Docs 来源又是消息附件。先运行的一方把字节移入隔离区，
另一方的行在它自己补扫之前就已读不到：附件下载、来源下载与搜索、模板读取按成立的结论回 410（不是 500），
即使字节又回到了存储；这样的来源不进项目的可用来源（项目的其他文件照常识别、预览、生成），随后它自己的补扫套用结论
（"已有隔离结论"，不需要字节）并标记行——包括之前已经记为干净的行。摘要里会写"另一个应用仍有 N 行引用这些字节"并给出命令。

**运行品质。** 开始时扫描器未开启或不可达 → 退出码 3，不读不写；运行中扫描器不再应答 → 停在那里（已完成的结论保留，
再运行从未完成处继续），退出码 3。**两个应用合起来**同时只允许一个 `rescan --apply`、`release` 或 `purge`（`quarantine/.rescan.lock`）：它们作用在摘要上，
一个应用的补扫会把有结论的存储副本移走，另一个应用的恢复正要把它放回——并行时刚恢复的文件会被删掉。第二个操作直接拒绝
（退出码 2），不排队。服务进程缓存成立的结论集合，键是索引每次写入都会前移的计数器（`quarantine/.generation`），
不只看目录时间戳（ext4 的时间粒度可能把临时文件与改名落在同一刻）。不加 `--apply` 只统计：按原记录的
各状态行数、不同内容份数、超过当前上限的份数，并报告扫描器是否可用——不扫描、不写入。输出只有 id、摘要、大小、签名与计数，
不含文件名、内容或其他用户文本。退出码：0 全部干净或无事可做（超过上限的只报告）；1 有感染、出错、摘要不符或内容缺失；
2 用法或配置错误；3 扫描器不可用。第二次运行找不到新的行（干净与已拒绝的行不再被选中）。

**不在补扫范围里的已存文件。**

* 可续传上传（`uploads`）：完成但未使用的上传在被用作来源或模板时走 `add_source`/`upload_template` 的闸门，不需要补扫。
* 成员导入（账户服务，CI-25）：原文件保存在 `<数据目录>/member_imports/<导入 id>`（0600），只供同一次导入按需 OCR，
  不提供下载；导入执行结束时删除，到期清理（CI-96，`SYNWEVO_MEMBER_IMPORT_CLEANUP=on`）连文件一起删除。
  扫描上线前上传、至今未执行的导入文件没有补扫命令；它们不会被任何路由送出，只会被该导入的 OCR 再读。
* 生成的产物：不扫描（由模板与数据生成）；输入被拒后一律撤回（上文）。

### 8.2 其他运维事项

* **误报**：ClamAV 误报按官方流程提交（`clamav.net/reports/fp`）；本地没有"跳过扫描"按钮，管理员也没有。
  补扫隔离的历史文件用 `quarantine release`（§8.1）恢复，它要求当前引擎判定干净。新上传被拒的字节已删除；
  同一字节被拒后再上传恒为 400——需要重新接纳时先在隔离环境确认签名已撤回，再删除 `rejected` 行。
* **备份**：`<数据目录>/quarantine/`（隔离的字节、索引、移在一旁的回归用例；不含锁与半写文件）随数据库进备份——
  被拒绝的行与能恢复它们的索引是同一份状态，只备一半既不能恢复误报，也分不清"按设计撤回"与"丢失"。`restore` 合并它
  （目标已有的条目保留，摘要不符的字节不放回）。**任何恢复之后都再运行一次补扫**：比结论更早的数据库里，行还不知道结论
  （读取仍由索引把关，补扫把行标记并把存储副本移走）。被拒绝或隔离的行仍引用那个摘要：`blob_audit.py` 只在**引用它的每一行
  本身都被拒绝**时把"被引用、不在存储中"列为 `withdrawn_absent`；有干净的行引用它就算缺失；索引说在隔离区而副本不见了，
  列为 `quarantine_copy_missing` 并算缺失（`verify-restore`/`restore` 拒绝这样的备份）。
* **病毒库**：容器内 freshclam 每小时检查；`scan.engine` 记录判定时的库版本；`/readyz` 的 `malware_scanner`
  只说明守护进程可达，不说明库新旧——库日期看 `scan.engine` 或容器日志。
* **资源**：`clamav` 服务 `mem_limit 2048m`（守护进程 + 一次重载）；隔离中的文件占用与普通 blob 相同，
  `scan_failed` 自动重试有6次上限，耗尽后须人工恢复；隔离文件不会因此自动删除，存储回收仍按既有引用/保留策略。Docs当前单进程扫描池防止同来源并发执行；不宣称跨进程扫描exactly-once。回退旧代码仍隔离文件，但旧代码不认识新的耗尽标志，可能恢复无限重试，运维须在回退时核对队列。
* **部署**（2026-09-28 生产已按此完成，见 deployment.md CI-87；官方镜像的入口脚本需要 CHOWN、SETUID、SETGID、DAC_OVERRIDE 四项能力，参考 compose 已改）：`infra/production/compose.yaml` 是参考；接收端不同步 compose，运维需把 `clamav` 服务与 docs 的两个
  环境变量合并进 `/opt/synwevo/config/compose.yaml`，先确认 VPS 有 ≥2 GiB 余量，再 `docker compose up -d clamav`
  等其 healthy，最后重建 docs。回滚只需去掉两个环境变量。
* **clamd 选项 `AlertEncryptedArchive yes`**（§3.1，2026-09-29 加入参考 compose）：官方镜像只接受挂载的配置文件，所以挂的是同一标签
  镜像自带的 `clamd.conf` 加这一行（只读，`/opt/synwevo/config/clamd.conf`）。主机步骤、检查与回滚见 `docs/operations.md` §8；
  **不开** `AlertEncrypted`/`AlertEncryptedDoc`（它们会拒绝只设所有者口令的 PDF）。

## 9. 明确未做

* 管理界面上的"补扫"按钮：补扫与隔离区只有命令行（§8.1）。
* 附件被补扫拒绝时不通知上传者：通知目录没有合适的类别（§8.1）。
* 新上传被拒时，字节只在没有别的用处时直接删除（上传者手里有文件）；同一份字节若还是之前存入的来源（同一文件再次上传）、
  模板、发布或消息附件，上传闸门改为把它移入隔离区并留下成立的结论（每行按原状态记录；两个应用的读取立即拒绝，各自的补扫再
  标记行），可以复核后恢复。上传闸门不取补扫的锁：它与同一份字节的 `release` 恰好同时发生时，恢复的行可能缺存储副本——
  再运行一次 `release` 即可补上。
* 备份里的隔离区含有真实的恶意字节：把备份复制到别处时，那里的杀毒软件可能隔离归档本身。
* 部署前生成、不记来源的脱敏副本授权：某个来源被补扫拒绝时，同一工作空间里这些旧授权一并撤回（分不出是不是那份文件的副本），
  重新生成即可；部署后生成的副本只随自己的来源撤回。
* 成员导入原文件的过期清理：`sweep_expired` 没有调用方且不删文件（§8.1），未确认的导入文件会一直留在数据目录。
* ~~密码保护的压缩包/Office 文件是盲区~~ 2026-09-29 已补（§3.1）：开启扫描时加密的 ZIP/7z/RAR、Office（含 OpenDocument）与
  需要打开密码的 PDF 一律不接纳。仍然存在的：
  * **压缩在别的文件里的加密压缩包**：识别器在整个文件里找压缩包的头，所以不压缩地存在别处（ZIP 里存放、附加在 PDF 或可执行存根后面）的会被找到；
    被**压缩**在文档或压缩包里的，要解压才看得见，靠 clamd 的 `AlertEncryptedArchive`（参考 compose 已配置，生产在下一次部署时按 operations.md §8 加上）。
    嵌在文档里的加密**文档**或 PDF（例如 DOCX 里的加密 XLSX 对象、PDF 附件里的加密 PDF）没有人报——引擎的
    `AlertEncryptedDoc` 同时会拒绝只设所有者口令的 PDF，所以不开。
  * 其他格式的加密：ARJ/ACE/CAB 等冷门压缩格式、S/MIME 或 PGP 加密的内容只靠引擎（第一版在这里写"自解压可执行文件只靠引擎"是错的——
    审查实测引擎也没有报出；现在识别器在整个文件里找 ZIP/7z/RAR）；RAR 与 7z 的识别按格式说明实现、用合成样本与 libarchive 核对，没有用
    RAR/7-Zip 本身写的样本验证（本机没有这两个工具）；7z 头用 Copy/Deflate/LZMA/LZMA2 以外的编码器打包时按"无法检查"拒绝；
    加密的 `.ppt` 只用合成样本验证（LibreOffice 不写加密 PPT）。
  * 没有"要求用户提供密码"的流程：用户去掉密码后重新上传。
  * 失败即拒绝的代价：截断、损坏到识别器读不完的压缩包、复合文档或带 `/Encrypt` 的 PDF 会以同一句话被拒绝，本地头加中央目录头超过 70 万个（约 35 万个条目，已超过 25 MiB 扫描上限所能容纳的数量）的 ZIP、编码器总数超过 10 万的 7z 同样；这些情况用的是"结构无法检查"那一句，不是密码那一句；这是有意的——读不完就不能说它干净。第三轮审查（3030bcd）把第二轮为减少误报放开的两条规则改回失败即拒绝后，代价明确扩大：**加密字典无法求值的 PDF 有没有文件头都拒绝**（含嵌着这类字典的 Ghostscript 动态库、内嵌完整 PDF 片段的文本），**任何带加密标志的 ZIP 本地/中央头都拒绝**（含机器码里偶然带该标志的剖析器 DLL）。因为真实阅读器（MuPDF、libarchive）能用口令打开这两类形状中的一部分，宁可误拒这些少见的二进制/文本，也不放过一个真加密文件；只提到 `/Encrypt` 而没有 PDF 结构的文本、不带加密标志的普通压缩包不受影响。
  * 扫描上线前存入、补扫报告为 `encrypted` 的历史文件照常可读，等所有者决定（§8.1）；项目复制会把这样的文件带到别的空间，
    审批页显示「设有密码，无法检查」，由审批人决定。
* 产物（生成的 DOCX/PDF/XLSX）不扫描：它们由模板与数据生成，不包含上传的原始字节；若将来允许把上传文件原样
  打包进产物，需要在打包前复用 `scan_template_bytes`。
* ~~生产尚未部署扫描器~~ 2026-09-28 已上线（CI-87，`docs/quality/deployment.md`）：accounts、docs、messages 以 `required` 扫描新上传；此前入库的文件仍是 `not_configured`，补扫见 §8.1（CI-90）。


## 附：成员导入上传（账户服务，CI-25）

组织成员批量导入让账户服务第一次接收用户文件。它走同一条接纳链，只是由账户服务自己执行：

| 环节 | 实现 | 说明 |
|---|---|---|
| 接收 | `POST /api/auth/organizations/{oid}/imports`（multipart，≤ 20 MB，`org.members.import`） | 同一 clamd 策略（`synwevo_core.malware`，已从 Docs 搬入共享包）：感染 400、加密 400 `encrypted_file`（§3.1）、扫描器不可用 503（不放行）、超限 413；未配置扫描器时 `scan.status=not_configured` 如实返回并显示在页面上 |
| 读取 | `synwevo_docs.ingest.DocumentIngest`（由 `synwevo_server` 注入）或 `synwevo_core.member_import.BasicIngest` | 引擎的全部结构与资源上限照用；导入另加：每表 2000 行、5000 行文本、200 页文字、20 页 OCR、单次 500 行计划 |
| 保留 | 原文件仅在 `extracted/planned` 阶段保存在 `<data>/member_imports/<id>`（0600），供按需 OCR；执行结束即删除。7 天的 `expires_at` 只写在库里：过期清理没有调用方、也不删文件（CI-90 核对，见 §8.1、§9） | 库里只留提取出的行（有界）与逐行结果 |
| 输出 | 报告 CSV 对 `= + - @` 开头的单元格加前缀，`no-store` | 不下载原文件 |

Tesseract 子进程现在与 LibreOffice 一样只继承最小环境（`engine/ocr.py:_child_env`），账户服务的 SMTP 密码不会进入 OCR 子进程。
生产 compose 目前只给 `docs` 容器配置了 `SYNWEVO_CLAMD_ADDRESS`；给 `accounts` 加上同样两项后，导入上传才会真正扫描（见 operations.md §8）。
