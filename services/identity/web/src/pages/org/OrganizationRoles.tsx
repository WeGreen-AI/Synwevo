/** Custom roles of an organization: named sets of organization-level permissions.
 *  Everything an editor may put into a role is limited to what they hold themselves (the server
 *  enforces it; the boxes for permissions they lack are disabled here so the rule is visible).
 *  Owner-only permissions are shown but never selectable. */
import { useEffect, useState } from "react";
import { api, get, patch, post } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, Loading } from "./AuthFlow";
import OrganizationAction from "./OrganizationAction";
import { orgPath, type CustomRole, type Organization, type PermissionInfo } from "./types";

const GROUP_LABEL: Record<string, string> = { organization: "组织", members: "成员", roles: "角色", workspaces: "工作空间", governance: "治理", owner: "仅所有者" };

export function PermissionPicker({ catalogue, mine, value, onChange, idPrefix }: {
  catalogue: PermissionInfo[]; mine: string[]; value: string[]; onChange: (next: string[]) => void; idPrefix: string;
}) {
  const { t } = useI18n();
  const groups = Array.from(new Set(catalogue.map(p => p.group)));
  return <div className="col gap8 perm-picker">
    {groups.map(g => <fieldset key={g} className="flow-options"><legend>{t(GROUP_LABEL[g] || g)}</legend>
      {catalogue.filter(p => p.group === g).map(p => {
        const held = mine.includes(p.id); const disabled = p.owner_only || !held;
        return <label key={p.id} className="check" title={p.owner_only ? t("仅所有者持有，不能放进自定义角色") : held ? undefined : t("你没有这项权限，不能授予他人")}>
          <input id={`${idPrefix}-${p.id}`} type="checkbox" checked={value.includes(p.id)} disabled={disabled}
            onChange={e => onChange(e.target.checked ? [...value, p.id] : value.filter(x => x !== p.id))} />
          <span>{p.label}<small className="muted">{p.description}</small></span>
        </label>;
      })}
    </fieldset>)}
  </div>;
}

export default function OrganizationRoles({ organization, mine, catalogue, onChanged }: {
  organization: Organization; mine: string[]; catalogue: PermissionInfo[]; onChanged: () => void;
}) {
  const { t } = useI18n();
  const [roles, setRoles] = useState<CustomRole[] | null>(null); const [error, setError] = useState<unknown>(null); const [revision, setRevision] = useState(0);
  const [action, setAction] = useState<{ kind: "create" } | { kind: "edit"; role: CustomRole } | { kind: "delete"; role: CustomRole } | null>(null);
  const [name, setName] = useState(""); const [description, setDescription] = useState(""); const [perms, setPerms] = useState<string[]>([]);
  const canManage = mine.includes("org.roles.manage");
  useEffect(() => {
    const abort = new AbortController(); setError(null);
    get<{ items: CustomRole[] }>(`${orgPath(organization.id)}/roles`, abort.signal).then(out => setRoles(out.items)).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [organization.id, revision]);
  const reload = () => { setAction(null); setRevision(v => v + 1); onChanged(); };
  const open = (next: NonNullable<typeof action>) => {
    if (next.kind === "edit") { setName(next.role.name); setDescription(next.role.description || ""); setPerms(next.role.permissions); }
    else if (next.kind === "create") { setName(""); setDescription(""); setPerms([]); }
    setAction(next);
  };
  const labelOf = (id: string) => catalogue.find(p => p.id === id)?.label || id;
  return <section className="org-section" id="org-roles"><h2>{t("自定义角色")}</h2>
    <p className="tiny muted">{t("角色是一组组织级权限，可分配给成员；不改变工作空间内的权限。所有者拥有全部权限；管理员持有默认的管理权限；普通成员默认没有组织级权限。")}</p>
    <FlowError error={error} />
    {canManage && organization.status === "active" && <div className="org-actions"><button type="button" className="btn primary" onClick={() => open({ kind: "create" })}>{t("创建角色")}</button></div>}
    {error ? <button type="button" className="btn" onClick={() => setRevision(v => v + 1)}>{t("重试")}</button> : !roles ? <Loading />
      : !roles.length ? <p className="muted">{t("还没有自定义角色。")}</p>
      : <ul className="org-list">{roles.map(role => <li key={role.id}>
        <div><strong>{role.name}</strong>{role.description && <div className="small muted">{role.description}</div>}
          <div className="row wrap gap4 role-perms">{role.permissions.map(p => <span key={p} className="badge">{labelOf(p)}</span>)}</div>
          <div className="tiny muted">{t("已分配给 {count} 位成员", { count: role.assigned_count ?? 0 })}</div></div>
        {canManage && <div className="org-actions">
          <button type="button" className="btn" onClick={() => open({ kind: "edit", role })}>{t("编辑")}</button>
          <button type="button" className="btn danger" onClick={() => open({ kind: "delete", role })}>{t("删除")}</button>
        </div>}
      </li>)}</ul>}
    {action && <OrganizationAction key={action.kind + ((action as { role?: CustomRole }).role?.id || "")} spec={{
      title: t(action.kind === "create" ? "创建角色" : action.kind === "edit" ? "编辑角色" : "删除角色"),
      impact: action.kind === "delete"
        ? t(action.role.assigned_count ? "删除后，{count} 位成员将失去这个角色带来的权限。" : "这个角色没有分配给任何成员。", { count: action.role.assigned_count ?? 0 })
        : t("角色只能包含你自己已持有的权限。分配给成员后，成员立即获得这些权限。"),
      danger: action.kind === "delete",
      run: reason => action.kind === "create" ? post(`${orgPath(organization.id)}/roles`, { name: name.trim(), description: description.trim() || undefined, permissions: perms })
        : action.kind === "edit" ? patch(`${orgPath(organization.id)}/roles/${encodeURIComponent(action.role.id)}`, { name: name.trim(), description: description.trim() || null, permissions: perms, version: action.role.version })
        : api(`${orgPath(organization.id)}/roles/${encodeURIComponent(action.role.id)}`, { method: "DELETE", body: { unassign: !!action.role.assigned_count } }).then(() => reason),
    }} ready={action.kind === "delete" || (!!name.trim() && perms.length > 0)} onClose={() => setAction(null)} onDone={reload}>
      {action.kind !== "delete" && <>
        <div className="field"><label htmlFor="role-name">{t("角色名称")}</label><input id="role-name" className="input" value={name} maxLength={60} required onChange={e => setName(e.target.value)} /></div>
        <div className="field"><label htmlFor="role-desc">{t("说明（可选）")}</label><input id="role-desc" className="input" value={description} maxLength={500} onChange={e => setDescription(e.target.value)} /></div>
        <PermissionPicker catalogue={catalogue} mine={mine} value={perms} onChange={setPerms} idPrefix="role-perm" />
      </>}
    </OrganizationAction>}
  </section>;
}
