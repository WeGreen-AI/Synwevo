import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { get, patch, post, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { AuthFrame, FlowError, Loading, needsVerification } from "./org/AuthFlow";
import OrganizationCreate from "./org/OrganizationCreate";
import OrganizationAction, { type OrganizationActionSpec } from "./org/OrganizationAction";
import OrganizationAvatar from "./org/OrganizationAvatar";
import OrganizationMembers from "./org/OrganizationMembers";
import OrganizationWorkspaces from "./org/OrganizationWorkspaces";
import OrganizationRoles from "./org/OrganizationRoles";
import { orgPath, roleSource, statusSource, type Organization, type OrganizationWorkspace, type PermissionInfo } from "./org/types";
import "../organization.messages";

export default function Organizations() {
  const { oid } = useParams<{ oid: string }>();
  return oid ? <OrganizationDetail key={oid} id={oid} /> : <OrganizationList />;
}
function OrganizationList() {
  const { t } = useI18n(); const { me } = useSession();
  const [items, setItems] = useState<Organization[] | null>(null); const [error, setError] = useState<unknown>(null); const [retry, setRetry] = useState(0); const [create, setCreate] = useState(false);
  useEffect(() => {
    if (!me || needsVerification(me)) return;
    const abort = new AbortController(); setError(null);
    get<{ items: Organization[] }>("/api/auth/organizations", abort.signal).then(out => setItems(out.items)).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [me?.id, retry, needsVerification(me)]);
  return <AuthFrame wide title={t("我的组织")} description={t("管理团队归属与协作空间。组织成员身份与工作空间权限分别管理。")}>
    <div className="org-actions"><a href="/account">{t("账户设置")}</a><a href="/onboarding">{t("工作空间")}</a></div>
    <FlowError error={error} />{needsVerification(me) ? <a href="/verify-email">{t("先验证邮箱")}</a> : !me ? <Loading /> : <>
      <button type="button" className="btn primary" onClick={() => setCreate(v => !v)} aria-expanded={create} aria-controls="organization-create">{t(create ? "收起创建表单" : "创建组织")}</button>
      {create && <section id="organization-create" className="org-section"><h2>{t("创建组织")}</h2><OrganizationCreate onCreated={org => { window.location.assign(`/organizations/${encodeURIComponent(org.id)}`); }} /></section>}
      {error ? <button type="button" className="btn" onClick={() => setRetry(v => v + 1)}>{t("重试")}</button> : !items ? <Loading /> : items.length ? <div className="org-grid">{items.map(org => <article className="org-card" key={org.id}><h2>{org.name}</h2><p>{t(roleSource(org.role))} · {t(statusSource(org.status))}</p><a className="btn" href={`/organizations/${encodeURIComponent(org.id)}`}>{t("查看组织")}</a></article>)}</div> : <p className="notice">{t("你还没有加入组织。可以创建一个，或使用团队发来的邀请邮件加入。")}</p>}
    </>}
  </AuthFrame>;
}
function OrganizationDetail({ id }: { id: string }) {
  const { t, locale } = useI18n(); const { me, refresh } = useSession();
  const [org, setOrg] = useState<Organization | null>(null); const [workspaces, setWorkspaces] = useState<OrganizationWorkspace[]>([]);
  const [error, setError] = useState<unknown>(null); const [revision, setRevision] = useState(0); const [modal, setModal] = useState<string | null>(null);
  const [name, setName] = useState(""); const [policy, setPolicy] = useState<"admins" | "owners">("admins");
  const [mine, setMine] = useState<string[]>([]); const [catalogue, setCatalogue] = useState<PermissionInfo[]>([]);
  useEffect(() => {
    if (!me || needsVerification(me)) return;
    const abort = new AbortController(); setError(null);
    Promise.all([get<{ organization: Organization }>(orgPath(id), abort.signal), get<{ items: OrganizationWorkspace[] }>(`${orgPath(id)}/workspaces`, abort.signal),
                 get<{ catalogue: PermissionInfo[]; mine: string[] }>(`${orgPath(id)}/permissions?lang=${encodeURIComponent(locale)}`, abort.signal)])
      .then(([a, b, c]) => { setOrg(a.organization); setWorkspaces(b.items); setName(a.organization.name); setPolicy(a.organization.invitation_policy); setMine(c.mine); setCatalogue(c.catalogue); })
      .catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [id, me?.id, revision, needsVerification(me), locale]);
  const changed = () => { setRevision(v => v + 1); void refresh(); };
  // What this member may do here comes from the server's permission catalogue (base role plus
  // custom roles), not from the base role alone.
  const manage = mine.includes("org.settings.manage");
  const seeMembers = mine.includes("org.members.view");
  const lifecycle: Record<string, { title: string; impact: string; danger?: boolean }> = {
    freeze: { title: "冻结组织", impact: "冻结后，所属工作空间将停止访问，相关会话、令牌与待处理邀请会被撤销。之后可以解冻，已撤销的凭据不会恢复。", danger: true },
    unfreeze: { title: "解冻组织", impact: "恢复组织访问。已撤销的会话、令牌和邀请不会恢复，成员需要重新登录。" },
    delete: { title: "删除组织", impact: "组织和所属工作空间会停止访问，相关会话、令牌与待处理邀请会被撤销。七天内可恢复组织；此操作不会直接删除文档数据。", danger: true },
    recover: { title: "恢复组织", impact: "恢复至删除前的正常或冻结状态。已撤销的会话、令牌和邀请不会恢复。" },
    leave: { title: "退出组织", impact: "你将失去此组织及所属工作空间的成员资格，相关登录与令牌会失效。若你是唯一所有者，需要先交接组织和工作空间。", danger: true },
  };
  let spec: OrganizationActionSpec | null = null;
  if (modal === "settings" && org) spec = { title: t("编辑组织设置"), impact: t("更新组织名称或邀请策略。更严格的策略可能撤销不再符合条件的待处理邀请。"), run: reason => patch(orgPath(id), { name: name.trim(), ...(org.role === "owner" ? { invitation_policy: policy } : {}), version: org.version, reason }) };
  else if (modal && lifecycle[modal]) spec = { title: t(lifecycle[modal].title), impact: t(lifecycle[modal].impact), danger: lifecycle[modal].danger, run: reason => post(`${orgPath(id)}/${modal}`, { reason }) };
  return <AuthFrame wide title={org?.name || t("组织详情")} description={t("成员、空间与组织设置，在这里保持清晰。")}>
    <a className="action-link" href="/organizations">{t("返回我的组织")}</a><FlowError error={error} />
    {needsVerification(me) ? <a href="/verify-email">{t("先验证邮箱")}</a> : error ? <button type="button" className="btn" onClick={() => setRevision(v => v + 1)}>{t("重试")}</button> : !org ? <Loading /> : <>
      <div className="org-actions"><span className="badge">{t(statusSource(org.status))}</span><span>{t("你的角色：{role}", { role: t(roleSource(org.role)) })}</span>{org.usage && <span className="org-count">{t("{workspaces} 个空间 · {members} 位成员", { workspaces: org.usage.workspaces, members: org.usage.members })}</span>}</div>
      {org.status !== "active" && <p className="notice warn">{t(org.status === "frozen" ? "组织已冻结，工作空间暂时无法访问。" : "组织已删除，工作空间暂时无法访问。")}{org.status === "deleted" && org.recover_until && ` ${t("可恢复至 {date}", { date: new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(org.recover_until * 1000) })}`}</p>}
      <OrganizationAvatar organization={org} onChanged={changed} />
      <OrganizationWorkspaces organization={org} workspaces={workspaces} onChanged={changed} />
      {seeMembers && <OrganizationMembers organization={org} mine={mine} catalogue={catalogue} onChanged={changed} />}
      {seeMembers && <OrganizationRoles organization={org} mine={mine} catalogue={catalogue} onChanged={changed} />}
      {manage && <section className="org-section"><h2>{t("组织设置")}</h2><p className="tiny muted">{t(org.invitation_policy === "owners" ? "仅组织所有者可发送工作空间邀请。" : "组织所有者和管理员可发送工作空间邀请。")}</p>{org.status === "active" && <button type="button" className="btn" onClick={() => { setName(org.name); setPolicy(org.invitation_policy); setModal("settings"); }}>{t("编辑组织设置")}</button>}</section>}
      <section className="org-section"><h2>{t("组织访问")}</h2><div className="org-actions">
        {org.role === "owner" && <>{org.status === "active" && <button type="button" className="btn" onClick={() => setModal("freeze")}>{t("冻结组织")}</button>}{org.status === "frozen" && <button type="button" className="btn" onClick={() => setModal("unfreeze")}>{t("解冻组织")}</button>}{org.status !== "deleted" ? <button type="button" className="btn danger" onClick={() => setModal("delete")}>{t("删除组织")}</button> : org.recover_until && org.recover_until * 1000 > Date.now() ? <button type="button" className="btn primary" onClick={() => setModal("recover")}>{t("恢复组织")}</button> : <span className="muted">{t("恢复期限已过。")}</span>}</>}
        <button type="button" className="btn" onClick={() => setModal("leave")}>{t("退出组织")}</button>
      </div></section>
      {spec && <OrganizationAction spec={spec} ready={modal !== "settings" || !!name.trim()} onClose={() => setModal(null)} onDone={() => { if (modal === "leave") { window.location.assign("/organizations"); return; } setModal(null); changed(); }}>
        {modal === "settings" && <><div className="field"><label htmlFor="edit-org-name">{t("组织名称")}</label><input id="edit-org-name" className="input" value={name} onChange={e => setName(e.target.value)} required maxLength={100} /></div>{org.role === "owner" && <div className="field"><label htmlFor="invitation-policy">{t("邀请策略")}</label><select id="invitation-policy" className="input" value={policy} onChange={e => setPolicy(e.target.value as "owners" | "admins")}><option value="admins">{t("所有者和管理员")}</option><option value="owners">{t("仅所有者")}</option></select></div>}</>}
      </OrganizationAction>}
    </>}
  </AuthFrame>;
}
