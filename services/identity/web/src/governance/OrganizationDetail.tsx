import { useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError, get, qs } from "@synwevo/auth-client";
import { fmtTime, formatNumber, getLocale, hasMessage, Modal, t as tr, useI18n, useToast } from "@synwevo/ui";
import { useDebounced, useLoad } from "../lib/hooks";
import { ADMIN_HEADING_ID } from "../admin/navigation";
import type { PermissionInfo } from "../pages/org/types";
import { useAdminPage } from "./data";
import { PageControls } from "./ListControls";
import { ACCOUNT_STATUS, WORKSPACE_KIND, WORKSPACE_STATUS, errText } from "./labels";
import { permitted, type Held } from "./access";
import { ORG_ROLE_LABELS } from "./AccountDetail";
import { closedWithoutFocus, withView } from "./runs";
import { LIFECYCLE_ACTION_LABELS, ORGANIZATION_STATUS_LABELS, OrganizationLifecycleDialog, lifecycleActions, recoverable,
  type LifecycleAction } from "./OrganizationLifecycleDialog";
import "./messages";
import "./organization-lifecycle.messages";
import "./org-detail.messages";

/**
 * A34: one organization's governance metadata for a platform administrator holding
 * `platform.orgs.view` — member of it or not (`GET /api/admin/organizations/{oid}` and its
 * workspaces, members and per-member permissions).
 *
 * Read-only and metadata only: the organization's status and settings, its counts, the names of
 * its owners, administrators, workspaces, custom roles and members, and the state of its pending
 * member imports and project copies. Never a project or template name, an imported row or file, a
 * copy's contents or a document. Reading it creates no membership, entitlement, token or session,
 * and the tenant pages keep refusing a non-member. People's addresses come only to a caller who may
 * also read accounts (`platform.users.view`); otherwise `email` is null and nothing stands in its
 * place, and the member search matches names only.
 *
 * The open organization is kept in the address (`org`, as the run pages keep the open run), so a
 * reload, the Back button and a link show the same panel. Lifecycle changes (A35) are offered here
 * with the same dialog and permission as on the list; the ownership handover (A37) stays in its own
 * panel below the list, which this panel points to when no owner is usable.
 */

// ------------------------------------------------------------------------------------ contract
/** A person as the detail names them. `email` is null unless the caller holds platform.users.view. */
export type OrgPerson = { user_id: string; display_name: string | null; email: string | null; account_status: string; joined_at: number; usable: boolean };
export type OrgPersonRef = { user_id: string; display_name: string | null };
export type OrgWorkspace = { id: string; name: string; kind: string; status: string; created_at: number; members: number };
export type OrgCustomRole = { id: string; name: string; permissions: string[]; assigned: number };
/** A member import that has not ended (`member_imports.status`: extracted, planned, running): its state
 *  and row count, never its file or rows. Rows are counted once a preview is planned, so an
 *  `extracted` import reports 0. */
export type OrgImport = { id: string; state: string; rows: number; created_at: number; created_by: OrgPersonRef | null };
/** A project copy request that has not ended (`project_copies.status`: pending, approved, running):
 *  never the project's name or manifest. `expires_at` is the deadline of the current step — the
 *  request's expiry while pending, the approval's while approved — and null while it runs. */
export type OrgCopy = { id: string; status: string; source_workspace_id: string; target_workspace_id: string; created_at: number;
  expires_at: number | null; requested_by: OrgPersonRef | null };
export type OrganizationDetail = {
  organization: { id: string; name: string; status: string; created_at: number; deleted_at: number | null; recover_until: number | null;
    previous_status: string | null; version: number; has_avatar: boolean };
  settings: { invitation_policy: string | null; transfer_policy: string | null };
  /** `pending_imports` / `pending_copies` are null where the deployment has no such records. */
  counts: { members: number; owners: number; admins: number; usable_owners: number; workspaces: number; workspaces_frozen: number;
    custom_roles: number; pending_imports: number | null; pending_copies: number | null };
  owners: OrgPerson[]; admins: OrgPerson[]; owners_total: number; admins_total: number;
  workspaces: { items: OrgWorkspace[]; total: number };
  custom_roles: OrgCustomRole[];
  pending: { imports: OrgImport[]; copies: OrgCopy[] };
  viewer: { is_member: boolean; role: string | null };
  /** Not part of the A34 contract: the organization-permission catalogue (`org_roles.catalogue`, the
   *  labels the tenant roles page shows) in the requested language. Permissions are labelled from it
   *  when the server sends it, otherwise shown by their stable id. */
  permission_catalogue?: PermissionInfo[];
};
export type OrgMember = OrgPerson & { role: string; custom_roles: { id: string; name: string }[] };
export type OrgMemberPage = { items: OrgMember[]; total: number; offset: number; limit: number; active_owner_count: number };
export type OrgWorkspacePage = { items: OrgWorkspace[]; total: number; offset: number; limit: number };
/** `OrgRoles.effective`: each permission with where it comes from (`base:<role>` or `role:<id>`). An
 *  account that is not active has no base role and no permissions. */
