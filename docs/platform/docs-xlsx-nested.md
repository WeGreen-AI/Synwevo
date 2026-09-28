# XLSX 嵌套明细：多行的重复内容块与块内明细行（设计，待审）

持续迭代 §8.1 要求「表格与非表格重复块、嵌套/跨页明细」按模板结构生成，而不是凑巧匹配一个样本。
DOCX 已有（CI-104，`docs-nested-details.md`）；XLSX 目前只有单行的重复明细，没有「每段复制一组行」。
本设计把 DOCX 的模型原样搬到电子表格：**一组连续整行 = 重复内容块**，块内可有一行（或几行）重复明细，
计划层（`_sectioned_rows`、`_nest`）不改。新增的只有：XLSX 的字段形状、引用改写规则和填充算法。

## 1. 用户故事

财务要给每位客户一份对账单（每组一份，按「客户」分组），一张工作表里按订单一段段往下排：
每段有订单号、日期，段内是该订单的明细行，段末是订单小计，所有订单下面是合计。

模板工作表「对账单」（D 列为金额）：

```
1  客户：| C-0                          ← 普通单元格字段（每份文件一个值）
3  订单号：| O-0 | 日期：| 2024-01-01   ┐ 重复内容块「订单段」第 3–6 行；B3、D3 是块的列
4  品名 | 数量 | 单价 | 金额            │ 块内表头
5  示例 | 1 | 0.00 | =B5*C5            │ 块内明细行「明细」（parent = 订单段）
6  订单小计 |  |  | =SUM(D5:D5)        ┘ 段小计
8  合计 |  |  | =SUM(D6)                ← 块外：对每一段小计求和
```

操作：在网格里拖选第 3–6 行（或点行号 3、Shift+点行号 6）→「设为待填区域」→「重复内容块（整行）」；
点第 5 行行号 →「重复明细行」（提示它在内容块「订单段」里）；点 B3 →「作为内容块「订单段」的一列」，D3 同理。
映射对话框里给「订单段」选「分段字段：订单号」，列 B3←订单号、D3←日期（段内取值「必须一致」）；「明细」照常映射。

客户 A（A-1 两行、A-2 一行）的输出：

```
3  订单号：A-1  日期：2024-03-01        8  订单号：A-2  日期：2024-03-09
4  品名 数量 单价 金额                  9  品名 数量 单价 金额
5  螺丝 5 2.00 =B5*C5                  10 垫圈 10 0.50 =B10*C10
6  螺母 3 1.20 =B6*C6                  11 订单小计 =SUM(D10:D10)
7  订单小计 =SUM(D5:D6)                13 合计 =SUM(D7,D11)
```

每段只含本段明细，段小计只覆盖本段的明细行，合计展开到每一段的小计；公式结果由内置引擎写入并由 Excel 打开时重算。

## 2. 数据模型（不迁移；新增键都可选）

* **内容块字段**沿用 DOCX 的形状 `kind: "repeat", block: true`，定位是整行：
  `locator: {sheet, first, last}`（0 起行号，`last > first`），`columns: [{key: "c0", name, cell: "B3", original_text, had_formula}]`，
  `empty_policy ∈ EMPTY_POLICIES["block"]`（remove / placeholder / error）、`placeholder`、`separator ∈ SEPARATORS`
  （none / blank = 份与份之间空一行 / page_break = 每份之后手动分页符，最后一份除外）。工作副本加标记名 `_dw_<id>`（`$A$r1:$XFD$r2`）。
  用 `block: true` 就自动得到：`static_check` 允许 `group_by`、`may_nest`、`EMPTY_POLICIES["block"]` 校验、映射对话框与侧栏的内容块界面。
* **块的列**：块行内、不在明细行上的单元格。请求 `{kind: "block_column", block_id, sheet, cell}`，键按 `c{n}` 取第一个未用的号
  （同 `docx_blocks.add_column`）；名称来自对话框。移除列只改字段（单元格恢复模板原文）。
