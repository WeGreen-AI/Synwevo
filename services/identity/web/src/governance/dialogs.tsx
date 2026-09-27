import { t as tr } from "@synwevo/ui";
import "./messages";
/**
 * The member-governance dialogs. Each states who is affected and what they lose before anything
 * happens; the dangerous ones (removing, demoting an admin or owner, making an owner, handing
 * over ownership) require a reason and re-authentication through ActionDialog.
 */
import { useId, useState } from "react";
import { patch, post } from "@synwevo/auth-client";
import { ActionDialog } from "./ActionDialog";
import { ROLE_GOVERNS, ROLE_RANK, ROLES, roleLabel, type Role } from "./labels";
import type { AppInfo, Member, Removed, WorkspaceInfo } from "./types";
import { memberName } from "./types";
import type { BulkResponse } from "./BulkResult";

const base = (wid: string) => `/api/auth/workspaces/${encodeURIComponent(wid)}`;
const rank = (r: string) => ROLE_RANK[r as Role] ?? -1;

/** The server's own rule (members.py `_is_dangerous_change`): making an owner, or moving an
 *  admin or owner down. Mirrored only to ask for the reason up front; the server decides. */
export function isDangerousChange(from: string, to: string): boolean {
  return (to === "owner" && from !== "owner") || ((from === "admin" || from === "owner") && rank(to) < rank(from));
}

/** Roles this caller may hand out: never above their own. In a frozen workspace, never above
 *  the member's current role either — it sheds access but gains none. */
export function assignable(callerRole: string, currentRole: string, frozen: boolean): Role[] {
  return ROLES.filter((r) => rank(r) <= rank(callerRole) && (!frozen || rank(r) <= rank(currentRole)));
}

export function roleChangeEffects(from: string, to: string): string[] {
  const out: string[] = [];
  if (from === to) return out;
  if (to === "owner") {
    out.push(tr("将成为所有者：可以管理这个工作空间里的所有成员（包括你）、邀请任意角色、移交所有权。所有者只能由其他所有者降级。"));
    return out;
  }
  if (from === "owner") out.push(tr("将不再是所有者：失去管理其他所有者与管理员、移交所有权的权限。"));
  if ((from === "owner" || from === "admin") && rank(to) < rank("admin")) out.push(tr("将失去邀请成员和管理成员的权限。"));
  if (to === "viewer" && from !== "viewer") out.push(tr("将不能再编辑；导出权限会随之关闭，需要时可以再单独开启。"));
  if (rank(to) > rank(from)) out.push(tr("将获得「{p0}」的权限：{p1}。", {p0: roleLabel(to), p1: ROLE_GOVERNS[to]}));
  return out;
}

function Names({ ms }: { ms: Member[] }) {
  return (
    <ul className="names">
      {ms.map((m) => <li key={m.user_id}><b>{memberName(m)}</b>{m.display_name && <span className="muted">（{m.email}）</span>} · {roleLabel(m.role)}</li>)}
    </ul>
  );
}

export function removedSummary(r: Removed | undefined | null): string {
  if (!r) return tr("已移除");
  return tr("已撤销 {p0} 项应用使用资格、{p1} 个令牌；", {p0: r.entitlements_revoked, p1: r.tokens_revoked})
    + tr("{p0} 个登录已移出该工作空间；取消了 {p1} 个由其发出的邀请", {p0: r.sessions_moved, p1: r.invitations_cancelled});
}

/* ------------------------------------------------------------------ role */
export function RoleDialog({ wid, m, callerRole, ws, onDone, onClose }: {
  wid: string; m: Member; callerRole: string; ws: WorkspaceInfo;
  onDone: (r: { before: { role: string }; after: { role: string } }) => void; onClose: () => void;
}) {
  const frozen = ws.status !== "active";
  const options = assignable(callerRole, m.role, frozen);
  const [role, setRole] = useState<string>(options.find((r) => r !== m.role) || m.role);
  const id = useId();
  const dangerous = isDangerousChange(m.role, role);
  const effects = roleChangeEffects(m.role, role);
  return (
    <ActionDialog title={tr("修改 {p0} 的角色", {p0: memberName(m)})} confirmLabel={tr("修改角色")} danger={dangerous}
      reason={dangerous ? "required" : "optional"} reauthScope="admin" ready={role !== m.role}
      impact={
        <>
          {tr("{name}（{email}）在「{space}」的角色将从「{before}」改为「{after}」。", {name: memberName(m), email: m.email, space: ws.name, before: roleLabel(m.role), after: roleLabel(role)})}
          {effects.length > 0 && <ul>{effects.map((t) => <li key={t}>{t}</li>)}</ul>}
          {dangerous && <p className="small muted" style={{ margin: "6px 0 0" }}>{tr("这是需要写明理由并重新验证身份的操作。")}</p>}
        </>
      }
      run={(reason) => patch(`${base(wid)}/members/${encodeURIComponent(m.user_id)}`, { role, reason: reason || undefined })}
      onDone={onDone} onClose={onClose}>
      <div className="field">
        <label htmlFor={id}>{tr("新角色")}</label>
        <select id={id} className="input" value={role} onChange={(e) => setRole(e.target.value)}>
          {options.map((r) => <option key={r} value={r}>{roleLabel(r)}{r === m.role ? tr("（当前）") : ""}</option>)}
        </select>
        {frozen && <span className="tiny muted">{tr("工作空间已冻结：只能降低角色，不能提升。")}</span>}
        {callerRole === "admin" && <span className="tiny muted">{tr("管理员最多可以授予「管理员」；授予后你将不能再管理这位成员。")}</span>}
      </div>
    </ActionDialog>
  );
}