export type OrgEffective = { base_role: string | null; roles: { id: string; name: string; permissions: string[] }[];
  permissions: string[]; sources: Record<string, string[]> };

// ------------------------------------------------------------------------------------ words
/** The tenant settings page's wording for the two policies (pages/Organizations.tsx). */
export const INVITATION_POLICY_LABELS: Record<string, string> = { admins: "所有者和管理员", owners: "仅所有者" };
export const INVITATION_POLICY_MEANING: Record<string, string> = {
  admins: "组织所有者和管理员可发送工作空间邀请。", owners: "仅组织所有者可发送工作空间邀请。" };
export const TRANSFER_POLICY_LABELS: Record<string, string> = { organization: "仅限组织内的工作空间", any: "按各工作空间的数据流政策" };
export const TRANSFER_POLICY_MEANING: Record<string, string> = {
  organization: "组织内容只能在组织的工作空间之间转移。", any: "组织内容可按各工作空间的数据流政策带到组织以外。" };
/** `member_imports.status`, as the tenant import page words it (pages/org/OrganizationImport.tsx). */
export const IMPORT_STATE_LABELS: Record<string, string> = { extracted: "已读取", planned: "已生成预览", running: "执行中",
  finished: "已完成", partial: "部分完成", failed: "失败", cancelled: "已取消" };
/** `project_copies.status`, as the tenant approvals page words it (pages/org/OrganizationApprovals.tsx). */
export const COPY_STATUS_LABELS: Record<string, string> = { pending: "待审批", approved: "已批准，等待复制", rejected: "已拒绝",
  withdrawn: "已撤回", expired: "已过期", running: "复制中", done: "已完成", failed: "失败", stale: "需重新申请" };
/** The panel's wording for its refusals by code, used when the server's sentence is not catalogued:
 *  the page-wide sentence for `no_organization` speaks to a member ("或你不是其成员"). */
export const ORG_DETAIL_REFUSALS: Record<string, string> = {
  no_organization: "找不到这个组织：它可能已被永久清除。请刷新组织列表。",
  not_member: "这个账户已不是该组织的成员；请刷新成员列表。",
  bad_role: "请选择有效的组织角色筛选。",
  invalid_field: "搜索或分页参数无效，请清除搜索后重试。",
};
export const ORG_DETAIL_DENIED = "你的平台角色不能查看组织详情。";
export const ORG_DETAIL_NOTE = "平台管理只读取组织的治理元数据：不授予成员身份，不显示项目、模板、导入文件或文档内容。";

const DASH = "—";
const label = (map: Record<string, string>, key: string | null | undefined) => !key ? DASH : map[key] ? tr(map[key]) : key;
export const orgRoleText = (role: string | null | undefined) => label(ORG_ROLE_LABELS, role);
export const orgStatusText = (status: string) => label(ORGANIZATION_STATUS_LABELS, status);
/** A person's name as the panel shows it: the display name, else the address when the caller may
 *  read it, else the account id (never a placeholder that suggests there is no address). */
export const personName = (p: { display_name: string | null; email?: string | null; user_id: string }) => p.display_name || p.email || p.user_id;
export const refName = (p: OrgPersonRef | null) => p ? (p.display_name || p.user_id) : DASH;
/** The deadline of the copy request's current step: the request's expiry while it waits for a
 *  decision, the approval's while approved; a running copy has none. */
export function copyDeadline(copy: Pick<OrgCopy, "status" | "expires_at">): string {
  if (copy.expires_at) return fmtTime(copy.expires_at);
  return copy.status === "running" ? tr("执行中，无截止") : DASH;
}

function aboutDetail(e: unknown): unknown {
  if (!(e instanceof ApiError) || e.upstream || hasMessage(e.originalMessage) || !ORG_DETAIL_REFUSALS[e.code]) return e;
  const next = new ApiError(e.status, e.code, ORG_DETAIL_REFUSALS[e.code], e.detail, e.upstream);
  next.requestId = e.requestId; next.retryAfter = e.retryAfter;
  return next;
}
/** A refusal of the detail's routes as one sentence: the server's catalogued sentence first, then
 *  this panel's wording for its codes, then the page-wide wording (labels.ts). */
