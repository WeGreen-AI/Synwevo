import { useState } from "react";
import { post, setWorkspaceContext, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, portalWorkspaceUrl, useAction } from "./AuthFlow";
import OrganizationAction from "./OrganizationAction";
import { orgPath, roleSource, statusSource, type Organization, type OrganizationWorkspace } from "./types";
export default function OrganizationWorkspaces({ organization, workspaces, onChanged }: { organization: Organization; workspaces: OrganizationWorkspace[]; onChanged: () => void }) {
  const { t, locale } = useI18n(); const { me, state, refresh } = useSession(); const action = useAction(); const [name, setName] = useState("");
  const [adopt, setAdopt] = useState(""); const [confirmAdopt, setConfirmAdopt] = useState(false);
  const manage = organization.role === "owner" || organization.role === "admin";
  const candidates = (me?.workspaces || []).filter(w => w.kind === "org" && w.id !== "default" && w.role === "owner" && !(w as typeof w & { organization_id?: string }).organization_id && !workspaces.some(ow => ow.id === w.id));
  function create(e: React.FormEvent) { e.preventDefault(); void action.run(async () => {
    if (!name.trim()) throw new Error("请填写工作空间名称。"); await post(`${orgPath(organization.id)}/workspaces`, { name: name.trim() }); setName(""); onChanged(); await refresh();
  }); }
  return <section className="org-section"><h2>{t("组织工作空间")}</h2><FlowError error={action.error} />
    {workspaces.length ? <div className="org-grid">{workspaces.map(w => <article key={w.id} className="org-card"><h3>{w.name}</h3><p>{t(statusSource(w.status))} · {w.workspace_role ? t(roleSource(w.workspace_role)) : t("你不是成员")}</p>
      {w.workspace_role && w.status === "active" && organization.status === "active" && <button type="button" className="btn" disabled={action.busy} onClick={() => void action.run(async () => {
        const destination = portalWorkspaceUrl(state?.portal_url, w.id, locale);
        await post("/api/auth/workspace", { workspace_id: w.id });
        setWorkspaceContext(w.id);
        window.location.assign(destination);
      })}>{t("进入工作空间")}</button>}
      {manage && ["owner", "admin"].includes(w.workspace_role || "") && organization.status === "active" && w.status === "active" && (organization.role === "owner" || organization.invitation_policy === "admins") && <a className="btn ghost" href={`/account/workspaces/${encodeURIComponent(w.id)}/members`}>{t("成员与邀请")}</a>}
    </article>)}</div> : <p className="muted">{t("这个组织还没有工作空间。")}</p>}
    {manage && organization.status === "active" && <><form className="org-create" onSubmit={create} aria-busy={action.busy}><div className="field"><label htmlFor="new-workspace-name">{t("新工作空间名称")}</label><input id="new-workspace-name" className="input" required value={name} maxLength={100} disabled={action.busy} onChange={e => setName(e.target.value)} /></div><button className="btn" disabled={action.busy}>{t(action.busy ? "创建中…" : "创建工作空间")}</button></form>
      {candidates.length > 0 && <div className="flow-stack org-create"><div className="field"><label htmlFor="adopt-workspace">{t("纳入已有工作空间")}</label><select id="adopt-workspace" className="input" value={adopt} onChange={e => setAdopt(e.target.value)}><option value="">{t("选择你拥有的团队工作空间")}</option>{candidates.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select><span className="tiny muted">{t("只能纳入由你拥有、尚未属于任何组织的工作空间。")}</span></div><button type="button" className="btn" disabled={!adopt} onClick={() => setConfirmAdopt(true)}>{t("查看并确认纳入")}</button></div>}
    </>}
    {confirmAdopt && <OrganizationAction spec={{ title: t("纳入已有工作空间"), impact: t("将「{name}」纳入此组织。它的成员会加入组织，但不会因此获得其他工作空间的权限。", { name: candidates.find(w => w.id === adopt)?.name || adopt }), run: reason => post(`${orgPath(organization.id)}/workspaces/${encodeURIComponent(adopt)}/adopt`, { reason }) }} onClose={() => setConfirmAdopt(false)} onDone={() => { setConfirmAdopt(false); setAdopt(""); onChanged(); void refresh(); }} />}
  </section>;
}
