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
/** `input_name` is the recorded source (an uploaded file's `dpf_…` id, a submission or task id, or
 *  "records"); `input_label` is the file name the server resolves for people, null otherwise. */
export interface Issue { code: string; source_row?: number; input_name?: string; input_label?: string | null; column_name?: string; step_id?: string; value?: string; message?: string }
export interface Page<T> { items: T[]; next_offset: number | null; total?: number; hidden?: number }
export const stepNames: Record<string, string> = { trim: "清理空白", cast: "类型转换", split: "拆分字段", merge: "合并字段", filter: "过滤", sort: "排序", dedupe: "去重", append: "追加数据", join: "关联匹配", aggregate: "分组汇总", validate: "规则校验" };
/** "3. 类型转换" for each step id: where an issue or report came from, labelled from the definition
 *  that produced it (the pinned version for a run, the previewed draft in the editor). */
export function stepLabels(steps: Step[], t: (key: string) => string): Record<string, string> {
  return Object.fromEntries(steps.map((step, index) => [step.id, `${index + 1}. ${t(stepNames[step.type] || "步骤")}`]));
}
/** The editor's caption for each recipe field a refusal can point at (path after the step type). */
const draftFields: Record<string, string> = {
  name: "方案名称", description: "方案说明", "definition.source": "主来源名称", "definition.parameters": "方案参数",
  columns: "选择列", column: "列", "condition.column": "列", "condition.op": "条件", "condition.value": "比较值（$名称引用参数）",
  to: "目标类型", scale: "小数位数", rounding: "精度处理", offset: "无时区时间的固定偏移", into: "输出列（每行一个）",
  separator: "分隔文本", target: "输出列名", nulls: "空值处理", keep: "保留规则", source: "追加或关联来源", alignment: "列对齐",
  left: "本表关联键", right: "来源关联键", how: "关联方式", matches: "重复匹配", prefix: "来源列前缀",
  group_by: "分组列（可留空）", measures: "汇总指标", "measures.op": "汇总方式", "measures.column": "数值列",
  "measures.target": "输出列名", message: "异常说明", on_error: "异常行处理",
};
const unfilled = new Set(["missing", "string_too_short", "too_short"]);
/** What to fix in a draft the server refused as invalid_input: one line per step and field, from the
 *  refusal's `detail.fields[].loc` and the steps that were sent. A location the page cannot place
 *  (a recipe-wide rule, an unknown field) adds no line; the general message still shows. */
export function draftProblems(detail: unknown, steps: Step[], t: (key: string, params?: Record<string, string | number>) => string): string[] {
  const fields = (detail as { fields?: { loc?: unknown; type?: unknown }[] } | null)?.fields;
  if (!Array.isArray(fields)) return [];
  const labels = stepLabels(steps, t), lines: string[] = [];
  for (const f of fields) {
    const loc = Array.isArray(f?.loc) ? f.loc.map(String) : [], empty = unfilled.has(String(f?.type));
    let line: string | null = null;
    if (loc[0] === "definition" && loc[1] === "steps") {
      const step = steps[Number(loc[2])], label = step && labels[step.id];
      if (!label) continue;
      // Drop the step type tag and list positions: `measures.0.column` is the measure's `column`.
      const path = loc.slice(4).filter(p => !/^\d+$/.test(p)).join("."), rule = f?.type === "value_error";
      // Join and measure have one cross-field rule each: equal key counts, a column for sum/min/max.
      if (rule && path === "" && step.type === "join") line = t("步骤 {step}：本表关联键与来源关联键的数量必须相同", { step: label });
      else if (rule && path === "measures") line = t("步骤 {step}：请填写「{field}」", { step: label, field: t("数值列") });
      else if (draftFields[path]) line = t(empty ? "步骤 {step}：请填写「{field}」" : "步骤 {step}：「{field}」的值无效", { step: label, field: t(draftFields[path]) });
      else line = t("步骤 {step}：参数不完整或不一致", { step: label });
    } else {
      const field = draftFields[loc.slice(0, 2).join(".")] ?? draftFields[loc[0] ?? ""];
      if (field) line = t(empty ? "请填写「{field}」" : "「{field}」的值无效", { field: t(field) });
    }
    if (line && !lines.includes(line)) lines.push(line);
  }
  return lines;
}
/** A refusal's wording when its detail says where to look: an unreadable CSV record names the
 *  physical line it starts on. Null keeps the general wording for the code. */
export function refusalWithPlace(code: string, detail: unknown): [string, { line: number }] | null {
  const line = (detail as { line?: unknown } | null)?.line;
  if (code === "invalid_csv" && typeof line === "number" && Number.isInteger(line) && line > 0)
    return ["文件结构无效：第 {line} 行开始的记录无法读取，请检查引号是否成对", { line }];
  return null;
}
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
