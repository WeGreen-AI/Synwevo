export type WS = { id: string; name: string; role: string };
export type Values = Record<string, string | null>;
export interface DataRow { values: Values; origins: [string, number][]; origin_count?: number }
export interface Dataset { id: string; name: string; columns: string[]; row_count: number; column_count: number; origin: string; created_at: number; read_notes: string[]; source: any; issue_count?: number }
export interface Step { id: string; type: string; [key: string]: any }
export interface Definition { source: string; parameters: Values; steps: Step[] }
export interface Recipe { id: string; name: string; description: string; draft: Definition; draft_rev: number; status: string; published_version: number | null;
  versions: { version: number; name: string; status: string; definition?: Definition; published_at: number; note: string }[] }
export interface Run { id: string; recipe_id: string; recipe_version: number; status: string; output_name: string; output_dataset_id: string | null; created_at: number;
  cancel_requested: number; error_code: string | null; inputs: Record<string, { dataset_id: string; content_hash: string }>; params: Values;
  progress: { step?: number; processed?: number; total?: number }; counts: { output_rows: number; issues: number } | null;
  reports: { step_id: string; input_rows: number; output_rows: number; issues: number }[] | null; issue_total: number; attempts: number }
export interface Issue { code: string; source_row?: number; input_name?: string; column_name?: string; step_id?: string; value?: string; message?: string }
export interface Page<T> { items: T[]; next_offset: number | null; total?: number }
export const stepNames: Record<string, string> = { trim: "清理空白", cast: "类型转换", split: "拆分字段", merge: "合并字段", filter: "过滤", sort: "排序", dedupe: "去重", append: "追加数据", join: "关联匹配", aggregate: "分组汇总", validate: "规则校验" };
export const typeNames: Record<string, string> = { text: "文本", integer: "整数", decimal: "金额", date: "日期", datetime: "日期时间", boolean: "布尔值" };
export function newStep(type: string): Step {
  const id = `s_${Math.random().toString(36).slice(2, 10)}`;
  const predicate = { column: "", op: "eq", value: "", numeric: false };
  const defaults: Record<string, object> = {
    trim: { columns: [] }, cast: { column: "", to: "decimal", scale: 2, rounding: "reject", offset: null },
    split: { column: "", into: ["part_1", "part_2"], separator: " " }, merge: { columns: [], target: "combined", separator: " ", nulls: "propagate" },
    filter: { condition: predicate }, sort: { columns: [], numeric: false, descending: false }, dedupe: { columns: [], keep: "first" },
    append: { source: "other", alignment: "strict" }, join: { source: "other", left: [], right: [], how: "left", matches: "one", prefix: "right_", nulls_equal: false },
    aggregate: { group_by: [], measures: [{ op: "count", column: null, target: "count" }] },
    validate: { condition: predicate, message: "", on_error: "keep" },
  };
  return { id, type, ...defaults[type] };
}