* **块内的明细行**是现有 XLSX `repeat` 字段，`parent: <块 id>` 由 `assign_parents` 推出：XLSX 不读文件，按定位算
  （同一工作表且 `first ≤ row ≤ last`），每次存版本、模板详情、生成时重算，两种创建顺序都成立，删块即清 `parent`。
  一个块可有多行明细（各占一行，互不相邻亦可），每行都是块的子字段，`_nest` 本来就逐个处理。
* **块内的普通单元格字段**：每一份写入同一个值（与 DOCX「块内的其他字段每一份填入相同的值」一致）。
  区域字段不能与块相交；明细行上不能再建单元格字段（明细行上的单元格只能是明细列）。
* 块外的明细行仍是独立的单行重复，行为不变。

## 3. 引擎

### 3.1 引用规则（块 = 第 b1–b2 行，明细行 d，第 k 份从 s_k 行开始、有 m_k 行明细）

| 引用写在 | 指向 | 生成后 |
|---|---|---|
| 块内（含明细行） | 完全在块内 | 跟随本份：r<d → s_k+(r−b1)；r>d → 再加 m_k−1；区域起点在 d → 本份第一明细行，终点在 d → 本份最后明细行（**段小计只覆盖本段**）；块内单格 `=D5` → 第一明细行（同现有「单格不扩展」）；明细行上指向本行的相对行 → 当前行，绝对行 `$5` → 本份第一明细行（`SUM($D$5:D5)` 仍是段内累计） |
| 块内 | 块外 | 不随份移动，**不论有没有 `$`**；块下方的目标按插入行数平移。这与独立明细行的 Excel「向下填充」语义不同，是有意的：`=D5*H1` 的税率格不能每份漂移 |
| 任意位置 | 包含整个块（a≤b1 且 b≥b2，含整列、整行区间） | 扩展到覆盖全部份：`SUM(D3:D6)` → `SUM(D3:D11)`（小计也在 D 列时会重复计算，界面说明中推荐下一行的写法） |
| 块外（含其他工作表） | 完全在块内，且是 SUM / COUNT / COUNTA / AVERAGE / MIN / MAX / PRODUCT 的一个完整参数 | 展开为每一份对应区域：`SUM(D6)` → `SUM(D7,D11)`；`SUM(D5:D5)` → `SUM(D5:D6,D10:D10)`；某份没有对应行则不列入 |
| 块外 | 其他落在块内的引用（`=D6*1.1`、VLOOKUP 区域、名称、图表系列、表格、筛选） | 建块时拒绝（只能指向其中一份） |
| 任意 | 跨过块边界 | 拒绝 |

3-D 引用、INDIRECT/OFFSET 的文字地址不改写（现有限制，写进说明）。

### 3.2 填充算法（`xlsx_doc.fill_band`，一次平移，逐份生成）

现有 `insert_copies_of_row`（xlsx_doc.py:584）每插一次就对整本工作簿 `shift_rows`；K 份 × 两次平移会是 O(K×工作簿)。
所以按最终布局一次算好：

0. **校验与取下**：`expand_shared_formulas`；`check_band`（与建字段同一函数，填充时再跑一遍，旧版本也受保护）。
   由计划值得到 K 份与每份 m_k（有明细 = 行数；没有时按子字段策略：remove_row→0，blank_row / placeholder→1），
   算出 s_k、总高 T（含 blank 分隔行）、Δ = T − H；先做上限检查（§5）。
   块外公式里「聚合参数且完全在块内」的引用换成占位名 `_dwband<n>`（`shift_formula` 不碰名称，refshift.py:141）。
   把块的行从 `sheetData` 取下作原型，公式**只分词一次**成模板；同时取下块内合并、块内的 sqref 部分、块内分页符。
1. **平移块外（一次）**：Δ>0 → `shift_rows(sheet, Shift(b2+1, Δ, expand_row=b2, expand_from=b1))`。
   新参数 `expand_from`：区域终点等于 b2 且起点 ≤ b1 才扩展（现有 `expand_row` 只看终点，会把块内的 `D6:D6` 也扩出去）；
   `expand_single` 同样受它约束。Δ<0（K=0 且删除块，或唯一一份没有明细行）→ `Shift(b2+1+Δ, Δ)`：块已取下，被删的位置上
   只剩块外指向块内的引用——它们已被拒绝或换成占位名；包含整个块的区域按删除规则收缩。
   其余结构（块外的公式、合并、条件格式、数据验证、超链接、筛选、表格、名称与打印区域、图表、批注与图形锚点、分页符、
   透视缓存来源）全部沿用 `shift_rows` 现有代码（xlsx_doc.py:417–556），不另写。