export const orgDetailErrText = (e: unknown) => errText(aboutDetail(e));
/** Which state an unanswered or refused detail is in: a 403 is the caller's role (the server has
 *  recorded the attempt), `no_organization` is an organization that is gone; anything else can be retried. */
export function detailProblem(error: unknown): "denied" | "missing" | "error" {
  if (error instanceof ApiError && !error.upstream) {
    if (error.status === 403 && error.code === "platform_permission_denied") return "denied";
    if (error.code === "no_organization") return "missing";
  }
  return "error";
}

/** Labels for organization permissions from the catalogue the server sent, else the stable id. */
export function permissionLabels(catalogue: PermissionInfo[] | undefined) {
  const byId = new Map((catalogue || []).map((p) => [p.id, p]));
  return { label: (id: string) => byId.get(id)?.label || id, description: (id: string) => byId.get(id)?.description, known: (id: string) => byId.has(id) };
}
type Labels = ReturnType<typeof permissionLabels>;

/** Where one permission comes from, in words: the base role, or a custom role by its name. */
export function sourceText(source: string, roles: { id: string; name: string }[]): string {
  if (source.startsWith("base:")) return tr("基础角色 {role}", { role: orgRoleText(source.slice(5)) });
  if (source.startsWith("role:")) {
    const role = roles.find((r) => r.id === source.slice(5));
    return tr("角色「{name}」", { name: role?.name || source.slice(5) });
  }
  return source;
}

// ------------------------------------------------------------------------------------ the address
export const ORG_VIEW_KEY = "org";
/** The open organization in the address. Opening adds a history entry (Back closes it); from inside
 *  an open panel it replaces it. Closing one opened from the list goes back; one opened by its link
 *  removes the key. When nothing holds focus after closing (opened by its link, or the list was read
 *  again and its row is gone), focus goes to the page heading (runs.ts `closedWithoutFocus`). */
export function useOrganizationView() {
  const location = useLocation();
  const navigate = useNavigate();
  const id = new URLSearchParams(location.search).get(ORG_VIEW_KEY) || "";
  const open = (org: string) => navigate({ pathname: location.pathname, search: withView(location.search, { [ORG_VIEW_KEY]: org }) },
    id ? { replace: true, state: location.state } : { state: { opened: true } });
  const close = () => (location.state as { opened?: boolean } | null)?.opened ? navigate(-1)
    : navigate({ pathname: location.pathname, search: withView(location.search, { [ORG_VIEW_KEY]: "" }) }, { replace: true });
  const shown = useRef(id);
  useEffect(() => {
    const lost = closedWithoutFocus(shown.current, id, !!document.activeElement && document.activeElement !== document.body);
    shown.current = id;
    if (lost) document.getElementById(ADMIN_HEADING_ID)?.focus();
  }, [id]);
  return { id, open, close };
}

// ------------------------------------------------------------------------------------ pieces
function Stat({ name, value, note }: { name: string; value: number | null; note?: string }) {
  return <div className="card stat">
    <div className="muted small">{name}</div>
    <div className="v">{value === null ? DASH : formatNumber(value)}</div>
    {note && <div className="small muted">{note}</div>}
  </div>;
}

function Usable({ usable }: { usable: boolean }) {
  return <span className={`badge ${usable ? "ok" : "warn"}`}>{usable ? tr("账户可用") : tr("账户不可用")}</span>;
}

/** Owners or administrators: name, address when visible, account state, whether they count. */
function PeopleTable({ caption, people, total, empty }: { caption: string; people: OrgPerson[]; total: number; empty: string }) {
  return <div className="col gap8">
    <h4 className="small">{caption}</h4>
    {!people.length ? <p className="muted small">{empty}</p> : <div className="list-wrap admin-cards-wrap">
      <table className="gov admin-cards-table" aria-label={caption}>
        <thead><tr><th scope="col">{tr("姓名")}</th><th scope="col">{tr("账户状态")}</th><th scope="col">{tr("加入时间")}</th></tr></thead>
        <tbody>{people.map((p) => <tr key={p.user_id}>
          <th scope="row">{personName(p)}{p.email && p.email !== personName(p) && <div className="who2">{p.email}</div>}</th>
          <td data-label={tr("账户状态")}><span className="row gap4 wrap">{ACCOUNT_STATUS[p.account_status] || p.account_status} <Usable usable={p.usable} /></span></td>
          <td data-label={tr("加入时间")}>{fmtTime(p.joined_at)}</td>
        </tr>)}</tbody>
      </table></div>}
    {total > people.length && <p className="muted small">{tr("共 {total} 位，这里按加入时间显示前 {shown} 位；在下方成员列表中按角色筛选可以看到全部。", { total, shown: people.length })}</p>}
  </div>;
}

