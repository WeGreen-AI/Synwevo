import { useEffect, useState } from "react";
import { api, get, patch, post } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, Loading } from "./AuthFlow";
import OrganizationAction from "./OrganizationAction";
import { orgPath, roleSource, type MemberPage, type Organization, type OrganizationMember, type OrganizationRole } from "./types";
export default function OrganizationMembers({ organization, onChanged }: { organization: Organization; onChanged: () => void }) {
  const { t } = useI18n(); const [page, setPage] = useState<MemberPage | null>(null); const [error, setError] = useState<unknown>(null);
  const [input, setInput] = useState(""); const [query, setQuery] = useState(""); const [offset, setOffset] = useState(0); const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<{ member: OrganizationMember; operation: "role" | "remove" | "transfer" } | null>(null);
  const [role, setRole] = useState<OrganizationRole>("member");
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
        {!member.is_self && <div className="org-actions">
          {organization.role === "owner" && member.manageable && <button type="button" className="btn" disabled={page.single_owner && member.role === "owner"} onClick={() => { setRole(member.role); setSelected({ member, operation: "role" }); }}>{t("修改角色")}</button>}
          {organization.role === "owner" && organization.status === "active" && member.account_status === "active" && <button type="button" className="btn" onClick={() => setSelected({ member, operation: "transfer" })}>{t("交接所有权")}</button>}
          {member.manageable && <button type="button" className="btn danger" disabled={page.single_owner && member.role === "owner"} onClick={() => setSelected({ member, operation: "remove" })}>{t("移除")}</button>}
        </div>}
      </li>)}</ul>}
      <div className="org-actions"><button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(v => Math.max(0, v - 25))}>{t("上一页")}</button><button type="button" className="btn" disabled={offset + page.items.length >= page.total} onClick={() => setOffset(v => v + 25)}>{t("下一页")}</button></div>
    </>}
    {selected && <OrganizationAction key={`${selected.member.user_id}-${selected.operation}`} spec={{
      title: t(selected.operation === "role" ? "修改成员角色" : selected.operation === "transfer" ? "交接组织所有权" : "移除组织成员"),
      impact: t(selected.operation === "role" ? "修改 {email} 的组织角色。工作空间内的权限仍由各空间独立管理。" : selected.operation === "transfer" ? "将组织所有权交给 {email}，你的组织角色将变为管理员。工作空间所有权不会自动转移。" : "移除 {email} 后，对方会失去此组织及所属工作空间的成员资格，相关会话、令牌和待处理邀请会被撤销。", { email: selected.member.email }),
      danger: selected.operation !== "role",
      run: reason => selected.operation === "role" ? patch(`${orgPath(organization.id)}/members/${encodeURIComponent(selected.member.user_id)}`, { role, reason })
        : selected.operation === "transfer" ? post(`${orgPath(organization.id)}/transfer`, { user_id: selected.member.user_id, reason })
        : api(`${orgPath(organization.id)}/members/${encodeURIComponent(selected.member.user_id)}`, { method: "DELETE", body: { reason } }),
    }} ready={selected.operation !== "role" || role !== selected.member.role} onClose={() => setSelected(null)} onDone={reload}>
      {selected.operation === "role" && <div className="field"><label htmlFor="member-role">{t("新角色")}</label><select id="member-role" className="input" value={role} onChange={e => setRole(e.target.value as OrganizationRole)}>{(["member", "admin", "owner"] as const).map(r => <option key={r} value={r}>{t(roleSource(r))}</option>)}</select></div>}
    </OrganizationAction>}
  </section>;
}
