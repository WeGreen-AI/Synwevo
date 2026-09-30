import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@synwevo/auth-client";
import { useI18n, formatDate } from "@synwevo/ui";
import { newKey, op, read } from "../../shared/ops";
import { Failure } from "../../shared/states";
import type { Page } from "./types";
import { errors, statuses, fieldTypes, modes, operators, events, problems } from "./text";

export { fieldTypes, modes, operators, events, problems };
export const base = (ws: string) => `/approvals/w/${ws}`;
export const stamp = (ts: number | null | undefined) => ts ? formatDate(ts * 1000, { dateStyle: "medium", timeStyle: "short" }) : "—";
export function Status({ value }: { value: string }) {
  const { t } = useI18n();
  const tone = ["approved", "published", "active"].includes(value) ? "ok" : ["rejected", "revoked"].includes(value) ? "err" : ["stalled", "returned", "pending", "in_review"].includes(value) ? "warn" : "";
  return <span className={`badge ${tone}`}>{t(statuses[value] ?? value)}</span>;
}
export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useI18n();
  if (!error) return null;
  const detail = error instanceof ApiError ? error.detail as { errors?: { field: string; code: string }[]; problems?: { code: string; field?: string; stage?: string }[] } : undefined;
  const localized = error instanceof ApiError ? new Error(t(errors[error.code] ?? "操作未完成，请刷新状态后重试。")) : error;
  return <><Failure error={localized} onRetry={retry} />
    {error instanceof ApiError && error.requestId && <p className="muted small">{t("请求编号：{id}", { id: error.requestId })}</p>}
    {!!(detail?.errors || detail?.problems) && <ul className="notice err">{[...detail?.errors ?? [], ...detail?.problems ?? []].map((p, n) =>
      <li key={n}>{p.field ?? ("stage" in p ? p.stage : "")} · {t(problems[p.code] ?? "请检查字段、条件和审批人设置。")}</li>)}</ul>}</>;
}
export function useRead<T>(name: string, input: Record<string, unknown>, ws: string) {
  const [data, setData] = useState<T>(); const [error, setError] = useState<unknown>(); const [generation, setGeneration] = useState(0);
  const serialized = JSON.stringify(input);
  const reload = useCallback(() => setGeneration(x => x + 1), []);
  useEffect(() => {
    const controller = new AbortController(); setData(undefined); setError(undefined);
    read<T>(`approvals.${name}`, JSON.parse(serialized), ws, controller.signal).then(setData).catch(e => {
      if (!controller.signal.aborted) setError(e);
    });
    return () => controller.abort();
  }, [name, serialized, ws, generation]);
  return { data, error, reload };
}
export function useAction(ws: string) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>();
  const active = useRef(false); const intent = useRef<{ signature: string; key: string } | null>(null);
  async function run<T = any>(name: string, payload: Record<string, unknown>, done?: (result: T) => void) {
    if (active.current) return;
    active.current = true; setBusy(true); setError(undefined);
    const signature = JSON.stringify([ws, name, payload]);
    if (intent.current?.signature !== signature) intent.current = { signature, key: newKey() };
    try {
      const result = await op<T>(`approvals.${name}`, payload, { workspace: ws, key: intent.current.key });
      intent.current = null; done?.(result.output);
    } catch (e) {
      setError(e);
      // Keep this intent on every error, including a history guard refusal: a 403 can mean
      // the write already committed but its old output is no longer safe to show. Changing
      // payload creates a new intent; retrying unchanged input must never repeat the write.
    } finally { active.current = false; setBusy(false); }
  }
  return { busy, error, run };
}
export function Pager({ page, offset, setOffset }: { page: Page<unknown>; offset: number; setOffset: (v: number) => void }) {
  const { t } = useI18n();
  return <div className="ap-toolbar"><span className="muted">{t("共 {n} 项", { n: page.total })}</span>
    <button type="button" className="btn sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>{t("上一页")}</button>
    <button type="button" className="btn sm" disabled={page.next_offset === null} onClick={() => setOffset(page.next_offset!)}>{t("下一页")}</button></div>;
}
