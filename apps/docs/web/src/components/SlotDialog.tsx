import { serverText, issueText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useState } from "react";
import { describeSelection, pdfLike } from "../lib/describe";
import type { Selection, Slot, TemplateDetail } from "../lib/types";
import { isImeKey, Modal, cellRef, rangeRef } from "@synwevo/ui";
import { errorText } from "../lib/perm";
import PdfRepeatEditor, { type RepeatLayout } from "./PdfRepeatEditor";

interface Props {
  tpl: TemplateDetail;
  sel: Selection;
  suggestedName: string;
  onCreate: (req: any) => Promise<void>;
  onClose: () => void;
}

export default function SlotDialog({ tpl, sel, suggestedName, onCreate, onClose }: Props) {
  useI18n();
  const slots = tpl.version.slots;
  const options = kindOptions(tpl.format, sel, slots);
  const [kind, setKind] = useState(options[0]?.kind || "text");
  const [name, setName] = useState(suggestedName || "");
  const [mode, setMode] = useState(sel.kind === "docx_text" && sel.start === sel.end ? "insert" : "replace");
  const [required, setRequired] = useState(false);
  const [share, setShare] = useState(true);
  const [emptyPolicy, setEmptyPolicy] = useState("remove_row");
  const [headerRows, setHeaderRows] = useState<number>(sel.kind === "docx_row" ? Math.min(1, sel.row) : 1);
  const [fontSize, setFontSize] = useState<number | "">("");
  const [minFont, setMinFont] = useState(6);
  const [align, setAlign] = useState("left");
  const [layout, setLayout] = useState<RepeatLayout | null>(null);
  const [layoutInfo, setLayoutInfo] = useState<{ capacity: number; error?: string }>({ capacity: 0 });
  const [overflow, setOverflow] = useState("error");
  const [pdfEmpty, setPdfEmpty] = useState("blank");
  const [separator, setSeparator] = useState("none");
  const [blockEmpty, setBlockEmpty] = useState("remove");
  const [autoCols, setAutoCols] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const same = slots.find((s) => s.name === name.trim() && s.kind !== "repeat" && s.kind !== "region");

  const submit = async () => {
    if (!name.trim()) {
      setErr(tr("请填写字段名称"));
      return;
    }
    const opt = options.find((o) => o.kind === kind);
    if (!opt) return;
    if (kind === "pdf_repeat" && (!layout || layoutInfo.error)) {
      setErr(layoutInfo.error || tr("明细区域还在识别中"));
      return;
    }
    const req: any = { ...opt.req, name: name.trim(), required };
    if (req.kind === "repeat_block") {
      req.separator = separator;
      req.empty_policy = blockEmpty;
      req.auto_columns = autoCols;
    }
    if (req.kind === "pdf_repeat") {
      req.layout = layout;
      req.overflow = overflow;
      req.empty_policy = pdfEmpty;
      req.min_font_size = minFont;
      if (fontSize) req.font_size = Number(fontSize);
    }
    if (req.kind === "text" || req.kind === "cell") req.mode = mode;
    if (req.kind === "repeat") {
      req.empty_policy = emptyPolicy;
      if (tpl.format === "docx") req.header_rows = headerRows;
    }
    if (req.kind === "pdf_box") {
      if (fontSize) req.font_size = Number(fontSize);
      req.min_font_size = minFont;
      req.align = align;
    }
    if (same && share && (req.kind === "text" || req.kind === "cell" || req.kind === "pdf_box" || req.kind === "pptx_text")) req.share_binding_with = same.id;
    setBusy(true);
    setErr(null);
    try {
      await onCreate(req);
      onClose();
    } catch (e: any) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!options.length) {
    return (
      <Modal title={tr("设为待填区域")} onClose={onClose} size="narrow" footer={<><span className="spacer" /><button className="btn" onClick={onClose}>{tr("关闭")}</button></>}>
        <div className="notice warn">{tr("当前选择（")}{describeSelection(sel)}{tr("）不能设为字段。请选择文字、单元格、表格行或框选区域。")}</div>
      </Modal>
    );
  }

  return (
    <Modal title={tr("设为待填区域")} onClose={onClose} size={kind === "pdf_repeat" ? "wide" : undefined}
      footer={<>{err && <div className="notice err grow">{serverText(err)}</div>}<span className="spacer" /><button className="btn" onClick={onClose}>{tr("取消")}</button><button className="btn primary" disabled={busy} onClick={submit}>{busy ? tr("创建中…") : tr("创建字段")}</button></>}>
      <div className="col gap12">
        <div className="notice">{tr("选择内容：")}{describeSelection(sel)}</div>
        {options.length === 1 && (
          <div className="kindline"><b>{options[0].label}</b><span className="small muted">{options[0].hint}</span></div>
        )}
        {options.length > 1 && (
          <div className="col gap4">
            {options.map((o) => (
              <label key={o.kind} className={`opt${kind === o.kind ? " on" : ""}`}>
                <input type="radio" checked={kind === o.kind} onChange={() => setKind(o.kind)} />
                <div><div className="t">{o.label}</div><div className="small muted">{o.hint}</div></div>
              </label>
            ))}
          </div>
        )}
        <label className="field">{tr("字段名称")}<input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("例如：客户名称")} data-autofocus
            onKeyDown={(e) => {
              // Enter that confirms a pinyin candidate must not create the field with half a name.
              if (isImeKey(e)) return;
              if (e.key === "Enter" && !busy) { e.preventDefault(); submit(); }
            }} />
        </label>
        {same && (kind === "text" || kind === "cell" || kind === "pdf_box" || kind === "pptx_text") && (
          <label className="check notice info">
            <input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} />{tr("已有同名字段「{v1}」：与它填入相同的值", { v1: same.name })}</label>
        )}
        {(kind === "text" && (sel.kind === "docx_text" || sel.kind === "docx_cell" || sel.kind === "pptx_text")) && (
          <div className="seg">
            <button type="button" aria-pressed={mode === "replace"} className={mode === "replace" ? "on" : ""} onClick={() => setMode("replace")} disabled={sel.kind === "docx_text" && sel.start === sel.end}>{tr("替换选中内容（默认）")}</button>
            <button type="button" aria-pressed={mode === "insert"} className={mode === "insert" ? "on" : ""} onClick={() => setMode("insert")}>{tr("保留原文，在")}{sel.kind === "docx_text" && sel.start !== sel.end ? tr("选区末尾") : tr("此处")}{tr("插入")}</button>
          </div>
        )}
        {kind === "cell" && tpl.format === "xlsx" && (
          <label className="field">{tr("写入方式")}<select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="replace">{tr("替换单元格内容（默认）")}</option>
              <option value="insert_after">{tr("保留原文，在其后追加")}</option>
              <option value="insert_before">{tr("保留原文，在其前插入")}</option>
            </select>
          </label>
        )}
        {kind === "repeat" && !pdfLike(tpl.format) && (
          <div className="grid2">
            {tpl.format === "docx" && sel.kind === "docx_row" && (
              <label className="field">{tr("上方表头行数（跨页时重复显示）")}<input className="input" type="number" min={0} max={sel.row} value={headerRows} onChange={(e) => setHeaderRows(Number(e.target.value))} />
              </label>
            )}
            <label className="field">{tr("零条明细时")}<select className="input" value={emptyPolicy} onChange={(e) => setEmptyPolicy(e.target.value)}>
                <option value="remove_row">{tr("删除明细行，保留表头")}</option>
                <option value="blank_row">{tr("保留一行空白")}</option>
                <option value="placeholder">{tr("保留一行并显示「（无）」")}</option>
                {tpl.format === "docx" && <option value="remove_table">{tr("删除整个表格")}</option>}
                <option value="error">{tr("视为错误（阻止该样本）")}</option>
              </select>
            </label>
          </div>
        )}
        {kind === "repeat_block" && (
          <div className="col gap8">
            <label className="check"><input type="checkbox" checked={autoCols} onChange={(e) => setAutoCols(e.target.checked)} />{tr(" 自动把「标签：值」中的值设为明细列（如「品名：示例商品」→ 列「品名」）")}</label>
            <div className="grid2">
              <label className="field">{tr("相邻两份之间")}<select className="input" value={separator} onChange={(e) => setSeparator(e.target.value)}>
                  <option value="none">{tr("直接相连")}</option><option value="blank">{tr("空一行")}</option><option value="page_break">{tr("分页（每条明细一页）")}</option>
                </select>
              </label>
              <label className="field">{tr("零条明细时")}<select className="input" value={blockEmpty} onChange={(e) => setBlockEmpty(e.target.value)}>
                  <option value="remove">{tr("删除整个内容块")}</option><option value="placeholder">{tr("保留一份，第一列显示「（无）」")}</option><option value="error">{tr("视为错误（阻止该样本）")}</option>
                </select>
              </label>
            </div>
            <div className="tiny muted">{tr("创建后可在块内继续划选文字，选「作为内容块的一列」添加列；块内的其他字段每一份填入相同的值。")}</div>
          </div>
        )}
        {kind === "pdf_repeat" && sel.kind === "pdf_region" && (
          <>
            <PdfRepeatEditor templateId={tpl.id} page={sel.page} nrect={sel.nrect} onChange={(l, info) => { setLayout(l); setLayoutInfo(info); }} />
            <div className="grid3">
              <label className="field">{tr("明细超过 ")}{layoutInfo.capacity || "?"}{tr(" 行时")}<select className="input" value={overflow} onChange={(e) => setOverflow(e.target.value)}>
                  <option value="error">{tr("视为错误（该样本不生成）")}</option>
                  {tpl.format === "pdf" && <option value="continue">{tr("复制本页续写（页上其他字段同样填写）")}</option>}
                  <option value="shrink">{tr("压缩行高放入（最多压缩到 50%）")}</option>
                </select>
              </label>
              <label className="field">{tr("零条明细时")}<select className="input" value={pdfEmpty} onChange={(e) => setPdfEmpty(e.target.value)}>
                  <option value="blank">{tr("区域留空")}</option>
                  <option value="placeholder">{tr("第一行显示「（无）」")}</option>
                  <option value="error">{tr("视为错误")}</option>
                </select>
              </label>
              <label className="field">{tr("字号（留空=沿用原文字号）")}<input className="input" type="number" value={fontSize} onChange={(e) => setFontSize(e.target.value ? Number(e.target.value) : "")} /></label>
            </div>
            <div className="tiny muted">{tr("区域内原有文字会被删除，表格线保留；放不下时逐步缩小字号，仍放不下则该样本报错，不会截断。")}</div>
          </>
        )}
        {kind === "pdf_box" && (
          <div className="grid3">
            <label className="field">{tr("字号（留空=沿用原文字号）")}<input className="input" type="number" value={fontSize} onChange={(e) => setFontSize(e.target.value ? Number(e.target.value) : "")} /></label>
            <label className="field">{tr("最小字号")}<input className="input" type="number" value={minFont} onChange={(e) => setMinFont(Number(e.target.value))} /></label>
            <label className="field">{tr("对齐")}<select className="input" value={align} onChange={(e) => setAlign(e.target.value)}><option value="left">{tr("左")}</option><option value="center">{tr("中")}</option><option value="right">{tr("右")}</option></select></label>
            <div className="tiny muted" style={{ gridColumn: "1 / -1" }}>{tpl.format === "pdf" && tpl.info?.pdf?.kind !== "scanned"
              ? tr("框内原有文字会被删除，不是遮盖。")
              : tr("框内原有内容按周围背景重建；文字颜色与字号沿用原内容。")}{tr("新内容放不下时逐步缩小到最小字号，仍放不下则报错，不会截断。")}</div>
          </div>
        )}
        {!["repeat", "repeat_block", "pdf_repeat", "block_column", "region"].includes(kind) && (
          <label className="check"><input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />{tr(" 必填（值为空时阻止生成该样本）")}</label>
        )}
      </div>
    </Modal>
  );
}

