# 验收结果

* 验收日期：2026-09-23
* 环境：macOS 27（Apple Silicon，15 核，24 GB），Python 3.14.5，LibreOffice 26.8.0.3，Tesseract 5.5.0（chi_sim + eng，tessdata_fast），Google Chrome（Playwright 驱动），Microsoft Word / Excel / PowerPoint for Mac
* 测试数据：全部由 `apps/docweave/scripts/make_fixtures.py` 生成，文件名以「测试_」开头，姓名、编号均为虚构（见 `testdata/README.txt`）
* 结论：**14 项验收场景全部通过**。自动化结果：`pytest` 65 项全部通过；浏览器端到端 2 个脚本（5 条流程）全部通过。下表列出每项的测试位置与具体检查内容。

复现：

```bash
.venv/bin/python -m pytest                            # 650 passed, 1 skipped（约 2.5 分钟，三个套件）
.venv/bin/python -m pytest apps/docweave/tests        # 306 passed, 1 skipped（只跑 DocWeave）
python scripts/platform.py up &                                      # 启动服务后
.venv/bin/python apps/docweave/scripts/ui_e2e.py              # 主流程（真实浏览器操作）
.venv/bin/python apps/docweave/scripts/ui_e2e_more.py         # 分组/PDF 框选/内置编辑器
.venv/bin/python apps/docweave/scripts/benchmark.py           # 性能实测
```

## 1. 场景结果

| # | 场景 | 结果 | 测试 | 检查内容 |
|---|---|---|---|---|
| 1 | 每行生成 | ✅ | `test_acceptance_1_4.py::test_s1_per_row_100_contracts`；`apps/docweave/scripts/ui_e2e.py`（界面全流程） | 100 行 XLSX → 100 份 DOCX；第 1、37、100 条逐字核对姓名、编号、日期（`yyyy年m月d日`）、金额（`#,##0.00`）；编号前导零保留（文件名 `000001_…` 与正文）；未映射的固定段落不变；ZIP 恰好 100 份 + 清单 |
| 2 | 跨文件复用 | ✅ | `test_s2_cross_file_reuse_20` | 20 份检测表只在代表文件上用选区生成定位（服务端生成前后文锚点）；「校验」对 20 个文件逐项通过；生成 20 份，每份的明细行与各自来源表格逐行相同 |
| 3 | 分组与重复明细 | ✅ | `test_s3_group_orders_15`、`test_s3b_group_into_xlsx_template_with_formulas`；`apps/docweave/scripts/ui_e2e_more.py` 流程 A | 100 条打乱顺序的明细按订单号分 15 组 → 15 份；每份只有本订单明细，按「行号」数值排序；明细总行数 100；XLSX 版本：插入明细行后小计公式逐行平移、合计 `SUM` 扩展、LibreOffice 计算的缓存值正确、下方内容下移不被覆盖、边框样式复制 |
| 4 | 区域语义 | ✅ | `test_s4_region_semantics`；界面「选中区域的含义…」对话框 | 同样的 10 行：规则预览先显示「10 份」或「1 份」；按「每行一份」生成 10 份；按「整块为明细」生成 1 份且表格含 10 行明细 |
| 5 | 表格区域填充 | ✅ | `test_s5_region_fill_and_size_mismatch` | 3×4 来源 → 3×4 目标逐格对应；目标填充色与字体保留；周边单元格不被写入；3×4 → 3×3 时预览被阻止、生成返回 409，不截断 |
| 6 | 重复明细边界 | ✅ | `test_s6_repeat_detail_0_1_many_and_pagination`、`test_s6b_xlsx_zero_rows_removes_template_row` | 0 行（显示「（无）」/ 删除行两种策略）、1 行、60 行；60 行 DOCX 与 PDF：表头行设置跨页重复，PDF 每页都有表头，60 条全部出现，表格后文字未被覆盖；XLSX 0 行删除模板行后下方内容上移并对失效公式给出警告 |
| 7 | 一对多与冲突 | ✅ | `test_s7_one_to_many_and_conflicts` | 同一列写入正文和页眉两个字段；显式「拼接」组合两个来源；两个映射写同一字段 → 摘要中列出 `target_conflict` 且生成被拒绝（409）；多来源无组合规则 → `combine_missing` |
| 8 | 结构变化 | ✅ | `test_s8_structure_changes` | 列顺序变化的文件按列名取值正确；重名列未消歧 → 该样本报「同名列」并在跳过策略下明确列为跳过，指定第 2 个同名列后正确；缺列、工作表缺失、同名（仅空格不同）工作表歧义分别报 `missing_column` / `missing_sheet` / `ambiguous_sheet`；DOCX 标签被改名 → 该文件报 `anchor_missing`，其他文件正常 |
| 9 | 模板保留 | ✅ | `test_s9_template_preservation` | 除 `word/document.xml` 外所有部件（样式、页眉页脚、图片、主题等）字节级相同；未涉及的段落与表格文字相同；图片保留；中文长文本（约 2 400 字）完整写入并沿用字段起始字符的字体；PDF 导出跨多页、未截断；模板使用的未安装字体在上传时提示 |
| 10 | PDF 与 OCR | ✅ | `test_s10_text_pdf_source_into_pdf_template`、`test_s10_scanned_image_ocr_and_correction`、`test_s10b_pdf_form_template`、`test_engine.py::test_pdf_coordinates_and_redaction[0/90/180/270]`、`test_claims.py::test_scanned_pdf_template_clears_pixels`；`apps/docweave/scripts/ui_e2e_more.py` 流程 B（真实鼠标框选） | 归一化坐标 ↔ PDF 坐标在四种旋转下往返一致；从第 2 页文字层框选取值并记录标签；旋转页模板字段；输出中两页旧值均无法再提取；PNG 本地 OCR、低置信度阻止并在人工修正后生成；表单字段填写并扁平化；扫描 PDF 模板框内像素被清除 |
| 11 | 保存和重用 | ✅ | `test_s11_persistence_reuse_and_versions`、`test_s11b_job_snapshot_isolated_from_later_edits`；`apps/docweave/scripts/ui_e2e.py`（刷新页面后任务仍可见） | 新进程打开同一数据目录：模板版本、字段、规则、映射全部恢复；替换为新一批同结构文件后生成使用新数据；任务创建后再修改模板/映射，任务仍使用创建时的模板版本和映射 |
| 12 | 任务恢复 | ✅ | `test_s12_partial_failure_retry_and_names`、`test_s12b_cancel`、`test_s12c_restart_recovery` | 超过 Excel 单元格上限的样本失败并写明原因；模拟的临时故障在重试后成功（尝试次数 2），已成功项的文件不变；同名输出为 `A.xlsx` 与 `A (2).xlsx`；取消后「成功 + 已取消 = 总数」、文件数 = 成功数 × 格式数；服务重启后中断的任务自动续跑完成；清单 CSV 与界面计数一致 |
| 13 | 原生文件验证 | ✅ | `test_s13_outputs_open_in_libreoffice[docx/xlsx/pptx]` + 每个输出生成后的自动复核 + **Microsoft Office 实机打开** | 每个输出都用 python-docx / openpyxl / python-pptx / PyMuPDF 重新打开并逐值核对（不以 HTTP 200 为准）；LibreOffice 打开并渲染 DOCX/XLSX/PPTX；实机：见下节 |
| 14 | 格式能力边界 | ✅ | `test_s14_capability_boundary`；生成对话框 | DOCX 模板的 XLSX/CSV 导出显示「不可用」并给出替代方案（改用表格型模板）；强行提交返回 400 `format_unavailable`，不产生任何任务；PDF 模板不提供 DOCX；XLSX 模板提供 CSV（值），CSV 中公式结果由 LibreOffice 计算 |

