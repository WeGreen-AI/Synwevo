import { resultText } from "./labels";
import { t as tr } from "@synwevo/ui";
import "./messages";
/**
 * What a bulk action actually did, item by item.
 *
 * The server authorises and applies every object on its own and answers
 * `{ok, succeeded, failed, results: [{id, ok, code, message, ...}]}`; this lists every one of
 * those results. A single "done" toast for a batch in which two of twelve removals were refused
 * is how someone walks away believing a person no longer has access when they do.
 */
import { ReactNode } from "react";
import { Modal } from "@synwevo/ui";

export interface BulkItemResult {
  id: string;
  ok: boolean;
  code: string;
  message: string;
  [k: string]: unknown;
}
export interface BulkResponse {
  ok: boolean;
  succeeded: number;
  failed: number;
  results: BulkItemResult[];
}

export function bulkToast(r: BulkResponse, what: string): [string, "ok" | "err"] {
  if (r.failed === 0) return [tr("{p0}：{p1} 项全部成功", {p0: what, p1: r.succeeded}), "ok"];
  if (r.succeeded === 0) return [tr("{p0}：{p1} 项全部失败，详情见结果列表", {p0: what, p1: r.failed}), "err"];
  return [tr("{p0}：{p1} 项成功，{p2} 项失败，详情见结果列表", {p0: what, p1: r.succeeded, p2: r.failed}), "err"];
}

export function BulkResultDialog({ title, response, labelOf, detailOf, onClose, onSelectFailed }: {
  title: string;
  response: BulkResponse;
  /** Human name for a result id (a member's name, an invitation's address). */
  labelOf: (id: string) => ReactNode;
  /** Extra effect worth stating for a success (tokens revoked, new expiry…). */
  detailOf?: (r: BulkItemResult) => ReactNode;
  onClose: () => void;
  /** Re-select what failed, so it can be retried or dealt with one by one. */
  onSelectFailed?: (ids: string[]) => void;
}) {
  const failed = response.results.filter((r) => !r.ok);
  // Failures first: they are the part that needs someone to do something.
  const ordered = [...failed, ...response.results.filter((r) => r.ok)];
  return (
    <Modal title={tr(title)} onClose={onClose} size="narrow" role="dialog"
      footer={
        <>
          {onSelectFailed && failed.length > 0 && (
            <button type="button" className="btn" onClick={() => { onSelectFailed(failed.map((r) => r.id)); onClose(); }}>{tr("只选中失败的{p0}项", {p0: failed.length})}</button>
          )}
          <span className="spacer" />
          <button type="button" className="btn primary" data-autofocus onClick={onClose}>{tr("关闭")}</button>
        </>
      }>
      <div className="col gap12">
        <p className={`notice ${response.failed === 0 ? "ok" : response.succeeded === 0 ? "err" : "warn"}`} role="status">
          {response.failed === 0
            ? <>{tr("全部{p0}项成功。", {p0: response.succeeded})}</>
            : <>{tr("成功{p0}项，失败{p1}项。请查看失败原因，并核对刷新后的列表。", {p0: response.succeeded, p1: response.failed})}</>}
        </p>
        <ul className="bulkres" aria-label={tr("逐项结果")}>
          {ordered.map((r) => (
            <li key={r.id} className={r.ok ? "ok" : "err"}>
              <span className="mark" aria-hidden="true">{r.ok ? "✓" : "✕"}</span>
              <span className="grow">
                <span className="who">{labelOf(r.id)}</span>
                <span className="what">
                  <span className="sr-only">{r.ok ? tr("成功") : tr("失败")}：</span>
                  {r.ok ? (detailOf?.(r) || tr("完成")) : resultText(r.code, r.message, r.detail)}
                  {!r.ok && r.code && <span className="mono tiny muted"> {r.code}</span>}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </Modal>
  );
}