function kindOptions(format: string, sel: Selection, slots: Slot[]): { kind: string; label: string; hint: string; req: any }[] {
  if (format === "docx") {
    if (sel.kind === "docx_blocks") return [{ kind: "repeat_block", label: tr("重复内容块"), hint: tr("这 {v1} 个段落作为一个整体：每条明细复制一份（保留格式、编号和图片）", { v1: sel.count }), req: { kind: "repeat_block", part: sel.part, first: sel.first, last: sel.last } }];
    if (sel.kind === "docx_text") {
      const text = { kind: "text", label: tr("文字字段"), hint: sel.start === sel.end ? tr("在光标处插入字段") : tr("选中的文字将变成字段（可跨多个格式片段）"), req: { kind: "text", part: sel.part, path: sel.path, start: sel.start, end: sel.end } };
      const blk = sel.blk ? slots.find((s) => s.id === sel.blk) : undefined;
      if (blk && sel.start !== sel.end) return [
        { kind: "block_column", label: tr("作为内容块「{v1}」的一列", { v1: blk.name }), hint: tr("每一份中这里填入该条明细对应列的值"), req: { kind: "block_column", block_id: blk.id, part: sel.part, path: sel.path, start: sel.start, end: sel.end } },
        { ...text, hint: tr("每一份中这里都填入同一个值（不随明细变化）") },
      ];
      return [text];
    }
    if (sel.kind === "docx_cell") return [{ kind: "text", label: tr("单元格字段"), hint: tr("整个单元格的文字作为字段"), req: { kind: "cell", part: sel.part, table_path: sel.table_path, row: sel.row, col: sel.col } }];
    if (sel.kind === "docx_row") return [{ kind: "repeat", label: tr("重复明细行"), hint: tr("每条明细复制一次该行，保留该行样式；每个单元格成为一个明细列"), req: { kind: "repeat", part: sel.part, table_path: sel.table_path, row: sel.row } }];
    if (sel.kind === "docx_table" && sel.r1 !== undefined) return [{ kind: "region", label: tr("固定区域"), hint: tr("{v1} 行 × {v2} 列，按行列一一对应填入", { v1: sel.r2! - sel.r1 + 1, v2: sel.c2! - sel.c1! + 1 }), req: { kind: "region", part: sel.part, table_path: sel.table_path, r1: sel.r1, c1: sel.c1, r2: sel.r2, c2: sel.c2 } }];
  }
  if (format === "xlsx" && sel.kind === "cells") {
    const single = sel.r1 === sel.r2 && sel.c1 === sel.c2;
    if (single) return [{ kind: "cell", label: tr("单元格字段"), hint: cellRef(sel.r1, sel.c1), req: { kind: "cell", sheet: sel.sheet, cell: cellRef(sel.r1, sel.c1) } }];
    const out = [{ kind: "region", label: tr("固定区域"), hint: tr("{v1}：{v2} 行 × {v3} 列，按行列一一对应", { v1: rangeRef(sel.r1, sel.c1, sel.r2, sel.c2), v2: sel.r2 - sel.r1 + 1, v3: sel.c2 - sel.c1 + 1 }), req: { kind: "region", sheet: sel.sheet, range: rangeRef(sel.r1, sel.c1, sel.r2, sel.c2) } }];
    if (sel.r1 === sel.r2) out.unshift({ kind: "repeat", label: tr("重复明细行"), hint: tr("第 {v1} 行的 {v2} 为明细模板行：有几条明细就插入几行（公式、合并单元格和条件格式随之平移）", { v1: sel.r1 + 1, v2: rangeRef(sel.r1, sel.c1, sel.r2, sel.c2) }), req: { kind: "repeat", sheet: sel.sheet, row: sel.r1, c1: sel.c1, c2: sel.c2 } as any });
    return out;
  }
  if (pdfLike(format)) {
    if (sel.kind === "pdf_region") return [
      { kind: "pdf_box", label: tr("PDF 区域字段（单个值）"), hint: tr("删除框内原文字，生成时写入新值"), req: { kind: "pdf_box", page: sel.page, nrect: sel.nrect } },
      { kind: "pdf_repeat", label: tr("重复明细区域（多行）"), hint: tr("框选明细表的数据行（不含表头）：每条明细写入一行，自动识别列和行高，可拖动调整"), req: { kind: "pdf_repeat", page: sel.page, nrect: sel.nrect } },
    ];
    if (sel.kind === "pdf_field") return [{ kind: "pdf_field", label: tr("PDF 表单字段"), hint: tr("使用 PDF 已定义的表单字段"), req: { kind: "pdf_field", field: sel.field } }];
  }
  if (format === "pptx") {
    if (sel.kind === "pptx_text") return [{ kind: "text", label: tr("文字字段"), hint: tr("文本框中选中的文字"), req: { kind: "text", slide: sel.slide, shape: sel.shape, para: sel.para, start: sel.start, end: sel.end } }];
    if (sel.kind === "pptx_cell") return [{ kind: "cell", label: tr("表格单元格"), hint: "", req: { kind: "cell", slide: sel.slide, shape: sel.shape, row: sel.row, col: sel.col } }];
    if (sel.kind === "pptx_row") return [{ kind: "repeat", label: tr("重复明细行"), hint: tr("按明细条数复制该表格行（幻灯片不会自动分页）"), req: { kind: "repeat", slide: sel.slide, shape: sel.shape, row: sel.row } }];
  }
  return [];
}

export function suggestName(sel: Selection, ctxText?: string): string {
  if (sel.kind === "docx_text" && ctxText) {
    const before = ctxText.slice(0, sel.start);
    const m = /([^\s：:，,。；;]{1,12})[：:]\s*$/.exec(before);
    if (m) return m[1];
    if (sel.text && sel.text.length <= 12) return sel.text.trim();
  }
  if (sel.kind === "docx_cell" && sel.text) return "";
  return "";
}

export type { Slot };