补充覆盖（`test_formats.py`、`test_claims.py`）：PPTX 作为来源与模板；XLS/ODS 来源；DOC/ODT/RTF 来源与模板；PPT 模板；HTML（脚本不执行、表格可选）、GB18030 文本；内置文档模板（横向纸张、页眉页脚字段、图片、合并单元格、重复行、同名字段共享绑定、再次编辑生成新版本）；内置表格模板；上传防护（路径穿越文件名、损坏文件、不支持格式、ZIP 炸弹）；XLSX 插入行时表格对象、打印区域、图表数据系列、批注和图形锚点的平移。

## 2. Microsoft Office 实机打开检查（第 13 项）

2026-09-23 在本机用 Microsoft Office for Mac 打开本软件生成的文件，**均未出现「发现无法读取的内容 / 修复」提示**：

| 文件 | 应用 | 观察结果 |
|---|---|---|
| `合同_000001.docx`（跨 3 个 run 的字段、页眉字段、图片、表格、页脚） | Microsoft Word | 正常打开；页眉显示「合同编号：000001」；字段值保留红色样式；图片、表格、页脚完整 |
| `订单_DD-001.docx`（重复表格行 7 行） | Microsoft Word | 正常打开；7 行明细按行号 1–7；表格后的「（明细结束）」保留 |
| `订单_DD-001.xlsx`（插入 6 行、公式平移、合计扩展、合并单元格） | Microsoft Excel | 正常打开；Excel 重新计算后小计与合计 142.8 正确；边框、合并标题保留；「备注」下移到第 14 行 |
| `汇报_DD-001.pptx`（文本框字段、重复表格行 7 行） | Microsoft PowerPoint | 正常打开；客户字段已替换；7 行明细保留表格条纹样式 |

（Word 标题栏显示「兼容模式」是因为测试模板由 python-docx 的默认模板生成，属于模板本身的兼容性设置，与填充无关。Excel 窗口顶部的「打开已恢复的工作簿？」是 Excel 自身对之前会话的恢复提示，与本文件无关。）

## 3. 性能实测（`apps/docweave/scripts/benchmark.py`）

同一进程内通过真实 HTTP 接口执行完整流程（解析 → 样本 → 计划 → 填充 → 重新打开校验 → 保存）。

| 场景 | 样本 | 输出文件 | 来源解析 | 任务总耗时 | 平均每个文件 |
|---|---|---|---|---|---|
| XLSX 100 行 → DOCX（4 个字段） | 100 | 100 | 0.11 s | 1.19 s | 11.9 ms |
| XLSX 1 000 行 → DOCX | 1 000 | 1 000 | 0.15 s | 11.3 s | 11.3 ms |
| XLSX 5 000 行 → DOCX | 5 000 | 5 000 | 0.37 s | 69.0 s | 13.8 ms |
| XLSX 20 行 → DOCX + PDF（LibreOffice 转换） | 20 | 40 | 0.12 s | 17.1 s | 428 ms（主要为 PDF 转换，约 0.8 s/份） |
| 2 000 条明细 / 200 组 → XLSX（插入明细行 + 公式平移） | 200 | 200 | — | 0.93 s | 4.7 ms |
| PDF 模板 300 份（删除原文字 + 嵌入中文字体子集） | 300 | 300 | — | 23.3 s | 78 ms |
| OCR 一张 1240×700 扫描图片 | 1 | — | — | 0.17 s | — |

整个基准进程峰值内存约 **436 MB**。据此公布的限制：单任务默认上限 20 000 个样本（可调）；PDF 导出按约 1 秒/份估算；更大的批量请拆分任务。未测量的规模不作承诺。

## 4. 已知限制

见 `docs/FORMATS.md` 第 3 节与 `README.md`「已知限制」。
