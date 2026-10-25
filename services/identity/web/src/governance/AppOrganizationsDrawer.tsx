import { useId, useState } from "react";
import { qs } from "@synwevo/auth-client";
import { fmtTime, formatNumber, Modal, t as tr, useI18n, useToast } from "@synwevo/ui";
import { useDebounced } from "../lib/hooks";
import { useAdminPage } from "./data";
import { PageControls } from "./ListControls";
import { mayChangeAppWorkspace, type Held } from "./access";
import { ORGANIZATION_STATUS_LABELS } from "./OrganizationLifecycleDialog";
import { AppChangeDialog, CHANGE_LABELS, DONE_TEXT, changeKey, useOutcome,
  type AppChange, type AppChangeResult, type AppOrganization, type CatalogueApp } from "./AppChangeDialog";
import "./messages";
import "./apps.messages";

/**
 * B3: "按组织管理" — every organization and an application switched per organization (Synwevo
 * Message) there: its lifecycle state, whether the platform switched the application off for it and
 * when, its live members and its conversations (`GET /api/admin/apps/{id}/organizations`,
 * `platform.apps.view`). With `platform.apps.manage` — the same permission as a workspace's switch,
 * a per-tenant change — each row offers switching it off or on for that organization through
 * `AppChangeDialog` (impact preview, reason, re-authentication). Counts and metadata only: no
 * member, no conversation, no message.
 */

/** Whether an organization's recovery window is over (or it never had one): deleted and lost, so
 *  the server refuses switching the application on for it (`organization_unavailable`). */
export const organizationLost = (o: Pick<AppOrganization, "status" | "recover_until">, now = Date.now()) =>
  o.status === "deleted" && !(o.recover_until && o.recover_until * 1000 > now);
/** What a row offers: off where it is on; on where it is off, unless the organization is lost
 *  (then why not). A frozen organization, or one deleted and still recoverable, may be switched
 *  either way: its conversations are paused by its lifecycle anyway, and stay as switched once it
 *  is back. */
export function organizationOffer(o: Pick<AppOrganization, "status" | "recover_until" | "enabled">, now = Date.now()): { change: AppChange | null; hint?: string } {
  if (o.enabled) return { change: "disable" };
  if (organizationLost(o, now)) return { change: null, hint: "组织已删除且过了恢复期，不能为它启用应用。" };
  return { change: "enable" };
}
export function organizationDoneText(result: AppChangeResult, change: AppChange, app: { name: string }, organization: { name: string }) {
  return `${tr(DONE_TEXT[changeKey(change, "organization")], { app: result.app?.name || app.name, organization: result.organization?.name || organization.name })} ${tr("操作编号：{id}", { id: result.operation_id })}`;
}
const statusText = (status: string) => ORGANIZATION_STATUS_LABELS[status] ? tr(ORGANIZATION_STATUS_LABELS[status]) : status;

export function AppOrganizationsDrawer({ app, held, onDenied, onClose, onChanged }: {
  app: CatalogueApp; held: Held; onDenied: () => void; onClose: () => void; onChanged: () => void;
}) {
  useI18n();
  const toast = useToast();
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const searchId = useId();
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<AppOrganization>(
    `/api/admin/apps/${encodeURIComponent(app.id)}/organizations${qs({ q: query })}`, onDenied);
  // the same permission as a workspace's switch: platform.apps.manage (a per-tenant change)
  const canManage = mayChangeAppWorkspace(held);
  const items = loading || err ? [] : data?.items || [];
  const [change, setChange] = useState<{ organization: AppOrganization; change: AppChange } | null>(null);
  const outcome = useOutcome();
  const busy = loading || !!err;
  return <Modal title={tr("「{app}」的组织", { app: app.name })} size="wide" onClose={onClose}>
    <div className="col gap12">
      <p className="muted small">{tr("每个组织是否启用了这个应用，以及其中的有效成员和未归档的会话数。只显示数量，不显示成员或内容。为一个组织停用不影响其他组织，也不删除任何数据。")}</p>
      {app.status !== "active" && <p className="notice warn" role="note">{tr("这个应用目前在全平台停用：全平台的停用优先于这里的设置，为组织启用要等全平台重新启用后才生效。")}</p>}
      {!canManage && <p className="notice" role="note">{tr("你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。")}</p>}
      <div className="field"><label htmlFor={searchId}>{tr("搜索组织")}</label>
        <input id={searchId} type="search" className="input" value={q} maxLength={100} aria-describedby={`${searchId}-hint`}
          onChange={(e) => setQ(e.target.value)} data-autofocus />
        <span id={`${searchId}-hint`} className="tiny muted">{tr("输入组织名称的一部分，或完整的组织编号。")}</span></div>
      {outcome.region}
      {err && <div className="notice err" role="alert">{err} <button type="button" className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
      {loading && <p role="status">{tr("正在加载组织…")}</p>}
      <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={tr("「{app}」的组织", { app: app.name })} aria-busy={loading}>
        <thead><tr>
          <th scope="col">{tr("组织")}</th><th scope="col">{tr("组织状态")}</th><th scope="col">{tr("这个应用")}</th>
          <th scope="col">{tr("有效成员")}</th><th scope="col">{tr("未归档的会话")}</th><th scope="col" className="act">{tr("操作")}</th>
        </tr></thead>
        <tbody>{items.map((o) => { const offer = organizationOffer(o);
          return <tr key={o.id}>
            <th scope="row">{o.name}<div className="who2 mono">{o.id}</div></th>
            <td data-label={tr("组织状态")}>{o.status === "active" ? statusText(o.status)
              : <div><span className="badge warn">{statusText(o.status)}</span>
                {o.status === "deleted" && <span className="cell-note">{organizationLost(o) ? tr("已过恢复期") : tr("可恢复至 {time}", { time: fmtTime(o.recover_until!) })}</span>}</div>}</td>
            <td data-label={tr("这个应用")}><div>{o.enabled ? <span className="badge ok">{tr("已启用")}</span> : <span className="badge warn">{tr("已停用")}</span>}
              {o.updated_at && <span className="cell-note">{tr("更改于 {time}", { time: fmtTime(o.updated_at) })}</span>}</div></td>
            <td data-label={tr("有效成员")}>{formatNumber(o.members)}</td>
            <td data-label={tr("未归档的会话")}>{formatNumber(o.conversations)}</td>
            <td className="act" data-label={tr("操作")}><div className="row gap4 wrap">
              {canManage && offer.change && <button type="button" className={`btn sm${offer.change === "disable" ? " danger" : ""}`} disabled={busy}
                aria-label={tr("{action}：{p0}", { action: tr(CHANGE_LABELS[changeKey(offer.change, "organization")]), p0: o.name })}
                onClick={() => setChange({ organization: o, change: offer.change! })}>{tr(CHANGE_LABELS[changeKey(offer.change, "organization")])}</button>}
              {canManage && offer.hint && <span className="cell-note">{tr(offer.hint)}</span>}
            </div></td>
          </tr>; })}
        {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>{tr("没有匹配的组织。")}</td></tr>}</tbody>
      </table></div>
      <PageControls offset={offset} total={data?.total ?? offset + items.length} busy={busy} onPage={setOffset} />
    </div>
    {change && <AppChangeDialog app={app} change={change.change} organization={change.organization} onClose={() => setChange(null)}
      onDone={(result) => {
        const message = organizationDoneText(result, change.change, app, change.organization);
        setChange(null); reload(); onChanged(); outcome.say(message); toast(message, "ok");
      }} />}
  </Modal>;
}