/* ------------------------------------------------------------------ bulk role */
export function BulkRoleDialog({ wid, ms, callerRole, ws, onDone, onClose }: {
  wid: string; ms: Member[]; callerRole: string; ws: WorkspaceInfo;
  onDone: (r: BulkResponse) => void; onClose: () => void;
}) {
  const frozen = ws.status !== "active";
  const options = ROLES.filter((r) => rank(r) <= rank(callerRole));
  const [role, setRole] = useState<string>("viewer");
  const id = useId();
  const changing = ms.filter((m) => m.role !== role);
  const dangerous = changing.some((m) => isDangerousChange(m.role, role));
  const raising = changing.filter((m) => rank(role) > rank(m.role));
  return (
    <ActionDialog title={tr("修改 {p0} 位成员的角色", {p0: ms.length})} confirmLabel={tr("改为「{p0}」", {p0: roleLabel(role)})} danger={dangerous}
      reason={dangerous ? "required" : "optional"} reauthScope="admin" ready={changing.length > 0}
      impact={
        <>{tr("将把以下 {count} 位成员在「{space}」的角色改为「{role}」。", {count: changing.length, space: ws.name, role: roleLabel(role)})}
          {ms.length - changing.length > 0 && <>{tr("（另有{p0}位已经是该角色，不会变化）", {p0: ms.length - changing.length})}</>}：
          <Names ms={changing} />
          {role === "viewer" && <div>{tr("降为查看者的成员将不能再编辑，导出权限随之关闭。")}</div>}
          {dangerous && <div>{tr("其中包含管理员或所有者的降级、或新的所有者：需要写明理由并重新验证身份。")}</div>}
          {frozen && raising.length > 0 && <div className="err-text">{tr("工作空间已冻结，其中 {p0} 项提升会被拒绝。", {p0: raising.length})}</div>}
          <div className="small muted">{tr("每一项单独执行、单独报告结果；被拒绝的项不会改变。")}</div>
        </>
      }
      run={(reason) => post<BulkResponse>(`${base(wid)}/members/roles`, {
        changes: changing.map((m) => ({ user_id: m.user_id, role })), reason: reason || undefined,
      })}
      onDone={onDone} onClose={onClose}>
      <div className="field">
        <label htmlFor={id}>{tr("新角色")}</label>
        <select id={id} className="input" value={role} onChange={(e) => setRole(e.target.value)}>
          {options.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
        </select>
      </div>
    </ActionDialog>
  );
}

/* ------------------------------------------------------------------ remove */
export function RemoveDialog({ wid, ms, ws, app, onDone, onClose }: {
  wid: string; ms: Member[]; ws: WorkspaceInfo; app: AppInfo | null;
  onDone: (r: { single?: Removed; bulk?: BulkResponse }) => void; onClose: () => void;
}) {
  const single = ms.length === 1 ? ms[0] : null;
  const appName = app?.name || tr("应用");
  return (
    <ActionDialog title={single ? tr("移除成员") : tr("移除 {p0} 位成员", {p0: ms.length})} confirmLabel={single ? tr("移除成员") : tr("移除 {p0} 位成员", {p0: ms.length})}
      danger reason="required" reauthScope="admin"
      reasonPlaceholder={tr("例如：已离职 / 项目结束 / 账户交接")}
      impact={
        <>
          {single
            ? <>{tr("将把{p0}（{p1}，{p2}）从「{p3}」移除。", {p0: memberName(single), p1: single.email, p2: roleLabel(single.role), p3: ws.name})}</>
            : <>{tr("将把以下 {count} 位成员从「{space}」移除：", {count: ms.length, space: ws.name})}<Names ms={ms} /></>}{tr("立即生效：")}<ul>
            <li>{tr("失去这个工作空间里全部项目、模板与文件的访问；")}</li>
            <li>{tr("在这个工作空间使用{p0}的资格被撤销，已签发给应用的令牌全部失效；", {p0: appName})}</li>
            <li>{tr("正在这个工作空间里的登录会被移出（账户不会退出登录）；")}</li>
            <li>{tr("这些成员发出但尚未被接受的邀请会被取消。")}</li>
          </ul>{tr("账户本身不受影响。以后可以重新邀请，但不会恢复原来的角色和资格。")}</>
      }
      run={async (reason) => single
        ? { single: (await post<{ removed: Removed }>(`${base(wid)}/members/${encodeURIComponent(single.user_id)}/remove`, { reason })).removed }
        : { bulk: await post<BulkResponse>(`${base(wid)}/members/remove`, { user_ids: ms.map((m) => m.user_id), reason }) }}
      onDone={onDone} onClose={onClose} />
  );
}

/* ------------------------------------------------------------------ entitlements */
export function RevokeEntitlementDialog({ wid, ms, ws, app, onDone, onClose }: {
  wid: string; ms: Member[]; ws: WorkspaceInfo; app: AppInfo;
  onDone: (r: { single?: { tokens_revoked: number }; bulk?: BulkResponse }) => void; onClose: () => void;
}) {
  const single = ms.length === 1 ? ms[0] : null;
  return (
    <ActionDialog title={tr("撤销 {p0} 使用资格", {p0: app.name})} confirmLabel={tr("撤销使用资格")} danger reason="optional"
      reauthScope="admin"
      impact={
        <>
          {single
            ? <>{tr("将撤销{p0}（{p1}）在「{p2}」使用{p3}的资格。", {p0: memberName(single), p1: single.email, p2: ws.name, p3: app.name})}</>
            : <>{tr("将撤销以下 {count} 位成员在「{space}」使用 {app} 的资格：", {count: ms.length, space: ws.name, app: app.name})}<Names ms={ms} /></>}
          <ul>
            <li>{tr("立即不能再打开{p0}；", {p0: app.name})}</li>
            <li>{tr("已签发给{p0}的令牌全部失效；", {p0: app.name})}</li>
            <li>{tr("成员身份和角色不变，之后可以再授予。")}</li>
          </ul>
        </>
      }
      run={async (reason) => single
        ? { single: await post<{ tokens_revoked: number }>(`${base(wid)}/members/${encodeURIComponent(single.user_id)}/entitlements`,
          { app_id: app.id, grant: false, reason: reason || undefined }) }
        : { bulk: await post<BulkResponse>(`${base(wid)}/members/entitlements`,
          { app_id: app.id, changes: ms.map((m) => ({ user_id: m.user_id, grant: false })), reason: reason || undefined }) }}
      onDone={onDone} onClose={onClose} />
  );
}

/* ------------------------------------------------------------------ ownership */
export function TransferDialog({ wid, m, ws, onDone, onClose }: {
  wid: string; m: Member; ws: WorkspaceInfo;
  onDone: (r: { self_role: string }) => void; onClose: () => void;
}) {
  const [self, setSelf] = useState<string>("admin");
  const name = useId();
  const effects = self === "owner" ? [] : roleChangeEffects("owner", self);
  return (
    <ActionDialog title={tr("移交所有权")} confirmLabel={tr("移交所有权")} danger reason="required" reauthScope="admin"
      impact={
        <>
          {tr("{name}（{email}，当前为{role}）将成为「{space}」的所有者，拥有与你相同的全部管理权限，包括管理你自己。", {name: memberName(m), email: m.email, role: roleLabel(m.role), space: ws.name})}{self === "owner"
            ? <div>{tr("你仍保留所有者身份：接收方也将拥有所有者身份。")}</div>
            : <>{tr("你的角色将变为「{role}」。", {role: roleLabel(self)})}<ul>{effects.map((t) => <li key={t}>{t}</li>)}</ul></>}{tr("工作空间始终有所有者。")}</>
      }
      run={(reason) => post<{ self_role: string }>(`${base(wid)}/ownership/transfer`,
        { to_user_id: m.user_id, demote_self_to: self === "owner" ? null : self, reason })}
      onDone={onDone} onClose={onClose}>
      <fieldset className="col gap4" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="small" style={{ color: "var(--text-2)", marginBottom: 4 }}>{tr("移交之后，我的角色")}</legend>
        {(["owner", "admin", "editor", "viewer"] as const).map((r) => (
          <label key={r} className="check">
            <input type="radio" name={name} value={r} checked={self === r} onChange={() => setSelf(r)} />
            {r === "owner" ? tr("仍是所有者（共同拥有）") : roleLabel(r)}
          </label>
        ))}
      </fieldset>
    </ActionDialog>
  );
}

/* ------------------------------------------------------------------ leave */
export function LeaveDialog({ wid, wsName, soleOwner, onDone, onClose }: {
  wid: string; wsName: string; soleOwner: boolean; onDone: () => void; onClose: () => void;
}) {
  return (
    <ActionDialog title={tr("退出工作空间")} confirmLabel={tr("退出工作空间")} danger reason="none" ready={!soleOwner}
      impact={
        <>{tr("你将离开「{space}」：", {space: wsName})}
          <ul>
            <li>{tr("失去这个工作空间里全部内容的访问；")}</li>
            <li>{tr("你在这个工作空间的应用使用资格与令牌会被撤销；")}</li>
            <li>{tr("你发出但尚未被接受的邀请会被取消。")}</li>
          </ul>{tr("要回来，需要工作空间管理员重新邀请你。")}{soleOwner && <div className="notice warn mt8">{tr("你是这个工作空间唯一的所有者，不能直接退出。请先把所有权移交给另一位成员。")}</div>}
        </>
      }
      run={() => post(`${base(wid)}/leave`, {})}
      onDone={onDone} onClose={onClose} />
  );
}
