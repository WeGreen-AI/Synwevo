import { useEffect, useRef, useState } from "react";
import { ApiError } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { read, op, newKey } from "../../shared/ops";
import { Failure } from "../../shared/states";
import { errorKey, hasErrorKey, issueText } from "./display";
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
export { errorKey, issueText } from "./display";
/** A refusal in Tables' own words; with `fields`, it names the field it is about. */
export function TableFailure({error, retry, fields}: {error: unknown; retry?: () => void; fields?: Field[]}) {
  const {t} = useI18n();
  if (!error) return null;
  return <Failure error={error instanceof ApiError && hasErrorKey(error.code) ? new Error(issueText(t, error.code, error.detail, fields)) : error} onRetry={retry} />;
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
