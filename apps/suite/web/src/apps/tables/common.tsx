import { useEffect, useRef, useState } from "react";
import { ApiError } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { read, op, newKey } from "../../shared/ops";
import { Failure } from "../../shared/states";
import type { Field, RecordRow } from "./types";

export function useRead<T>(operation: string, input: Record<string, unknown>, workspace: string, tick = 0, enabled = true) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const payload = JSON.stringify(input);
  useEffect(() => {
    const controller = new AbortController();
    setData(undefined); setError(undefined);
    if (enabled) void read<T>(operation, JSON.parse(payload), workspace, controller.signal)
      .then(setData).catch(e => { if (!controller.signal.aborted) setError(e); });
    return () => controller.abort();
  }, [operation, payload, workspace, tick, enabled]);
  return {data, error};
}

/** Keep an intent's key across transport failures. A deliberate new successful action gets a new key. */
export function useMutation(workspace: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const keys = useRef(new Map<string, string>());
  const active = useRef(false);
  async function run<T = any>(operation: string, input: Record<string, unknown>): Promise<T> {
    if (active.current) throw new Error("Request in progress");
    const intent = JSON.stringify([workspace, operation, input]);
    const key = keys.current.get(intent) ?? newKey();
    keys.current.set(intent, key); active.current = true; setBusy(true); setError(undefined);
    try {
      const result = await op<T>(operation, input, {workspace, key});
      keys.current.delete(intent);
      return result.output;
    } catch(e) {
      // Even a 4xx can hide an already committed result. Keep the same intent key;
      // only changed parameters or a confirmed success start a new intent.
      setError(e); throw e;
    } finally {active.current = false; setBusy(false);}
  }
  return {run, busy, error, clear: () => setError(undefined)};
}

export const typeKeys: Record<string, string> = {
  text: "单行文本", long_text: "多行文本", number: "数值", money: "金额", date: "日期", datetime: "日期时间",
  boolean: "布尔值", single_select: "单选", multi_select: "多选", email: "邮箱", url: "网址", phone: "电话",
  link: "关联记录", formula: "计算公式", created_time: "创建时间", updated_time: "更新时间", created_by: "创建者", updated_by: "修改者",
};
export const roleKeys: Record<string, string> = {viewer: "查看者及以上", editor: "编辑者及以上", admin: "管理员及以上"};
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
export function errorKey(code: string) {return errorKeys[code] ?? "操作未完成，请检查输入和权限后重试。";}
export function TableFailure({error, retry}: {error: unknown; retry?: () => void}) {
  const {t} = useI18n();
  if (!error) return null;
  return <Failure error={error instanceof ApiError && errorKeys[error.code] ? new Error(t(errorKey(error.code))) : error} onRetry={retry} />;
}
export function valueText(f: Field, value: any, locale: string, row?: RecordRow): string {
  if (value === null || value === undefined || value === "") return "—";
  if (f.type === "money") return `${value.amount} ${value.currency}`;
  if (f.type === "link") return (row?.relations[f.id] ?? []).map(x => x.title).join(", ") || "—";
  if (f.type === "single_select" || f.type === "multi_select") {
    return (Array.isArray(value) ? value : [value]).map(v => f.config.choices?.find((x: any) => x.id === v)?.label ?? v).join(", ");
  }
  if (f.type === "created_time" || f.type === "updated_time") return new Intl.DateTimeFormat(locale, {dateStyle: "medium", timeStyle: "short"}).format(value * 1000);
  if (f.type === "datetime") return new Intl.DateTimeFormat(locale, {dateStyle: "medium", timeStyle: "short"}).format(new Date(value));
  return String(value);
}
