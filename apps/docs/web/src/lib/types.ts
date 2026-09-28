export type Family = "table" | "document" | "pdf" | "image" | "slides" | "text" | "unknown";

export interface SourceFile {
  id: string;
  filename: string;
  ord: number;
  size: number;
  format: string;
  family: Family;
  /** scanning → (parsing → ready | needs_ocr | error | unsupported) | rejected | scan_failed; the
   *  malware check comes first and `scan` says what it concluded (docs/security/file-acceptance.md). */
  status: "scanning" | "parsing" | "ready" | "error" | "unsupported" | "needs_ocr" | "rejected" | "scan_failed";
  error?: string | null;
  notes: string[];
  structure: any;
  scan?: { status: "pending" | "clean" | "infected" | "error" | "too_large" | "not_configured" | "unscanned";
           signature?: string; engine?: string; reason?: string; sha256?: string; checked_at?: number;
           attempts?: number; retry_at?: number };
  version: number;
  representative: boolean;
  ocr_pages: number[];
  corrections: Record<string, { value: string; confirmed: boolean }>;
  options?: any;
}

export interface Slot {
  id: string;
  name: string;
  kind: "text" | "cell" | "region" | "repeat" | "pdf_box" | "pdf_field" | "pptx_text";
  binding: string;
  data_type: string;
  required: boolean;
  format: any[];
  default?: string | null;
  mode?: string;
  original_text?: string;
  locator: any;
  columns?: { key: string; name: string; col: number; original_text?: string }[];
  rows?: number;
  cols?: number;
  empty_policy?: string;
  placeholder?: string;
  repeat_header?: boolean;
  header_rows?: number;
  font_size?: number;
  min_font_size?: number;
  align?: string;
  nrect?: number[];
  ngrid?: { kind: "col" | "row"; r: number[] }[];
  layout?: { trot: number; trect: number[]; row_height: number; cols: { key: string; name: string; x0: number; x1: number; align: string }[] };
  overflow?: "error" | "continue" | "shrink";
  color?: string;
  clear?: string;
  capacity?: number;
  warning?: string;
  block?: boolean;
  separator?: "none" | "blank" | "page_break";
  paragraphs?: number;
}

export interface TemplateDetail {
  id: string;
  name: string;
  kind: string;
  format: "docx" | "xlsx" | "pdf" | "pptx" | "png" | "jpeg";
  original_name?: string;
  head: number;
  info?: { notes?: string[]; pdf?: any };
  version: { no: number; slots: Slot[]; note: string; editor?: any };
  can_undo: boolean;
  can_redo: boolean;
  versions: { version_no: number; parent_no: number | null; note: string; created_at: number }[];
  exports: { format: string; label: string; available: boolean; reason?: string | null; note?: string; native?: boolean }[];
  created_slot?: Slot;
  removed_mappings?: string[];
}

export interface Mapping {
  id: string;
  no: number;
  color: string;
  enabled: boolean;
  type: "single" | "region" | "repeat";
  sources: any[];
  combine?: { op: string; sep?: string };
  target: { binding: string; slot_id?: string };
  transforms?: any[];
  agg?: string;
  agg_sep?: string;
  value_mode?: string;
  style?: string;
  columns?: { slot_col: string; source: any; transforms?: any[] }[];
  overrides?: Record<string, any>;
  label?: string;
  varies?: string;
  source_file?: string;
}

export interface RecordRule {
  mode?: "per_row" | "per_file" | "per_group" | "per_block";
  block?: { anchor: string; match?: "contains" | "starts" | "exact" | "regex"; end_anchor?: string; sheet?: string };
  table?: { sheet?: string; sheet_index?: number; header_row?: number | null; data_start?: number | null; data_end?: number | null; skip_blank?: boolean; filters?: any[]; first_col?: number; last_col?: number | null };
  group?: { keys: { header: string }[]; sort?: { header: string; dir: string; numeric?: boolean }[]; scope?: "file" | "cross_file" };
  id_column?: { header: string } | null;
}

export interface CopiedFrom {
  request_id: string;
  source_project_name?: string;
  requester?: { user_id: string; name: string } | null;
  decider?: { user_id: string; name: string } | null;
  decided_at?: number | null;
  executed_at?: number | null;
  self_approved?: boolean;
  organization_id?: string;
  sources_included?: boolean;
}

export interface Project {
  id: string;
  name: string;
  template_id?: string | null;
  record_rule: RecordRule;
  output: { naming?: string; formats?: string[]; error_policy?: string; layout?: "off" | "warn" | "fit" };
  mapping_rev: number;
  rev: number;
  /** Revision of the settings (name, rule, output, template choice); sent back as
   *  `base_settings_rev` so a stale screen's save is refused instead of overwriting. */
  settings_rev: number;
  origin?: string;
  /** Present on a project created by an approved copy from a personal workspace. */
  copied_from?: CopiedFrom | null;
  sources: SourceFile[];
  template: TemplateDetail | null;
  /** The template this project pointed at was deleted: template is null, a new one must be chosen. */
  template_missing?: boolean;
  /** A rescan found the template's bytes infected (CI-90): template is null and the template is
   *  refused wherever its bytes would be used; a new one must be chosen. */
  template_rejected?: boolean;
  mappings: { rev: number; mappings: Mapping[]; can_undo: boolean; can_redo: boolean };
  jobs?: any[];
}

/** positions inside viewers */
export type Pos =
  | { kind: "cells"; sheet: string; r1: number; c1: number; r2: number; c2: number; header?: number | null }
  | { kind: "docx_text"; part?: string; path: string; start: number; end: number }
  | { kind: "docx_cell"; part?: string; table_path: string; row: number; col: number }
  | { kind: "docx_row"; part?: string; table_path: string; row: number }
  | { kind: "docx_table"; part?: string; table_path: string }
  | { kind: "field"; field: string }
  | { kind: "rect"; page: number; nrect: number[]; grid?: { kind: "col" | "row"; r: number[] }[] }
  | { kind: "text_span"; line: number; start: number; end: number }
  | { kind: "pptx"; slide: number; shape: string; para?: number | null; start?: number | null; end?: number | null; row?: number | null; col?: number | null };

export interface Mark {
  id: string;           // mapping id or slot id
  no?: number;
  color: string;
  label: string;
  pos: Pos;
  active?: boolean;
  dim?: boolean;
  role: "src" | "tgt" | "slot";
}

export type Selection =
  | { kind: "cells"; sheet: string; r1: number; c1: number; r2: number; c2: number }
  | { kind: "docx_text"; part: string; path: string; start: number; end: number; text: string; blk?: string }
  | { kind: "docx_blocks"; part: string; first: string; last: string; count: number; text: string }
  | { kind: "docx_cell"; part: string; table_path: string; row: number; col: number; text?: string }
  | { kind: "docx_row"; part: string; table_path: string; row: number }
  | { kind: "docx_table"; part: string; table_path: string; r1?: number; c1?: number; r2?: number; c2?: number; header_rows?: number }
  | { kind: "pdf_region"; page: number; nrect: number[] }
  | { kind: "pdf_field"; field: string }
  | { kind: "text_span"; line: number; start: number; end: number; text: string }
  | { kind: "pptx_text"; slide: number; shape: string; para: number; start: number; end: number; text: string }
  | { kind: "pptx_cell"; slide: number; shape: string; row: number; col: number }
  | { kind: "pptx_row"; slide: number; shape: string; row: number }
  | { kind: "slot"; slotId: string };
