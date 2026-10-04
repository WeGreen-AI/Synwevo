import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { newKey, op, read } from "../../shared/ops";

export type Workspace = { id: string; name: string; role: string };
export type Props = { ws: Workspace; canWrite: boolean };
export interface Field {
  id: string; label: string; type: "text" | "decimal" | "date" | "boolean";
  required: boolean; many: boolean; review_required: boolean;
  rule: { kind: "prefix" | "cell" | "column"; prefix: string; table: string; row: number; column: number; header: string };
  max_length: number; minimum: string | null; maximum: string | null;
}
export interface Template { id: string; title: string; draft_rev: number; published_version: number | null; archived: boolean;
  draft: { fields: Field[] }; versions: { version: number; retired: boolean }[] }
export type Location = { page?: number; line?: number; table?: string; row?: number; column?: number; ocr?: boolean };
export interface Evidence { segment_id: string; start: number; end: number; source: Location }
export interface Value { value: string | string[] | null; status: string; origin: string;
  candidates: { value: string; evidence: Evidence; verified: boolean; validation: string | null }[] }
export interface Segment { id: string; text: string; source: Location; located: boolean }
export interface Task {
  id: string; title: string; workspace_id: string; template_id: string; template_version: number; status: string;
  revision: number; confirmed_revision: number | null; error_code: string | null; attempts: number;
  file: { id: string; filename: string; content_digest: string; scan: { status: string }; source?: { app?: string; submission_id?: string; workspace_id?: string } };
  definition: { fields: Field[] }; machine: Record<string, Value> | null; values: Record<string, Value> | null;
  history: { revision: number; kind: string; note: string; actor_id: string; created_at: number }[];
}
export const blankField = (): Field => ({ id: `f_${newKey().replaceAll("-", "").slice(0, 12)}`, label: "", type: "text",
  required: true, many: false, review_required: true, max_length: 2000, minimum: null, maximum: null,
  rule: { kind: "prefix", prefix: "", table: "", row: 1, column: 1, header: "" } });

export function useLoad<T>(operation: string, input: Record<string, unknown>, workspace: string, poll = false) {
  const encoded = JSON.stringify(input);
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion(v => v + 1), []);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setData(undefined); setError(undefined);
    const load = async () => {
      let keepPolling = poll;
      try {
        const value = await read<T>(operation, JSON.parse(encoded), workspace, abort.signal);
        if (!abort.signal.aborted) { setData(value); setError(undefined); }
        if (operation === "extract.task.get") keepPolling = ["queued", "running"].includes((value as { status: string }).status);
      } catch (e) { if (!abort.signal.aborted) { setError(e); setData(undefined); } }
      if (keepPolling && !abort.signal.aborted) timer = setTimeout(load, 2500);
    };
    void load();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [operation, encoded, workspace, version, poll]);
  return { data, error, reload };
}
export function useAction(workspace: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const pending = useRef(new Map<string, string>());
  const lock = useRef(false);
  const execute = async <T,>(name: string, input: Record<string, unknown>): Promise<T | undefined> => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(undefined);
    const intent = JSON.stringify([workspace, name, input]);
    const key = pending.current.get(intent) ?? newKey(); pending.current.set(intent, key);
    try {
      const result = await op<T>(name, input, { workspace, key });
      pending.current.delete(intent); return result.output;
    } catch (e) {
      // A failed response does not prove the server did not commit. Reuse the key
      // until success, or until the user makes a different business request.
      setError(e); return undefined;
    }
    finally { lock.current = false; setBusy(false); }
  };
  return { busy, error, setError, execute };
}
export const errors: Record<string, string> = {
  file_quarantined: "文件已被隔离，当前不能读取或处理", file_unavailable: "文件当前不可用，请联系管理员核实存储状态",
  parser_not_configured: "文件解析服务未配置", ocr_not_configured: "文字识别服务未配置",
  ocr_required: "文件需要启用文字识别", ocr_incomplete: "文字识别未完整完成",
  parse_failed: "文件无法解析，请检查格式或重新导出", encrypted_file: "请移除文件密码后重新上传",
  damaged_file: "文件已损坏或不完整（可能被截断），没有从中取值；请用生成它的程序重新导出后重新上传",
  parse_timeout: "解析超时，请拆分文件后重试", parse_limit: "解析结果不完整，请拆分文件后重试",
  candidate_limit: "匹配值过多，请缩小规则范围", result_limit: "提取结果过大，请缩小规则范围",
  review_conflict: "结果已被修改，请刷新后重试", draft_conflict: "模板已被修改，请刷新后重试",
  review_unresolved: "请先处理缺失、歧义、冲突或无效字段", unverified_ack_required: "请明确确认无可靠来源的值",
  invalid_correction: "修正值未通过字段校验", queue_full: "提取队列已满，请稍后重试",
  export_not_allowed: "没有导出权限", result_not_confirmed: "该结果版本尚未确认或已被替代",
  not_a_member: "你已无权访问该工作空间", read_only_role: "你目前只有查看权限",
  file_limit: "文件为空或超过十二兆字节", storage_limit: "该空间已达文件存储上限",
  unsupported_format: "请选择支持的文件格式", run_result_unavailable: "操作已执行，请核实业务记录后再操作",
  idempotent_failure: "上次操作已失败，请刷新记录后重试", worker_interrupted: "任务中断次数过多，请重新发起",
  worker_stopped: "服务已停止该任务，请重新发起", file_integrity_error: "原文件校验失败",
  credential_revoked: "发起这个任务的应用已停用，任务已停止", grant_revoked: "发起这个任务的应用授权已撤回，任务已停止", scope_withdrawn: "发起这个任务的应用已不再持有所需权限，任务已停止", unknown_principal: "无法确认发起这个任务的应用凭据，任务已停止",
};
export function useErrorText(error: unknown) {
  const { t } = useI18n();
  const code = typeof error === "string" ? error : error instanceof ApiError ? error.code : "";
  return t(errors[code] ?? "操作未完成，请检查输入、权限和连接后重试");
}
export const displayValue = (v: Value | undefined) => !v || v.value === null ? "—" : Array.isArray(v.value) ? v.value.join("\n") : v.value;
