import { serverText, issueText, diagnosticText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import type { Mark, Selection } from "../lib/types";
import DocView from "../views/DocView";
import GridView, { CellSel, GridWindow } from "../views/GridView";
import PdfView from "../views/PdfView";
import SlidesView from "../views/SlidesView";
import TextView from "../views/TextView";

export interface ViewProps {
  view: any;
  side: "src" | "tpl";
  reloadKey: string;
  loadGrid: (sheet: string | null, offset: number, limit: number) => Promise<GridWindow>;
  pageUrl?: (page: number) => string;
  wordsUrl?: (page: number) => string;
  marks: Mark[];
  selection: Selection | null;
  onSelect: (s: Selection | null) => void;
  onMarkClick?: (id: string) => void;
  zoom: number;
  sheet: string | null;
  onSheet: (s: string) => void;
  page: number;
  onPage: (n: number) => void;
  headerRow?: { sheet: string; row: number } | null;
  gridFocus?: { sheet: string; r: number; c: number; key: number } | null;
  focusKey?: string | null;
  slotNames?: Record<string, { name: string; color: string; no?: number }>;
  onGridInfo?: (i: any) => void;
  /** False for read-only surfaces such as the preview. */
  selectable?: boolean;
}

export default function AnyView(p: ViewProps) {
  useI18n();
  const v = p.view;
  if (!v) return <div className="empty">{tr("加载中…")}</div>;
  if (v.view === "pending") {
    const scanning = v.status === "scanning";
    const held = v.status === "scan_failed";
    return <div className="empty" role="status"><div className="big">{tr(held ? "文件尚未通过安全检查" : scanning ? "正在安全检查…" : "正在解析文件…")}</div>
      <div style={{ maxWidth: 480 }}>{held ? diagnosticText(v.error) : tr(scanning ? "检查通过后才会解析并显示内容" : "解析完成后自动显示")}</div></div>;
  }
  if (v.view === "error") return (
    <div className="empty">
      <div className="big">{tr("无法读取此文件")}</div>
      <div className="notice err" style={{ maxWidth: 480 }}>{diagnosticText(v.error)}</div>
    </div>
  );
  if (v.view === "grid") {
    const sel = p.selection?.kind === "cells" ? (p.selection as CellSel) : null;
    return (
      <GridView load={p.loadGrid} reloadKey={p.reloadKey} marks={p.marks} selection={sel} onSelect={(s) => p.onSelect(s ? { kind: "cells", ...s } : null)}
        onMarkClick={p.onMarkClick} headerRow={p.headerRow} focus={p.gridFocus} sheet={p.sheet} onSheet={p.onSheet} zoom={p.zoom} onInfo={p.onGridInfo}
        selectable={p.selectable !== false} />
    );
  }
  if (v.view === "docx") return <DocView doc={v.doc} marks={p.marks} selection={p.selection} onSelect={p.onSelect} onMarkClick={p.onMarkClick} side={p.side} zoom={p.zoom} slotNames={p.slotNames} focusKey={p.focusKey} selectable={p.selectable} />;
  if (v.view === "pdf" || v.view === "image") {
    const pages = v.view === "pdf" ? v.pdf.pages : [{ index: 0, w: (v.image?.width || 800) * 0.75, h: (v.image?.height || 1000) * 0.75, rotation: 0, kind: "scanned" }];
    const sel = p.selection?.kind === "pdf_region" ? p.selection : null;
    return (
      <PdfView pages={pages} imageUrl={p.pageUrl!} wordsUrl={p.wordsUrl} marks={p.marks} selection={sel} onSelect={(s) => p.onSelect(s)} onMarkClick={p.onMarkClick}
        zoom={p.zoom} focusKey={p.focusKey} reloadKey={p.reloadKey} page={p.page} onPage={p.onPage} selectable={p.selectable !== false} />
    );
  }
  if (v.view === "slides") return <SlidesView data={v.slides} marks={p.marks} selection={p.selection} onSelect={p.onSelect} side={p.side} zoom={p.zoom} slotNames={p.slotNames} focusKey={p.focusKey} onMarkClick={p.onMarkClick} selectable={p.selectable} />;
  if (v.view === "text") {
    return <TextView lines={v.lines} marks={p.marks} selection={p.selection} onSelect={p.onSelect} focusKey={p.focusKey} selectable={p.selectable} />;
  }
  return <div className="empty">{tr("无法预览")}</div>;
}
