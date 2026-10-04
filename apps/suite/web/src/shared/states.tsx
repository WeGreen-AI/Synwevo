/** Whole-area states every page uses: loading, a refusal or failure with its request id, empty. */
import type { ReactNode } from "react";
import { ApiError } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";

export function Loading({ label }: { label?: string }) {
  const { t } = useI18n();
  return <div className="suite-state" role="status" aria-live="polite"><span className="suite-spinner" aria-hidden="true" />{label ?? t("加载中…")}</div>;
}

/** `text` is an application's own wording of the refusal (by its code), when it has one. */
export function Failure({ error, onRetry, text: own }: { error: unknown; onRetry?: () => void; text?: string }) {
  const { t } = useI18n();
  const text = own ?? (error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error));
  const rid = error instanceof ApiError ? error.requestId : undefined;
  return <div className="suite-state err" role="alert">
    <p>{text}</p>
    {rid && <p className="muted small">{t("请求编号：{id}", { id: rid })}</p>}
    {onRetry && <button type="button" className="btn sm" onClick={onRetry}>{t("重试")}</button>}
  </div>;
}

export function Empty({ title, children, action }: { title: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return <div className="suite-empty">
    <p className="suite-empty-title">{title}</p>
    {children && <div className="muted">{children}</div>}
    {action && <div className="suite-empty-action">{action}</div>}
  </div>;
}

export function RunBadge({ status }: { status: string }) {
  const { t } = useI18n();
  const map: Record<string, [string, string]> = {
    queued: [t("排队中"), ""], running: [t("进行中"), "info"], waiting_input: [t("等待填写"), "warn"],
    waiting_approval: [t("等待审批"), "warn"], succeeded: [t("已完成"), "ok"], partial: [t("部分完成"), "warn"],
    failed: [t("失败"), "err"], cancelled: [t("已取消"), ""], unknown: [t("结果待核实"), "warn"],
  };
  const [label, tone] = map[status] ?? [status, ""];
  return <span className={`badge ${tone}`}>{label}</span>;
}
