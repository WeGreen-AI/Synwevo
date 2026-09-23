# 测试方法

## 1. 测试数据

```bash
.venv/bin/python apps/docweave/scripts/make_fixtures.py        # 生成到 testdata/（pytest 首次运行时也会自动生成）
```

全部为合成数据（姓名以「测试」开头、编号虚构），包括：100 条客户 XLSX、列顺序变化/重名列变体、合同 DOCX 模板（跨 run 文字、页眉页脚、图片、表格）、20 份同结构检测表 DOCX 与报告模板、100 条订单明细（15 个订单，行号被打乱）与 DOCX/XLSX 订单模板、3×4 区域来源与模板、文本 PDF、含旋转页的 PDF 模板、AcroForm 模板、扫描 PNG/PDF、PPTX 模板、GB18030/UTF-8 CSV（含引号内换行）、损坏文件与不支持文件。

## 2. 自动化测试（pytest）

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest apps/docweave/tests -q
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

需要本机安装 Google Chrome，并先启动服务：

```bash
python scripts/platform.py up &
.venv/bin/python apps/docweave/scripts/ui_e2e.py           # 新建项目→上传→设模板→选文字建字段→定义样本→点选单元格建映射→预览→生成 100 份→下载 ZIP 核对→刷新→手机宽度查看任务
.venv/bin/python apps/docweave/scripts/ui_e2e_more.py      # 分组+XLSX 明细（拖选区域）、PDF 鼠标框选字段、内置文档编辑器、内置表格编辑器
.venv/bin/python apps/docweave/scripts/ui_e2e_keyboard.py  # 命令面板（⌘K）、映射列表键盘与批量操作+撤销、主题与文档纸张、布局记忆与减少动效
.venv/bin/python apps/docweave/scripts/ui_perf.py <项目ID>   # 可见窗口中的真实帧时间：首个可操作视图、选区/键盘响应、滚动与拖拽帧间隔、长任务、内存
```

截图保存在 `docs/screenshots/`。

## 4. 性能

```bash
.venv/bin/python apps/docweave/scripts/benchmark.py
```

输出 JSON，包含各场景耗时与进程峰值内存（结果记录在 `docs/ACCEPTANCE.md`）。

## 5. 人工检查清单（可选）

1. 用 Microsoft Word / Excel / PowerPoint 打开生成的文件，确认无修复提示（本次结果见 `docs/ACCEPTANCE.md` 第 2 节）。
2. 用真实（脱敏）业务文件重复主流程，并把问题文件加入 `testdata/` 与回归测试。
