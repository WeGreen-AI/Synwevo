import { t as tr } from "@synwevo/ui/i18n-core";
import type { Mapping, Pos, Selection, Slot } from "./types";
import { cellRef, rangeRef } from "@synwevo/ui";

export const MAP_TYPE: Record<string, string> = { single: "单值", region: "固定区域", repeat: "重复明细" };
export const SLOT_KIND: Record<string, string> = { text: "文字", cell: "单元格", region: "固定区域", repeat: "重复明细行", pdf_box: "PDF 区域", pdf_field: "PDF 表单字段", pptx_text: "幻灯片文字" };
// Sample-rule mode names live in status.ts (RULE_MODE / ruleMode): the copy that was here had no
// per_block, so 「每块一份」 projects showed a blank label in the top bar and the check panel.

export function describeLocator(loc: any): string {
  if (!loc) return "—";
  switch (loc.kind) {
    case "column":
      return tr("列「{v1}」{v2}", { v1: loc.header, v2: loc.occurrence ? tr("（第 {v1} 个同名列）", { v1: loc.occurrence }) : "" });
    case "cell":
      return `${loc.sheet}!${loc.cell}${loc.anchor ? tr("（标签「{v1}」）", { v1: loc.anchor.text }) : ""}`;
    case "range":
      return `${loc.sheet}!${loc.range}`;
    case "table":
      return tr("{v1} 明细表（表头第 {v2} 行{v3}）", { v1: loc.sheet, v2: loc.header_row + 1, v3: loc.data_end != null ? tr("，至第 {v1} 行", { v1: loc.data_end + 1 }) : tr("，至末尾") });
    case "docx_text":
      return tr("文档文字 {v1}", { v1: loc.prefix ? tr("「…{v1}」后", { v1: loc.prefix.slice(-8) }) : tr("段首") });
    case "docx_cell":
      return tr("文档表格单元格{v1}", { v1: loc.label ? `（「${loc.label.text}」${loc.label.where === "left" ? tr("右侧") : tr("下方")}）` : tr(" 第{v1}行第{v2}列", { v1: loc.row + 1, v2: loc.col + 1 }) });
    case "docx_table":
      return tr("文档表格明细（表头：{v1}）", { v1: (loc.headers || []).slice(0, 4).join("/") });
    case "docx_table_block":
      return tr("文档表格区域");
    case "pdf_region":
      return tr("PDF 第 {v1} 页区域{v2}", { v1: loc.page + 1, v2: loc.anchor ? tr("（标签「{v1}」）", { v1: loc.anchor.text }) : "" });
    case "ocr_region":
      return tr("扫描第 {v1} 页区域（OCR）", { v1: loc.page + 1 });
    case "text_span":
      return tr("第 {v1} 行文字", { v1: loc.line + 1 });
    case "pptx_text":
      return tr("第 {v1} 张幻灯片「{v2}」", { v1: loc.slide + 1, v2: loc.shape_name || loc.shape });
    case "constant":
      return tr("固定文字「{v1}」", { v1: loc.value });
    case "record_meta":
      return { file_name: tr("来源文件名"), record_index: tr("样本序号"), group_key: tr("分组编号"), sheet: tr("工作表名") }[loc.field as string] || loc.field;
    default:
      return loc.kind;
  }
}

export function variesText(loc: any, mode?: string): string {
  if (!loc) return "";
  if (loc.kind === "constant") return tr("固定值");
  if (loc.kind === "column") return mode === "per_group" ? tr("随分组变化") : tr("随样本变化");
  if (loc.kind === "record_meta") return tr("随样本变化");
  if (mode === "per_row" || mode === "per_group") return loc.kind === "cell" || loc.kind === "range" ? tr("固定值（同一文件内相同）") : tr("随文件变化");
  return tr("随文件变化");
}

