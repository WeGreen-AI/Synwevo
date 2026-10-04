import { useEffect, useState } from "react";
import { api, get, patch, post } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, Loading } from "./AuthFlow";
import OrganizationAction from "./OrganizationAction";
import { Modal } from "@synwevo/ui";
import { put } from "@synwevo/auth-client";
import { orgPath, roleSource, type CustomRole, type EffectivePermissions, type MemberPage, type Organization, type OrganizationMember, type OrganizationRole, type PermissionInfo } from "./types";
export default function OrganizationMembers({ organization, mine = [], catalogue = [], onChanged }: {
  organization: Organization; mine?: string[]; catalogue?: PermissionInfo[]; onChanged: () => void;
}) {
  const { t } = useI18n(); const [page, setPage] = useState<MemberPage | null>(null); const [error, setError] = useState<unknown>(null);
  const [input, setInput] = useState(""); const [query, setQuery] = useState(""); const [offset, setOffset] = useState(0); const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<{ member: OrganizationMember; operation: "role" | "remove" | "transfer" | "assign" } | null>(null);
  const [role, setRole] = useState<OrganizationRole>("member");
  const [roles, setRoles] = useState<CustomRole[] | null>(null); const [picked, setPicked] = useState<string[]>([]);
  const [effective, setEffective] = useState<{ member: OrganizationMember; data: EffectivePermissions | null; error?: unknown } | null>(null);
  const canAssign = mine.includes("org.roles.manage");
  const canManageMembers = mine.includes("org.members.manage");
  const labelOf = (id: string) => catalogue.find(p => p.id === id)?.label || id;
  // Fetched when the assignment dialog opens, so a role created a moment ago in the roles
  // panel is offered without a page reload.
  const loadRoles = () => { setRoles(null); get<{ items: CustomRole[] }>(`${orgPath(organization.id)}/roles`).then(out => setRoles(out.items)).catch(() => setRoles([])); };
  const showPermissions = (member: OrganizationMember) => {
    setEffective({ member, data: null });
    get<EffectivePermissions>(`${orgPath(organization.id)}/members/${encodeURIComponent(member.user_id)}/permissions`)
      .then(data => setEffective({ member, data })).catch(e => setEffective({ member, data: null, error: e }));
  };
  useEffect(() => {
    const abort = new AbortController(); setError(null); setPage(null);
    get<MemberPage>(`${orgPath(organization.id)}/members?${new URLSearchParams({ q: query, offset: String(offset), limit: "25" })}`, abort.signal)
      .then(setPage).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [organization.id, query, offset, revision]);
  const reload = () => { setSelected(null); setRevision(v => v + 1); onChanged(); };
  return <section className="org-section"><h2>{t("组织成员")}</h2>
    <form className="org-actions" onSubmit={e => { e.preventDefault(); setOffset(0); setQuery(input.trim()); }}><div className="field"><label htmlFor="org-member-query">{t("搜索姓名或邮箱")}</label><input id="org-member-query" className="input" type="search" value={input} maxLength={100} onChange={e => setInput(e.target.value)} /></div><button className="btn">{t("搜索")}</button></form>
    <FlowError error={error} />{error ? <button type="button" className="btn" onClick={() => setRevision(v => v + 1)}>{t("重试")}</button> : !page ? <Loading /> : <>
      <p className="org-count">{t("共 {count} 位成员", { count: page.total })}</p>
      {page.single_owner && organization.role === "owner" && <p className="notice warn">{t("当前只有一位可用所有者。退出前，请先交接组织和工作空间所有权。")}</p>}
      {!page.items.length ? <p className="muted">{t("没有符合条件的成员。")}</p> : <ul className="org-list">{page.items.map(member => <li key={member.user_id}><div><strong>{member.display_name || member.email}</strong><div className="muted">{member.email} · {t(roleSource(member.role))}{member.is_self ? ` · ${t("你")}` : ""}{member.account_status !== "active" ? ` · ${t("账户不可用")}` : ""}</div></div>
        {!!member.custom_roles?.length && <div className="row wrap gap4 role-perms">{member.custom_roles.map(r => <span key={r.id} className="badge info">{r.name}</span>)}</div>}
        <div className="org-actions">
          <button type="button" className="btn" onClick={() => showPermissions(member)}>{t("查看权限")}</button>
          {canAssign && !member.is_self && member.role !== "owner" && <button type="button" className="btn" onClick={() => { loadRoles(); setPicked((member.custom_roles || []).map(r => r.id)); setSelected({ member, operation: "assign" }); }}>{t("分配角色")}</button>}
          {!member.is_self && (organization.role === "owner" || canManageMembers) && member.manageable && <button type="button" className="btn" disabled={page.single_owner && member.role === "owner"} onClick={() => { setRole(member.role); setSelected({ member, operation: "role" }); }}>{t("修改角色")}</button>}
          {!member.is_self && organization.role === "owner" && organization.status === "active" && member.account_status === "active" && <button type="button" className="btn" onClick={() => setSelected({ member, operation: "transfer" })}>{t("交接所有权")}</button>}
          {!member.is_self && member.manageable && (organization.role === "owner" || canManageMembers) && <button type="button" className="btn danger" disabled={page.single_owner && member.role === "owner"} onClick={() => setSelected({ member, operation: "remove" })}>{t("移除")}</button>}
        </div>
      </li>)}</ul>}
      <div className="org-actions"><button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(v => Math.max(0, v - 25))}>{t("上一页")}</button><button type="button" className="btn" disabled={offset + page.items.length >= page.total} onClick={() => setOffset(v => v + 25)}>{t("下一页")}</button></div>
    </>}
    {selected && <OrganizationAction key={`${selected.member.user_id}-${selected.operation}`} spec={{
      title: t(selected.operation === "role" ? "修改成员角色" : selected.operation === "transfer" ? "交接组织所有权" : selected.operation === "assign" ? "分配自定义角色" : "移除组织成员"),
      impact: selected.operation === "assign" ? t("为 {email} 设置自定义角色。成员立即获得所选角色的组织级权限；工作空间内的权限不变。", { email: selected.member.email }) : t(selected.operation === "role" ? "修改 {email} 的组织角色。工作空间内的权限不变。" : selected.operation === "transfer" ? "将组织所有权交给 {email}，你的组织角色将变为管理员。工作空间所有权不会自动转移。" : "移除 {email} 后，对方会失去此组织及所属工作空间的成员资格，相关会话、令牌和待处理邀请会被撤销。", { email: selected.member.email }),
      danger: selected.operation !== "role",
      run: reason => selected.operation === "assign" ? put(`${orgPath(organization.id)}/members/${encodeURIComponent(selected.member.user_id)}/roles`, { role_ids: picked, reason })
        : selected.operation === "role" ? patch(`${orgPath(organization.id)}/members/${encodeURIComponent(selected.member.user_id)}`, { role, reason })
        : selected.operation === "transfer" ? post(`${orgPath(organization.id)}/transfer`, { user_id: selected.member.user_id, reason })
        : api(`${orgPath(organization.id)}/members/${encodeURIComponent(selected.member.user_id)}`, { method: "DELETE", body: { reason } }),
    }} ready={selected.operation === "assign" ? true : selected.operation !== "role" || role !== selected.member.role} onClose={() => setSelected(null)} onDone={reload}>
      {selected.operation === "assign" && (roles === null ? <Loading /> : !roles.length ? <p className="muted">{t("还没有自定义角色。")}</p>
        : <fieldset className="flow-options"><legend>{t("自定义角色")}</legend>{roles.map(r => { const beyond = !r.permissions.every(p => mine.includes(p)); return <label key={r.id} className="check" title={beyond ? t("这个角色包含你没有的权限，不能分配或收回") : r.permissions.map(labelOf).join("、")}>
          {/* adding or taking away a role needs every permission it carries (D12); one the member keeps untouched needs nothing */}
          <input type="checkbox" checked={picked.includes(r.id)} disabled={beyond} onChange={e => setPicked(e.target.checked ? [...picked, r.id] : picked.filter(x => x !== r.id))} /><span>{r.name}{r.description && <small className="muted">{r.description}</small>}</span></label>; })}</fieldset>)}
      {selected.operation === "role" && <div className="field"><label htmlFor="member-role">{t("新角色")}</label><select id="member-role" className="input" value={role} onChange={e => setRole(e.target.value as OrganizationRole)}>{(["member", "admin", "owner"] as const).map(r => <option key={r} value={r}>{t(roleSource(r))}</option>)}</select></div>}
    </OrganizationAction>}
    {effective && <Modal title={t("有效权限：{name}", { name: effective.member.display_name || effective.member.email })} onClose={() => setEffective(null)} size="narrow">
      <FlowError error={effective.error} />
      {!effective.data && !effective.error ? <Loading /> : effective.data && <>
        <p className="tiny muted">{t("基础角色：{role}", { role: t(roleSource(effective.data.base_role || "member")) })}{effective.data.roles.length ? " · " + t("自定义角色：{names}", { names: effective.data.roles.map(r => r.name).join("、") }) : ""}</p>
        {!effective.data.permissions.length ? <p className="muted">{t("该成员没有组织级权限；工作空间内的权限另行管理。")}</p>
          : <ul className="org-list perm-list">{effective.data.permissions.map(p => <li key={p}><div><strong>{labelOf(p)}</strong>
            <div className="tiny muted">{t("来自：{sources}", { sources: (effective.data!.sources[p] || []).map(src => src.startsWith("base:") ? t("基础角色 {role}", { role: t(roleSource(src.slice(5))) }) : t("角色「{name}」", { name: effective.data!.roles.find(r => `role:${r.id}` === src)?.name || src })).join("、") })}</div></div></li>)}</ul>}
        <p className="tiny muted">{t("按成员资格与角色计算得出，不是该成员登录后看到的内容。")}</p>
      </>}
    </Modal>}
  </section>;
}
