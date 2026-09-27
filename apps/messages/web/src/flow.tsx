/** The two status pieces the messages page uses: an error notice that takes focus, and a loading
 *  line. The account shell has richer versions tied to its sign-in flows; these are the parts
 *  the conversation page needs, written here so this shell imports nothing from another shell. */
import { useEffect, useRef, type ReactNode } from "react";
import { ApiError } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";

export function errorText(error: unknown, t: (s: string, p?: Record<string, string | number>) => string): string {
  if (error instanceof ApiError) return error.message || t("请求失败，请稍后重试。");
  if (error instanceof Error) return error.message || t("请求失败，请稍后重试。");
  return t("请求失败，请稍后重试。");
}

export function FlowError({ error }: { error: unknown }) {
  const { t } = useI18n(); const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) ref.current?.focus(); }, [error]);
  if (!error) return null;
  const text = errorText(error, t);
  return <div ref={ref} className="notice err flow-error" role="alert" tabIndex={-1}>
    {text}{error instanceof ApiError && error.requestId && !text.includes(error.requestId) && <div className="tiny">{t("请求编号：{id}", { id: error.requestId })}</div>}
  </div>;
}

export function Loading({ children }: { children?: ReactNode }) {
  const { t } = useI18n();
  return <p className="flow-status" role="status">{children || t("加载中…")}</p>;
}