2. **逐份生成**：对第 k 份的每个原型行克隆 `<row>`（行高、隐藏、分级、样式随行），改行号与单元格坐标，按 3.1 代入公式模板；
   明细行按 m_k 克隆；单行数组公式的 `ref` 改为本行（同 xlsx_doc.py:623–627）。块内合并每份一组（明细行上的横向合并每行一个）；
   块内条件格式 / 数据验证 / 忽略错误（含 x14 `xm:sqref`）的块内部分每份一个区域，仍是同一条规则的多区域 sqref（相对引用逐格生效）；
   块内分页符每份复制，紧跟块末行的分页符移到最后一份之后，`page_break` 分隔符加在每份之间，更新 `count` / `manualBreakCount`。
   写值顺序：块内普通单元格（同值）→ 块的列（第 k 行数据）→ 子明细行（`groups[k]`），都走 `XlsxDoc.set_cell`（类型、日期格式、共享字符串、警告不变）。
   0 明细的 blank_row / placeholder 与现有单行规则相同（templates.py:577–583）。按行号有序插回，占位名换成参数列表。
3. **其余字段**：块作为一个平移单位加入 `xlsx_fill` 的 `shifts`，单值 / 区域字段仍按 `mapped_row` 写入；`finalize` 计算全部公式。

同一工作表上的多个块、块与独立明细行按起始行自下而上处理（沿用 templates.py:561 的顺序思路，排序键改为单位的首行）；
块内的明细行不进外层循环，由块填写。

## 4. 计划与映射

* `plan._sectioned_rows`、`_row_ids`、`_nest` 不改（不看格式）。`static_check` 只改 `group_by_unsupported` 的句子（plan.py:89）。
  `nested_needs_group_by`、`nested_source_mismatch`、`nested_shared_binding`、段内 `no_detail`、空键、段内冲突全部原样适用。
* `xlsx_fill` 加与 docx_fill 相同的守卫：子明细有行但没有 `groups` → 原句 `nested_unconfigured`（templates.py:282）。
* 映射对话框不改逻辑：`isBlock = slot.block`（MappingDialog.tsx:52）已给出「分段字段」「分段字段为空的明细行」「段内取值」，
  块内有明细而未分段时不能保存；子明细显示「此表在内容块「X」内」。

## 5. 限制与拒绝（中文为源句，英文与繁体进 `messages.refusals.ts`；`refusals.test.ts` 每一步都要绿）

建字段时（`TemplateError` → 400；填充时再校验，句子相同，作为该份的错误）：

| code | 句子 |
|---|---|
| band_rows | 重复内容块至少要有两行；只重复一行请用「重复明细行」 |
| band_overlap | 第 {a}–{b} 行与重复内容块「{n}」（第 {c}–{d} 行）重叠：内容块之间不能交叉或嵌套（块套块即此句） |
| band_merge | 合并单元格 {ref} 跨过了重复内容块的边界（第 {a}–{b} 行）：请让它完全在块内或块外 |
| band_reference | {位置}引用了重复内容块内的 {ref}：内容块会按段复制，这个引用只能指向其中一份。请改为引用整个内容块（如 {whole}），或写成 SUM/COUNT/COUNTA/AVERAGE/MIN/MAX/PRODUCT 的参数（生成时展开到每一份）。{位置} ∈ 单元格 {s}!{c} 的公式 / 名称「{n}」/ 图表「{t}」的数据系列 / 条件格式 {sqref} 的公式 / 数据验证 {sqref} 的公式 |
| band_cross | {位置}的引用 {ref} 跨过了重复内容块的边界（第 {a}–{b} 行）：请让它完全在块内，或包含整个块 |
| band_objects | 第 {r} 行有{图片或图形 / 批注 / 超链接}（{ref}）：复制内容块时不会复制它，请移到块外或删除 |
| band_table | 表格「{t}」（{ref}）与重复内容块相交：Excel 表格不能随内容块复制，请转换为普通区域或移到块外 |
| band_pivot | 工作表「{s}」第 {r} 行有数据透视表「{p}」：插入行时透视表的位置不会随之移动，请把内容块放到透视表下方或其他工作表 |
| band_array | 数组公式 {ref} 跨越多行且与重复内容块相交，无法随内容块复制 |
| band_region | 区域字段「{n}」与重复内容块「{b}」相交：区域字段不能放在内容块里 |
| band_detail_cell | 单元格 {ref} 在重复内容块「{b}」的明细行上：明细行上的单元格只能是明细列 |
| band_column_outside | 请在重复内容块「{b}」的行内（第 {a}–{b} 行）选择单元格 |

