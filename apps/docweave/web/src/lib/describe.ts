import type { Mapping, Pos, Selection, Slot } from "./types";
import { cellRef, rangeRef } from "./ui";

export const MAP_TYPE: Record<string, string> = { single: "单值", region: "固定区域", repeat: "重复明细" };
export const SLOT_KIND: Record<string, string> = { text: "文字", cell: "单元格", region: "固定区域", repeat: "重复明细行", pdf_box: "PDF 区域", pdf_field: "PDF 表单字段", pptx_text: "幻灯片文字" };
export const MODE_NAME: Record<string, string> = { per_row: "每行一份", per_file: "每文件一份", per_group: "每组一份" };

export function describeLocator(loc: any): string {
  if (!loc) return "—";
  switch (loc.kind) {
    case "column":
      return `列「${loc.header}」${loc.occurrence ? `（第 ${loc.occurrence} 个同名列）` : ""}`;
    case "cell":
      return `${loc.sheet}!${loc.cell}${loc.anchor ? `（标签「${loc.anchor.text}」）` : ""}`;
    case "range":
      return `${loc.sheet}!${loc.range}`;
    case "table":
      return `${loc.sheet} 明细表（表头第 ${loc.header_row + 1} 行${loc.data_end != null ? `，至第 ${loc.data_end + 1} 行` : "，至末尾"}）`;
    case "docx_text":
      return `文档文字 ${loc.prefix ? `「…${loc.prefix.slice(-8)}」后` : "段首"}`;
    case "docx_cell":
      return `文档表格单元格${loc.label ? `（「${loc.label.text}」${loc.label.where === "left" ? "右侧" : "下方"}）` : ` 第${loc.row + 1}行第${loc.col + 1}列`}`;
    case "docx_table":
      return `文档表格明细（表头：${(loc.headers || []).slice(0, 4).join("/")}）`;
    case "docx_table_block":
      return "文档表格区域";
    case "pdf_region":
      return `PDF 第 ${loc.page + 1} 页区域${loc.anchor ? `（标签「${loc.anchor.text}」）` : ""}`;
    case "ocr_region":
      return `扫描第 ${loc.page + 1} 页区域（OCR）`;
    case "text_span":
      return `第 ${loc.line + 1} 行文字`;
    case "pptx_text":
      return `第 ${loc.slide + 1} 张幻灯片「${loc.shape_name || loc.shape}」`;
    case "constant":
      return `固定文字「${loc.value}」`;
    case "record_meta":
      return { file_name: "来源文件名", record_index: "样本序号", group_key: "分组编号", sheet: "工作表名" }[loc.field as string] || loc.field;
    default:
      return loc.kind;
  }
}

export function variesText(loc: any, mode?: string): string {
  if (!loc) return "";
  if (loc.kind === "constant") return "固定值";
  if (loc.kind === "column") return mode === "per_group" ? "随分组变化" : "随样本变化";
  if (loc.kind === "record_meta") return "随样本变化";
  if (mode === "per_row" || mode === "per_group") return loc.kind === "cell" || loc.kind === "range" ? "固定值（同一文件内相同）" : "随文件变化";
  return "随文件变化";
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
      return `${s.sheet}!${rangeRef(s.r1, s.c1, s.r2, s.c2)}` + (s.r1 !== s.r2 || s.c1 !== s.c2 ? `（${s.r2 - s.r1 + 1} 行 × ${s.c2 - s.c1 + 1} 列）` : "");
    case "docx_text":
      return s.start === s.end ? "插入位置（光标）" : `文字「${s.text.length > 24 ? s.text.slice(0, 24) + "…" : s.text}」`;
    case "docx_blocks":
      return `连续 ${s.count} 个段落「${s.text.length > 24 ? s.text.slice(0, 24) + "…" : s.text}」`;
    case "docx_cell":
      return `表格单元格（第 ${s.row + 1} 行第 ${s.col + 1} 列）`;
    case "docx_row":
      return `表格第 ${s.row + 1} 行`;
    case "docx_table":
      return s.r1 !== undefined ? `表格区域 第${s.r1 + 1}-${s.r2! + 1}行 × 第${s.c1! + 1}-${s.c2! + 1}列` : `整个表格（表头 ${s.header_rows ?? 1} 行）`;
    case "pdf_region":
      return `第 ${s.page + 1} 页框选区域`;
    case "pdf_field":
      return `表单字段「${s.field}」`;
    case "text_span":
      return `第 ${s.line + 1} 行「${s.text.slice(0, 20)}」`;
    case "pptx_text":
      return `幻灯片 ${s.slide + 1} 文字「${s.text.slice(0, 20)}」`;
    case "pptx_cell":
      return `幻灯片 ${s.slide + 1} 表格单元格`;
    case "pptx_row":
      return `幻灯片 ${s.slide + 1} 表格第 ${s.row + 1} 行`;
    case "slot":
      return "字段";
  }
}

export function mappingSlots(m: Mapping, slots: Slot[]) {
  const b = m.target?.binding || m.target?.slot_id;
  return slots.filter((s) => (s.binding || s.id) === b);
}

export { cellRef };