function WorkspaceRows({ items }: { items: OrgWorkspace[] }) {
  return <>{items.map((w) => <tr key={w.id}>
    <th scope="row">{w.name}<div className="who2 mono">{w.id}</div></th>
    <td data-label={tr("类型")}>{WORKSPACE_KIND[w.kind] || w.kind}</td>
    <td data-label={tr("状态")}>{w.status === "active" ? WORKSPACE_STATUS.active : <span className="badge warn">{WORKSPACE_STATUS[w.status] || w.status}</span>}</td>
    <td data-label={tr("成员")}>{formatNumber(w.members)}</td>
    <td data-label={tr("创建时间")}>{fmtTime(w.created_at)}</td>
  </tr>)}</>;
}

/** The organization's workspaces: the first page arrives with the detail; later pages are read
 *  from `…/workspaces` (deleted workspaces are not listed). */
export function WorkspacesSection({ orgId, first }: { orgId: string; first: OrganizationDetail["workspaces"] }) {
  const headingId = useId();
  const [offset, setOffset] = useState(0);
  const path = `/api/admin/organizations/${encodeURIComponent(orgId)}/workspaces${qs({ offset, limit: 50 })}`;
  const later = useLoad<OrgWorkspacePage>(offset ? path : null, (signal) => get<OrgWorkspacePage>(path, signal));
  const page = offset ? (later.data?.offset === offset ? later.data : null) : { ...first, offset: 0, limit: 50 };
  const loading = !!offset && later.loading;
  const items = page?.items || [];
  const total = page?.total ?? first.total;
  return <section className="col gap8" aria-labelledby={headingId}>
    <h3 className="secttl" id={headingId}>{tr("工作空间（{count} 个）", { count: first.total })}</h3>
    <p className="muted small">{tr("不含已删除的工作空间；只显示名称与成员人数，不显示项目或内容。")}</p>
    {!!offset && !!later.error && <div className="notice err" role="alert">{orgDetailErrText(later.error)} <button type="button" className="btn sm" onClick={later.reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载工作空间…")}</p>}
    {!first.total ? <p className="muted small">{tr("这个组织还没有工作空间。")}</p> : <>
      <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={tr("组织的工作空间")} aria-busy={loading}>
        <thead><tr><th scope="col">{tr("名称")}</th><th scope="col">{tr("类型")}</th><th scope="col">{tr("状态")}</th>
          <th scope="col">{tr("成员")}</th><th scope="col">{tr("创建时间")}</th></tr></thead>
        <tbody><WorkspaceRows items={items} /></tbody>
      </table></div>
      {total > 50 && <PageControls offset={offset} total={total} busy={loading || (!!offset && !!later.error)} onPage={setOffset} />}
    </>}
  </section>;
}

/** Every member, searchable by name (and by address only for a caller who may read addresses), with
 *  a role filter, their custom roles, and the per-member permission explainer. */
export function MembersSection({ orgId, held, onExplain }: { orgId: string; held: Held; onExplain: (member: OrgMember) => void }) {
  const headingId = useId(), searchId = useId();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const query = useDebounced(q.trim());
  const emails = held.has("platform.users.view");
  const page = useAdminPage<OrgMember>(`/api/admin/organizations/${encodeURIComponent(orgId)}/members${qs({ q: query, role })}`);
  const items = page.loading || page.err ? [] : page.data?.items || [];
  const filtered = !!(query || role);
  return <section className="col gap8" aria-labelledby={headingId}>
    <h3 className="secttl" id={headingId}>{tr("组织成员")}</h3>
    <div className="row gap12 wrap" style={{ alignItems: "flex-end" }}>
      <div className="field grow"><label htmlFor={searchId}>{emails ? tr("搜索姓名或邮箱") : tr("搜索姓名")}</label>
        <input id={searchId} type="search" className="input" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} /></div>
      <label className="field">{tr("组织角色")}<select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="">{tr("全部角色")}</option>
        {(["owner", "admin", "member"] as const).map((r) => <option key={r} value={r}>{orgRoleText(r)}</option>)}</select></label>
    </div>
    {!emails && <p className="muted small">{tr("你的平台角色不能查看账户邮箱：这里不显示邮箱，搜索只按姓名匹配。")}</p>}
    {page.err && <div className="notice err" role="alert">{page.err} <button type="button" className="btn sm" onClick={page.reload}>{tr("重试")}</button></div>}
    {page.loading && <p role="status">{tr("正在加载成员…")}</p>}
    <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={tr("组织成员")} aria-busy={page.loading}>
      <thead><tr><th scope="col">{tr("姓名")}</th><th scope="col">{tr("组织角色")}</th><th scope="col">{tr("账户状态")}</th>
        <th scope="col">{tr("加入时间")}</th><th scope="col" className="act">{tr("操作")}</th></tr></thead>
      <tbody>{items.map((m) => <tr key={m.user_id}>
        <th scope="row">{personName(m)}{m.email && m.email !== personName(m) && <div className="who2">{m.email}</div>}</th>
        <td data-label={tr("组织角色")}><div>{orgRoleText(m.role)}
          {!!m.custom_roles?.length && <div className="row wrap gap4 org-detail-chips">{m.custom_roles.map((r) => <span key={r.id} className="badge info">{r.name}</span>)}</div>}</div></td>
        <td data-label={tr("账户状态")}><span className="row gap4 wrap">{ACCOUNT_STATUS[m.account_status] || m.account_status} {!m.usable && <Usable usable={false} />}</span></td>
        <td data-label={tr("加入时间")}>{fmtTime(m.joined_at)}</td>
        <td className="act" data-label={tr("操作")}><button type="button" className="btn sm" onClick={() => onExplain(m)}
          aria-label={tr("查看有效权限：{name}", { name: personName(m) })}>{tr("有效权限")}</button></td>
      </tr>)}
      {!page.loading && !page.err && !items.length && <tr className="empty-row"><td colSpan={5}>{filtered ? tr("没有符合条件的成员。") : tr("这个组织没有成员。")}</td></tr>}</tbody>
    </table></div>
    <PageControls offset={page.offset} total={page.data?.total || 0} busy={page.loading || !!page.err} onPage={page.setOffset} />
  </section>;
}

/** What one member may do in the organization and where each permission comes from (`OrgRoles.effective`). */
export function EffectivePermissionsBody({ data, labels, member }: { data: OrgEffective; labels: Labels; member?: Pick<OrgMember, "account_status" | "usable"> }) {
  // A member whose account is not active holds nothing here: not "a member without permissions".
  if (!data.base_role) return <div className="col gap12">
    <p className="notice" role="note">{member && !member.usable
      ? tr("这个账户当前不可用（{status}），组织权限对它不生效。", { status: ACCOUNT_STATUS[member.account_status] || member.account_status })
      : tr("这个账户当前没有生效的组织角色，组织权限对它不生效。")}</p>
    <p className="small muted">{tr("按成员资格与角色计算得出，不是该成员登录后看到的内容。")}</p>
  </div>;
  return <div className="col gap12">
    <p className="small">{tr("基础角色：{role}", { role: orgRoleText(data.base_role) })}
      {data.roles.length ? ` · ${tr("自定义角色：{names}", { names: data.roles.map((r) => r.name).join(tr("、")) })}` : ""}</p>
    {!data.permissions.length ? <p className="muted">{tr("该成员没有组织级权限；工作空间内的权限另行管理。")}</p>
      : <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={tr("有效权限")}>
        <thead><tr><th scope="col">{tr("权限")}</th><th scope="col">{tr("来源")}</th></tr></thead>
        <tbody>{data.permissions.map((p) => <tr key={p}>
          <th scope="row">{labels.label(p)}{labels.known(p) && <div className="who2 mono">{p}</div>}</th>
          <td data-label={tr("来源")}><div className="row wrap gap4 org-detail-chips">{(data.sources[p] || []).map((s) =>
            <span key={s} className={`badge${s.startsWith("role:") ? " info" : ""}`}>{sourceText(s, data.roles)}</span>)}</div></td>
        </tr>)}</tbody></table></div>}
    <p className="small muted">{tr("按成员资格与角色计算得出，不是该成员登录后看到的内容。")}</p>
  </div>;
}

export function MemberPermissionsDialog({ orgId, member, labels, onClose }: { orgId: string; member: OrgMember; labels: Labels; onClose: () => void }) {
  useI18n();
  const path = `/api/admin/organizations/${encodeURIComponent(orgId)}/members/${encodeURIComponent(member.user_id)}/permissions`;
  const load = useLoad<OrgEffective>(path, (signal) => get<OrgEffective>(path, signal));
  return <Modal title={<span className="org-detail-name">{tr("有效权限：{name}", { name: personName(member) })}</span>} size="narrow" onClose={onClose}
    footer={<><span className="spacer" /><button type="button" className="btn" onClick={onClose}>{tr("关闭")}</button></>}>
    {load.error ? <div className="notice err" role="alert">{orgDetailErrText(load.error)} <button type="button" className="btn sm" onClick={load.reload}>{tr("重试")}</button></div>
      : load.loading || !load.data ? <p role="status">{tr("正在计算有效权限…")}</p>
      : <EffectivePermissionsBody data={load.data} labels={labels} member={member} />}
  </Modal>;
}

// ------------------------------------------------------------------------------------ the panel
/** The loaded detail. Pure apart from the two lists it reads further pages of (workspaces, members),
 *  so it can be rendered from a fixture (tests/admin-org-detail.test.mjs). */
export function OrganizationDetailBody({ detail: d, held, onAct, onExplain }: {
  detail: OrganizationDetail; held: Held; onAct?: (action: LifecycleAction) => void; onExplain: (member: OrgMember) => void;
}) {
  useI18n();
  const ids = { overview: useId(), people: useId(), roles: useId(), pending: useId() };
  const o = d.organization, c = d.counts;
  const manage = permitted(held, "platform.orgs.manage");
  const labels = permissionLabels(d.permission_catalogue);
  // The handover (A37) is the ownership panel's, below the list; it is shown to a caller who can read it.
  const handover = held.has("platform.orgs.manage") && held.has("platform.workspaces.view");
  const workspaceName = (id: string) => d.workspaces.items.find((w) => w.id === id)?.name;
  const space = (id: string) => { const name = workspaceName(id); return name ? <>{name}<div className="who2 mono">{id}</div></> : <span className="mono">{id}</span>; };
  return <div className="col gap16 org-detail">
    <p className="notice" role="note">{d.viewer.is_member
      ? tr("你在这个组织的角色：{role}。组织设置页按这个成员角色开放；这里是平台治理视图。", { role: orgRoleText(d.viewer.role) })
      : tr("你不是这个组织的成员：这里只显示治理元数据，不授予成员身份或内容访问；组织设置页对你不可用。")}</p>

    <section className="col gap8" aria-labelledby={ids.overview}>
      <div className="row gap8 wrap"><h3 className="secttl grow" id={ids.overview}>{tr("概况")}</h3>
        {d.viewer.is_member && <a className="btn sm" href={`/organizations/${encodeURIComponent(o.id)}`}>{tr("组织设置")}</a>}
        {onAct && !manage.disabled && lifecycleActions(o.status).map((kind) => <button type="button" key={kind}
          className="btn sm" disabled={kind === "recover" && !recoverable(o)}
          onClick={() => onAct(kind)}>{tr(LIFECYCLE_ACTION_LABELS[kind])}</button>)}</div>
      {manage.hint && <p className="muted small">{tr("你的平台角色可以查看组织，但不能在管理后台执行组织的生命周期操作。")}</p>}
      <dl className="facts">
        <dt>{tr("组织编号")}</dt><dd className="mono">{o.id}</dd>
        <dt>{tr("状态")}</dt><dd><span className={`badge ${o.status === "active" ? "ok" : "warn"}`}>{orgStatusText(o.status)}</span>
          {o.status === "deleted" && <span className="cell-note">{[
            o.deleted_at ? tr("删除时间：{time}", { time: fmtTime(o.deleted_at) }) : "",
            o.previous_status ? tr("删除前状态：{status}", { status: orgStatusText(o.previous_status) }) : "",
            o.recover_until ? (recoverable(o) ? tr("恢复期限：{time}", { time: fmtTime(o.recover_until) }) : tr("恢复期限已过（{time}），不能再恢复。", { time: fmtTime(o.recover_until) })) : "",
          ].filter(Boolean).join(" · ")}</span>}</dd>
        <dt>{tr("创建时间")}</dt><dd>{fmtTime(o.created_at)}</dd>
        <dt>{tr("组织头像")}</dt><dd>{o.has_avatar ? tr("已设置") : tr("未设置")}</dd>
        <dt>{tr("邀请策略")}</dt><dd>{label(INVITATION_POLICY_LABELS, d.settings.invitation_policy)}
          {d.settings.invitation_policy && INVITATION_POLICY_MEANING[d.settings.invitation_policy] && <span className="cell-note">{tr(INVITATION_POLICY_MEANING[d.settings.invitation_policy])}</span>}</dd>
        <dt>{tr("内容外发上限")}</dt><dd>{label(TRANSFER_POLICY_LABELS, d.settings.transfer_policy)}
          {d.settings.transfer_policy && TRANSFER_POLICY_MEANING[d.settings.transfer_policy] && <span className="cell-note">{tr(TRANSFER_POLICY_MEANING[d.settings.transfer_policy])}</span>}</dd>
      </dl>
      <div className="row gap12 wrap">
        <Stat name={tr("成员人数")} value={c.members} />
        <Stat name={tr("所有者人数")} value={c.owners} note={tr("其中可用 {count}", { count: c.usable_owners })} />
        <Stat name={tr("管理员人数")} value={c.admins} />
        <Stat name={tr("工作空间数")} value={c.workspaces} note={c.workspaces_frozen ? tr("其中已冻结 {count}", { count: c.workspaces_frozen }) : undefined} />
        <Stat name={tr("自定义角色")} value={c.custom_roles} />
        <Stat name={tr("未完成的成员导入")} value={c.pending_imports} note={c.pending_imports === null ? tr("不统计") : undefined} />
        <Stat name={tr("未完成的复制申请")} value={c.pending_copies} note={c.pending_copies === null ? tr("不统计") : undefined} />
      </div>
    </section>

    <section className="col gap12" aria-labelledby={ids.people}>
      <h3 className="secttl" id={ids.people}>{tr("所有者与管理员")}</h3>
      {c.usable_owners === 0 && o.status !== "deleted" && <p className="notice warn" role="note">{tr("这个组织没有可用的所有者：所有者的账户已停用或已删除。")}
        {handover && <> {tr("可以在本页「没有有效拥有者的组织」中把一位现有成员指定为拥有者。")}</>}</p>}
      <PeopleTable caption={tr("所有者（{count} 位）", { count: d.owners_total })} people={d.owners} total={d.owners_total} empty={tr("没有所有者。")} />
      <PeopleTable caption={tr("管理员（{count} 位）", { count: d.admins_total })} people={d.admins} total={d.admins_total} empty={tr("没有管理员。")} />
    </section>

    <WorkspacesSection orgId={o.id} first={d.workspaces} />

    <MembersSection orgId={o.id} held={held} onExplain={onExplain} />

    <section className="col gap8" aria-labelledby={ids.roles}>
      <h3 className="secttl" id={ids.roles}>{tr("自定义角色（{count} 个）", { count: d.custom_roles.length })}</h3>
      <p className="muted small">{tr("自定义角色把一组组织级权限分配给成员，不改变工作空间内的权限。")}</p>
      {!d.custom_roles.length ? <p className="muted small">{tr("还没有自定义角色。")}</p>
        : <ul className="org-detail-roles">{d.custom_roles.map((r) => <li key={r.id}>
          <div className="row gap8 wrap"><strong className="grow">{r.name}</strong><span className="muted small">{tr("已分配给 {count} 位成员", { count: r.assigned })}</span></div>
          {r.permissions.length ? <div className="row wrap gap4 org-detail-chips">
            {r.permissions.map((p) => <span key={p} className="badge" title={labels.description(p)}>{labels.label(p)}</span>)}</div>
            : <p className="muted small">{tr("这个角色没有权限。")}</p>}
        </li>)}</ul>}
    </section>

    <section className="col gap12" aria-labelledby={ids.pending}>
      <h3 className="secttl" id={ids.pending}>{tr("未完成的成员导入与复制申请")}</h3>
      <p className="muted small">{tr("只显示状态、行数和时间：导入的文件、行内容和项目名称不显示。")}</p>
      <div className="col gap8"><h4 className="small">{tr("成员导入")}</h4>
        {c.pending_imports === null ? <p className="muted small">{tr("这个部署没有成员导入记录，不统计。")}</p>
          : !d.pending.imports.length ? <p className="muted small">{tr("没有未完成的成员导入。")}</p>
          : <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={tr("未完成的成员导入")}>
            <thead><tr><th scope="col">{tr("导入编号")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("行数")}</th>
              <th scope="col">{tr("创建时间")}</th><th scope="col">{tr("发起人")}</th></tr></thead>
            <tbody>{d.pending.imports.map((i) => <tr key={i.id}>
              <th scope="row"><span className="mono">{i.id}</span></th>
              <td data-label={tr("状态")}>{label(IMPORT_STATE_LABELS, i.state)}</td>
              <td data-label={tr("行数")}>{i.state === "extracted" ? <span className="muted">{tr("生成预览后计数")}</span> : formatNumber(i.rows)}</td>
              <td data-label={tr("创建时间")}>{fmtTime(i.created_at)}</td>
              <td data-label={tr("发起人")}>{refName(i.created_by)}</td>
            </tr>)}</tbody></table></div>}
        {c.pending_imports !== null && c.pending_imports > d.pending.imports.length && <p className="muted small">{tr("共 {total} 项，这里显示最新的 {shown} 项。", { total: c.pending_imports, shown: d.pending.imports.length })}</p>}
      </div>
      <div className="col gap8"><h4 className="small">{tr("项目复制申请")}</h4>
        {c.pending_copies === null ? <p className="muted small">{tr("这个部署没有项目复制记录，不统计。")}</p>
          : !d.pending.copies.length ? <p className="muted small">{tr("没有未完成的复制申请。")}</p>
          : <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={tr("未完成的复制申请")}>
            <thead><tr><th scope="col">{tr("申请编号")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("来源空间")}</th>
              <th scope="col">{tr("目标空间")}</th><th scope="col">{tr("申请时间")}</th><th scope="col">{tr("当前步骤截止")}</th><th scope="col">{tr("申请人")}</th></tr></thead>
            <tbody>{d.pending.copies.map((p) => <tr key={p.id}>
              <th scope="row"><span className="mono">{p.id}</span></th>
              <td data-label={tr("状态")}>{label(COPY_STATUS_LABELS, p.status)}</td>
              <td data-label={tr("来源空间")}><div>{space(p.source_workspace_id)}</div></td>
              <td data-label={tr("目标空间")}><div>{space(p.target_workspace_id)}</div></td>
              <td data-label={tr("申请时间")}>{fmtTime(p.created_at)}</td>
              <td data-label={tr("当前步骤截止")}>{copyDeadline(p)}</td>
              <td data-label={tr("申请人")}>{refName(p.requested_by)}</td>
            </tr>)}</tbody></table></div>}
        {c.pending_copies !== null && c.pending_copies > d.pending.copies.length && <p className="muted small">{tr("共 {total} 项，这里显示最新的 {shown} 项。", { total: c.pending_copies, shown: d.pending.copies.length })}</p>}
      </div>
    </section>
  </div>;
}

/** The panel's content for each state of the read: loading, refused by role, gone, failed, loaded. */
export function OrganizationDetailView({ state, held, onRetry, onAct, onExplain }: {
  state: { loading: boolean; error: unknown; data: OrganizationDetail | null }; held: Held;
  onRetry: () => void; onAct?: (action: LifecycleAction) => void; onExplain: (member: OrgMember) => void;
}) {
  useI18n();
  if (state.error) {
    const problem = detailProblem(state.error);
    if (problem === "denied") return <p className="notice" role="note">{tr(ORG_DETAIL_DENIED)}</p>;
    if (problem === "missing") return <p className="notice" role="note">{tr(ORG_DETAIL_REFUSALS.no_organization)}</p>;
    return <div className="notice err" role="alert">{orgDetailErrText(state.error)} <button type="button" className="btn sm" onClick={onRetry}>{tr("重试")}</button></div>;
  }
  if (state.loading || !state.data) return <p role="status">{tr("正在读取组织详情…")}</p>;
  return <OrganizationDetailBody detail={state.data} held={held} onAct={onAct} onExplain={onExplain} />;
}

/** The panel itself. `onChanged` runs after a lifecycle change made here, so the list re-reads. */
export function OrganizationDetailDialog({ id, held, onClose, onChanged }: { id: string; held: Held; onClose: () => void; onChanged?: () => void }) {
  useI18n();
  const toast = useToast();
  // The language rides along for the permission labels (as the tenant roles page asks for them).
  const path = `/api/admin/organizations/${encodeURIComponent(id)}${qs({ lang: getLocale() })}`;
  const load = useLoad<OrganizationDetail>(path, (signal) => get<OrganizationDetail>(path, signal));
  const data = load.data?.organization.id === id ? load.data : null;
  const [action, setAction] = useState<LifecycleAction | null>(null);
  const [explain, setExplain] = useState<OrgMember | null>(null);
  const name = data?.organization.name;
  return <>
    <Modal title={<span className="org-detail-name">{name ? tr("组织详情：{name}", { name }) : tr("组织详情")}</span>} size="wide" onClose={onClose}
      footer={<><span className="grow muted small">{tr(ORG_DETAIL_NOTE)}</span>
        <button type="button" className="btn sm" disabled={load.loading} onClick={load.reload}>{tr("刷新")}</button>
        <button type="button" className="btn" onClick={onClose}>{tr("关闭")}</button></>}>
      <OrganizationDetailView state={{ loading: load.loading, error: load.error, data }} held={held} onRetry={load.reload}
        onAct={setAction} onExplain={setExplain} />
    </Modal>
    {/* A35, from here as from the list: the same preview, reason and re-authentication */}
    {action && data && <OrganizationLifecycleDialog org={data.organization} action={action} onClose={() => setAction(null)}
      onDone={() => { setAction(null); load.reload(); onChanged?.(); toast(tr("组织状态已更新"), "ok"); }} />}
    {explain && data && <MemberPermissionsDialog orgId={data.organization.id} member={explain} labels={permissionLabels(data.permission_catalogue)}
      onClose={() => setExplain(null)} />}
  </>;
}
