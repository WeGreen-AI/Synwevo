import type { Translator } from "@synwevo/ui/i18n-core";
import type { Field } from "./types";

const CHOICE_ID = /^opt_[a-f0-9]{8}$/;
const RECORD_ID = /^rec_[a-f0-9]{12}$/;

/** A related record as people know it: by the name the page has for it. Internal ids are never
 *  shown; a record with no name the page can show reads as unavailable. */
export function relationLabel(t: Translator, id: string, titles: Record<string, string>): string {
  return Object.hasOwn(titles, id) ? titles[id] || "—" : t("（不可用的记录）");
}

/** A value in the change history, in the shape it was stored then: options by their current
 *  label, related records by the names the server resolved for this reader (`titles`). */
export function historyText(t: Translator, field: Field | undefined, v: unknown, titles: Record<string, string> = {}): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return t(v ? "是" : "否");
  if (Array.isArray(v)) return v.length ? v.map(x => historyText(t, field, x, titles)).join(", ") : "—";
  if (typeof v === "object") {
    // Historical values may predate a field type change; format their own shape.
    const money = v as {amount?: unknown; currency?: unknown};
    return typeof money.amount === "string" && typeof money.currency === "string" ? `${money.amount} ${money.currency}` : JSON.stringify(v);
  }
  const text = String(v);
  if (field && (field.type === "single_select" || field.type === "multi_select")) {
    const choice = (field.config.choices ?? []).find((c: {id: string; label: string}) => c.id === text);
    if (choice) return choice.label;
    if (CHOICE_ID.test(text)) return t("（已删除的选项）");
  }
  if (field?.type === "link" && RECORD_ID.test(text)) return relationLabel(t, text, titles);
  return text;
}

/** How Tables words a refusal by its code. Server prose is never shown as page copy. */
const errorKeys: Record<string, string> = {
  revision_conflict: "内容已被修改。请载入最新内容后重新编辑。", unique_conflict: "该值已被使用，请输入唯一值。",
  field_forbidden: "你不能读取或修改所选字段。", record_not_found: "记录不存在或你没有访问权限。",
  table_not_found: "数据表不存在或你没有访问权限。", table_archived: "数据表已归档，恢复后才能编辑。",
  field_dependencies: "有公式或保存的视图引用此字段，请先调整依赖。", preview_required: "数据已变化，请重新预览修改的影响。",
  migration_constraints: "修改不符合必填或唯一性要求，请先修复记录。", invalid_field: "字段配置或公式无效，请检查设置。",
  invalid_cell: "输入与字段类型不符，请检查数值、日期或选项。", required_cell: "请填写所有必填字段。",
  export_not_allowed: "你没有导出权限。", invalid_mapping: "请为每个来源列选择不同的目标字段。",
  import_validation: "部分行验证失败，请检查导入预览。", file_formula_not_allowed: "请将文件中的公式转为值后再导入。",
  record_read_only: "此记录只允许查看。", table_read_only: "此数据表只允许查看。",
  conversion_not_supported: "该类型不能直接转换，请创建新字段。", currency_conversion_required: "更换币种需要明确的汇率转换，请创建新字段。",
  import_row_limit: "每次最多导入 500 行和 100 列。", file_too_large: "文件超过大小限制（2 MB）。",
  import_headers: "首行需要唯一且非空的列名。", invalid_file: "无法读取文件，请选择有效的 UTF-8 CSV 或单工作表 XLSX。",
  import_not_found: "导入文件已过期或不可访问，请重新上传。", export_not_found: "下载链接已过期或不可访问，请重新导出。",
  record_limit: "此表已达到 10,000 条记录的上限。", division_by_zero: "除数为零", dependency_error: "来源计算失败",
  duplicate_field_name: "字段名称已存在。", formula_cycle: "公式存在循环引用。", name_required: "请输入名称。",
};
export function hasErrorKey(code: string): boolean {return Object.hasOwn(errorKeys, code);}
export function errorKey(code: string): string {return errorKeys[code] ?? "操作未完成，请检查输入和权限后重试。";}

/** After `tables.view.save`: the view to show (in the page address, which outlives the grid's
 *  reload) and what to tell the person. */
export function viewSaved(t: Translator, output: {id: string; changed?: boolean}, name: string): {params: {view: string}; notice: string} {
  return {params: {view: output.id}, notice: output.changed === false ? t("视图没有变化。") : t("已保存视图「{name}」。", {name: name.trim()})};
}

/** One refused import row, batch item or save, in the reader's language, naming what it is about:
 *  the file column (`detail.column`), else the field (`detail.field_id`, one of `fields`). */
export function issueText(t: Translator, error: string, detail?: Record<string, unknown> | null, fields: Field[] = []): string {
  const column = typeof detail?.column === "string" ? detail.column : null;
  const value = typeof detail?.value === "string" ? detail.value : "";
  // Related records are named in a file the way export writes them; say which name failed.
  if (column && error === "link_not_found") return t("列「{column}」：找不到名为“{value}”的关联记录。", {column, value});
  if (column && error === "link_ambiguous") return t("列「{column}」：有多条关联记录名为“{value}”，无法确定是哪一条。", {column, value});
  const message = t(errorKey(error));
  if (column) return t("列「{column}」：{message}", {column, message});
  const field = fields.find(f => f.id === detail?.field_id)?.name;
  if (field) return t("字段「{field}」：{message}", {field, message});
  return message;
}
