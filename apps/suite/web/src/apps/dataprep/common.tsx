import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "@synwevo/auth-client";
import { useI18n, formatDate } from "@synwevo/ui";
import { read, op, newKey } from "../../shared/ops";
import { Empty, Failure } from "../../shared/states";
import { stepLabels, type DataRow, type Issue, type Step } from "./model";

export const when = (ts: number) => formatDate(ts * 1000, { dateStyle: "medium", timeStyle: "short" });
export function useRead<T>(operation: string, input: Record<string, unknown>, workspace: string, poll = false, enabled = true) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState<unknown>(null), [tick, setTick] = useState(0);
  const serialized = JSON.stringify(input);
  useEffect(() => {
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    setData(null); setError(null);
    if (!enabled) return () => abort.abort();
    async function load() {
      try { const value = await read<T>(operation, JSON.parse(serialized), workspace, abort.signal); if (!abort.signal.aborted) { setData(value); setError(null); } }
      catch (e) { if (!abort.signal.aborted) setError(e); }
      finally { if (poll && !abort.signal.aborted) timer = setTimeout(load, 1800); }
    }
    void load();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [operation, serialized, workspace, tick, poll, enabled]);
  return { data, error, reload: useCallback(() => setTick(v => v + 1), []) };
}
export function useAction(workspace: string) {
  const [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(null);
  const pending = useRef<{ fingerprint: string; key: string } | null>(null), lock = useRef(false);
  async function run<T = any>(operation: string, input: Record<string, unknown>, done?: (value: T) => void) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    const fingerprint = JSON.stringify([workspace, operation, input]);
    if (pending.current?.fingerprint !== fingerprint) pending.current = { fingerprint, key: newKey() };
    try {
      const result = await op<T>(`dataprep.${operation}`, input, { workspace, key: pending.current.key });
      pending.current = null; done?.(result.output); return result.output;
    } catch (e) {
      // A history/permission refusal can follow a committed write. Retain the key
      // across every error; changed parameters or confirmed success start a new intent.
      setError(e);
    } finally { lock.current = false; setBusy(false); }
  }
  return { busy, error, run, setError };
}
export const errorKeys: Record<string, string> = {
  dataprep_not_found: "数据或方案不存在", invalid_columns: "列名必须唯一且不能为空", invalid_csv: "文件结构无效", invalid_workbook: "文件结构无效",
  invalid_encoding: "无法按所选编码读取文件", sheet_missing: "工作表不存在", formula_rejected: "范围包含公式，请明确选择缓存值或调整范围",
  formula_cache_missing: "公式没有缓存值", spreadsheet_error: "工作表包含错误单元格", active_workbook: "不接受宏或外部链接", file_type: "请选择 CSV 或 XLSX 文件",
  file_limit: "文件为空或超过 8 MB", workbook_limit: "文件展开后超过限制", table_limit: "数据超过处理规模限制", row_limit: "数据超过处理规模限制",
  join_limit: "关联展开超过处理规模限制", cell_limit: "单元格内容超过限制", issue_limit: "异常数量超过限制", invalid_decimal: "不是有效的十进制数值",
  precision_loss: "转换会丢失小数精度", timezone_required: "日期时间需要明确时区", invalid_date: "日期无效", invalid_datetime: "日期时间无效", invalid_boolean: "布尔值无效",
  duplicate_match: "关联键重复，请选择首个匹配或全部匹配", unknown_column: "步骤引用了不存在的列", source_missing: "请绑定所有来源", column_collision: "输出列名重复",
  append_columns: "追加数据的列不一致", row_width: "该行列数超过标题范围", unknown_parameter: "参数未在方案中声明", rule_failed: "未通过校验规则",
  draft_conflict: "方案已被修改，请重新载入", recipe_archived: "方案已归档", version_retired: "版本已停用或方案已归档", version_missing: "方案版本不存在",
  run_active: "运行尚未结束", processing_failed: "处理失败，请重试", recovery_exhausted: "运行多次中断，请检查后重试", input_changed: "输入校验失败",
  run_timeout: "运行超时，请缩小范围", queue_limit: "排队数量已达上限", storage_limit: "工作空间存储限额已达上限", preview_limit: "预览结果过大，请缩小样本",
  credential_revoked: "发起这次运行的应用已停用，运行已停止", grant_revoked: "发起这次运行的应用授权已撤回，运行已停止", scope_withdrawn: "发起这次运行的应用已不再持有所需权限，运行已停止", unknown_principal: "无法确认发起这次运行的应用凭据，运行已停止",
  export_not_allowed: "你没有导出权限", xlsx_invalid_char: "内容含不支持的表格字符，请导出 JSON", result_unavailable: "来源权限已改变，无法显示历史结果", invalid_input: "请检查必填字段与步骤参数",
};
export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useI18n();
  const code = error instanceof ApiError ? error.code : typeof error === "string" ? error : "";
  return <Failure error={errorKeys[code] ? new Error(t(errorKeys[code])) : error} onRetry={retry} />;
}
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="dp-field"><span>{label}</span>{children}</label>;
}
export function Grid({ columns, rows, caption }: { columns: string[]; rows: DataRow[]; caption: string }) {
  const { t } = useI18n();
  if (!rows.length) return <Empty title={t("没有数据行")} />;
  return <div className="dp-grid" tabIndex={0} role="region" aria-label={caption}><table><caption>{caption}</caption><thead><tr><th scope="col">{t("来源行")}</th>{columns.map(c => <th scope="col" key={c}>{c}</th>)}</tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i}><th scope="row"><details><summary>{r.origins[0]?.[1] ?? i + 1}</summary><span className="small">{r.origins.map(o => o.join(":" )).join(" · ")}{r.origin_count && r.origin_count > r.origins.length ? ` (+${r.origin_count - r.origins.length})` : ""}</span></details></th>
      {columns.map(c => <td key={c}>{!(c in r.values) ? <em className="muted">{t("缺失")}</em> : r.values[c] === null ? <em className="muted">{t("空值")}</em> : r.values[c] === "" ? <em className="muted">{t("空字符串")}</em> : r.values[c]}</td>)}</tr>)}</tbody></table></div>;
}
/** `steps` is the definition that produced these issues ([] for import issues, which have no step). */
export function IssueList({ items, steps }: { items: Issue[]; steps: Step[] }) {
  const { t } = useI18n(), labels = stepLabels(steps, t);
  if (!items.length) return <Empty title={t("当前范围没有异常")} />;
  return <div className="dp-grid" tabIndex={0} role="region" aria-label={t("异常行")}><table><thead><tr>{["来源", "来源行", "列", "步骤", "异常原因"].map(k => <th key={k} scope="col">{t(k)}</th>)}</tr></thead>
    <tbody>{items.map((i, n) => <tr key={n}><td title={i.input_label ? i.input_name : undefined}>{i.input_label || i.input_name || "—"}</td><td>{i.source_row ?? "—"}</td><td>{i.column_name || "—"}</td><td>{i.step_id ? labels[i.step_id] || t("步骤") : "—"}</td><td>{t(errorKeys[i.code] || "未通过校验规则")}{i.code === "rule_failed" && i.message && i.message !== i.code ? ` · ${i.message}` : ""}</td></tr>)}</tbody></table></div>;
}
export function Pager({ offset, next, set }: { offset: number; next: number | null; set: (n: number) => void }) {
  const { t } = useI18n();
  return <div className="dp-actions"><button className="btn sm" disabled={offset === 0} onClick={() => set(Math.max(0, offset - 50))}>{t("上一页")}</button><span>{offset + 1}</span><button className="btn sm" disabled={next === null} onClick={() => next !== null && set(next)}>{t("下一页")}</button></div>;
}