沿用：`vertical_merge`（明细行与纵向合并相交）、`merged_inner`、`overlap`、`duplicate_repeat`。明细行在块外**不拒绝**：它是独立明细行。

生成时（该份的问题）：

| code | 级别 | 句子 |
|---|---|---|
| band_row_limit | error | 重复内容块「{b}」展开后工作表「{s}」共 {n} 行，超过 Excel 的上限 1,048,576 行 |
| band_cell_limit | error | 重复内容块「{b}」展开后要写入 {n} 个单元格，超过上限 {max}（SYNWEVO_DOCS_MAX_CELLS） |
| band_formula_limit | error | 公式 {s}!{c} 要列出 {n} 段，超过 Excel 每个函数 255 个参数或 8 192 个字符：请改为引用整个内容块 |
| ref_removed | warning | 重复内容块「{b}」没有数据，已删除；引用它的公式 {cells} 变为 #REF!。如需保留公式，请把零条明细处理方式改为「保留一份」 |
| ref_removed | warning | 「{段}」这一段没有明细行，明细行已删除；该段的公式 {cells} 变为 #REF!。如需保留公式，请把明细行的零条明细处理方式改为「保留一行空白」 |

改句：`group_by_unsupported` →「映射 #{no}：分段目前只支持 Word 和 Excel 模板的重复内容块；PPT / PDF 模板和表格行暂不支持嵌套明细」。
原样复用：`nested_unconfigured`、`nested_needs_group_by`、`nested_source_mismatch`、`no_detail`、`subgroup_key_empty`、`group_conflict`、`block_lost`。

## 6. 验证

* **引擎**（`apps/docs/tests/test_xlsx_band.py`，openpyxl 合成模板，期望值由写入的数据算出，夹具标明）：
  §1 的例子逐格核对（值、公式文本、每份小计只覆盖本份、合计展开）；`data_only` 读回内置引擎的缓存值等于期望；
  有 LibreOffice 时 `converters.recalc_xlsx` 重算后小计与合计相同。公式改写：块内相对/绝对、块外税率格固定、块下方目标平移、
  段内累计、其他工作表的汇总 `SUM(对账单!D6)` 展开、`SUM(D3:D6)` 扩展、整列不动。结构：块内合并每份一组、明细行横向合并每行一个；
  条件格式与数据验证的 sqref（含跨边界拆分、x14）；打印区域扩展、块下方名称/表格/图表/批注锚点平移；行高与隐藏行随份；
  块内分页符、`page_break` 分隔符与 count 属性、`blank` 分隔行。空策略：块 remove（K=0，警告）/ placeholder / error；
  子明细 m=0 的四种策略。一块两行明细；一表两个块；块上下各有独立明细行；块在第二张表而汇总在第一张。
  每条拒绝句子各一例（建时与填充时）；上限用 monkeypatch 调小。500 份 × 3 行的时限（原型公式只分词一次）。
* **接口**（`apps/docs/tests/test_xlsx_nested.py`，对照 `test_docx_nested.py`）：两种创建顺序与 `parent`、删块清 `parent`、
  列的增删、`group_by` 预览与生成、`jobs.verify_output` 已校验值 > 0 且无缺失、空键两种策略、段内冲突与 `first`、
  未分段的静态检查、PPT/PDF 上的 `group_by` 仍被拒、`definitions` 导入导出往返（`repeat_block {sheet, rows: "3:6"}`、`block_column {block, sheet, cell}`）。