export function slotPos(s: Slot, format: string): Pos | null {
  const loc = s.locator || {};
  if (format === "xlsx") {
    if (s.kind === "cell") {
      const m = /([A-Z]+)(\d+)/.exec(loc.cell);
      if (!m) return null;
      const c = colIdx(m[1]), r = Number(m[2]) - 1;
      return { kind: "cells", sheet: loc.sheet, r1: r, c1: c, r2: r, c2: c };
    }
    if (s.kind === "region") {
      const [a, b] = String(loc.range).split(":");
      const p1 = /([A-Z]+)(\d+)/.exec(a)!, p2 = /([A-Z]+)(\d+)/.exec(b || a)!;
      return { kind: "cells", sheet: loc.sheet, r1: Number(p1[2]) - 1, c1: colIdx(p1[1]), r2: Number(p2[2]) - 1, c2: colIdx(p2[1]) };
    }
    // a repeat content block covers whole rows; the grid clips the last column to the sheet
    if (s.kind === "repeat" && s.block) return { kind: "cells", sheet: loc.sheet, r1: loc.first, c1: 0, r2: loc.last, c2: 16383 };
    if (s.kind === "repeat") return { kind: "cells", sheet: loc.sheet, r1: loc.row, c1: loc.c1 ?? 0, r2: loc.row, c2: loc.c2 ?? 0 };
  }
  if (format === "docx") return { kind: "field", field: s.id };
  if (pdfLike(format) && s.nrect) return { kind: "rect", page: loc.page, nrect: s.nrect, grid: s.ngrid };
  if (format === "pptx") return { kind: "pptx", slide: loc.slide, shape: String(loc.shape), para: loc.para, start: loc.start, end: loc.end, row: loc.row, col: loc.col };
  return null;
}

/** templates edited as page images with drawn boxes: PDF and image (PNG / JPEG) templates */
export function pdfLike(format: string | undefined | null): boolean {
  return format === "pdf" || format === "png" || format === "jpeg";
}

export function colIdx(letters: string) {
  let n = 0;
  for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}

export function describeSelection(s: Selection | null): string {
  if (!s) return "";
  switch (s.kind) {
    case "cells":
      if (s.rows) return `${s.sheet}!` + (s.r1 === s.r2 ? tr("第 {v1} 行（整行）", { v1: s.r1 + 1 }) : tr("第 {v1}–{v2} 行（整行）", { v1: s.r1 + 1, v2: s.r2 + 1 }));
      return `${s.sheet}!${rangeRef(s.r1, s.c1, s.r2, s.c2)}` + (s.r1 !== s.r2 || s.c1 !== s.c2 ? tr("（{v1} 行 × {v2} 列）", { v1: s.r2 - s.r1 + 1, v2: s.c2 - s.c1 + 1 }) : "");
    case "docx_text":
      return s.start === s.end ? tr("插入位置（光标）") : tr("文字「{v1}」", { v1: s.text.length > 24 ? s.text.slice(0, 24) + "…" : s.text });
    case "docx_blocks":
      return tr("连续 {v1} 个段落「{v2}」", { v1: s.count, v2: s.text.length > 24 ? s.text.slice(0, 24) + "…" : s.text });
    case "docx_cell":
      return tr("表格单元格（第 {v1} 行第 {v2} 列）", { v1: s.row + 1, v2: s.col + 1 });
    case "docx_row":
      return tr("表格第 {v1} 行", { v1: s.row + 1 });
    case "docx_table":
      return s.r1 !== undefined ? tr("表格区域 第{v1}-{v2}行 × 第{v3}-{v4}列", { v1: s.r1 + 1, v2: s.r2! + 1, v3: s.c1! + 1, v4: s.c2! + 1 }) : tr("整个表格（表头 {v1} 行）", { v1: s.header_rows ?? 1 });
    case "pdf_region":
      return tr("第 {v1} 页框选区域", { v1: s.page + 1 });
    case "pdf_field":
      return tr("表单字段「{v1}」", { v1: s.field });
    case "text_span":
      return tr("第 {v1} 行「{v2}」", { v1: s.line + 1, v2: s.text.slice(0, 20) });
    case "pptx_text":
      return tr("幻灯片 {v1} 文字「{v2}」", { v1: s.slide + 1, v2: s.text.slice(0, 20) });
    case "pptx_cell":
      return tr("幻灯片 {v1} 表格单元格", { v1: s.slide + 1 });
    case "pptx_row":
      return tr("幻灯片 {v1} 表格第 {v2} 行", { v1: s.slide + 1, v2: s.row + 1 });
    case "slot":
      return tr("字段");
  }
}

export function mappingSlots(m: Mapping, slots: Slot[]) {
  const b = m.target?.binding || m.target?.slot_id;
  return slots.filter((s) => (s.binding || s.id) === b);
}

export { cellRef };