* **前端**：`npm test`（`refusals.test.ts`、`i18n.test.ts`、`workbench.test.ts` 加 XLSX 选项用例）、`npm run typecheck && npm run build`。
* **浏览器证明**（隔离实例 317xx，夹具账户与合成工作簿；由 `scratchpad/nested_proof.py` 改写为 `xlsx_band_proof.py`）：
  拖选第 3–6 行建块、点行号建明细行、点 B3 建列 → 未分段时预览给出整句说明 → 选「订单号」→ 预览中客户 A 两段各自只有本段明细、
  小计与合计正确、「已校验 N 个值」；重开对话框首帧即已存设置；英文与繁体界面无残留中文（用户数据除外）；手机宽度无横向溢出；无页面错误。
* 仓库守卫：pytest 三套件、`check_boundaries` / `check_brand` / `check_design` / `check_component_names` / `brand_assets --check`。

## 7. 分批

1. **引擎（约 45%）**：`refshift`（`expand_from`、块内行映射、聚合参数识别与展开、引用扫描）、`xlsx_doc.fill_band`、
   `templates.check_band` 与 `xlsx_fill` 接入（单位排序、块内单元格、`nested_unconfigured`、上限）；`test_xlsx_band.py`（字段手工构造）；
   本步产生的填充时句子同时入目录。可独立提交与测试。
2. **模板与接口（约 25%）**：`xlsx_add_slot` 的 `repeat_block`、`xlsx_add_block_column`；`_check_overlap`（templates.py:527 假定 `locator.row`）
   与重复行查重（:495–497）认识块；`assign_parents(work, slots, fmt)` 支持 XLSX，并放开 services.py:841、:1003 与 jobs.py:198 的 docx 限定；
   `update_slot` 的 `remove_column`（services.py:1291、:1331–1333）按格式分支；`definitions` 的两种字段（definitions.py:43–44、:280–301）与
   `docs/integration/api.md`；建时句子入目录；`test_xlsx_nested.py`。
3. **界面与记录（约 30%）**：SlotDialog 的 XLSX 选项（多行选择 → 重复内容块，整行选择时排第一；块内单格 → 作为块的列 / 每份同值的单元格；
   块内单行 → 重复明细行并注明所在块）；GridView 行号 Shift+点击扩展行选择（GridView.tsx:377）；`slotPos` 画块与块的列（describe.ts:69）；
   侧栏块设置显示「第 a–b 行」和列的单元格；三语言；浏览器证明；FORMATS.md §1/§2、`docs-nested-details.md` §7、迭代状态与进展记录。

**本轮不做**：块套块（多层嵌套）；每组一张工作表（跨工作表的块）；横向（按列）重复；块内图片/图形/图表/批注/超链接的复制（拒绝）；
与块相交的 Excel 表格（拒绝）；块内列的自动识别（DOCX 的「标签：值」）；内置表格编辑器里的块；图表系列指向块的一部分（拒绝）；
内置引擎的 SUBTOTAL（仍由 LibreOffice 计算或 Excel 打开时计算）；子组单独排序；打印时每页重复块内表头（Excel 只能重复固定行）。

## 8. 待定问题

1. 名称：XLSX 也叫「重复内容块」（一个概念、对话框与拒绝句子通用，本设计的选择）还是另起「重复区段」？
2. 块内引用块外一律固定（3.1 第二行）——与独立明细行的「向下填充」不同，是否接受？
3. 聚合展开的函数范围：是否加入 SUBTOTAL（参数形状相同），并顺带在内置引擎实现 SUBTOTAL 以支持「包含整个块」的写法？
4. 块内的图片、批注、超链接先拒绝；是否有真实模板需要复制它们（会进入下一批）？
5. 已有缺口（与本设计无关但同一代码）：`shift_rows` 不平移同表数据透视表的 `location` 与线程批注的 `ref`，单行明细插行也不检查
   1,048,576 行上限。建议单独修（附回归），本设计先对块拒绝透视表并自带行数上限。
6. 条件格式公式里指向块内的**绝对**行引用先拒绝；若常见，可改为每份一条规则（代价：规则数 × K）。
