/**
 * The member-governance dialogs. Each states who is affected and what they lose before anything
 * happens; the dangerous ones (removing, demoting an admin or owner, making an owner, handing
 * over ownership) require a reason and re-authentication through ActionDialog.
 */
import { useId, useState } from "react";
import { patch, post } from "@synwevo/auth-client";
import { ActionDialog } from "../../components/ActionDialog";
import { ROLE_GOVERNS, ROLE_RANK, ROLES, roleLabel, type Role } from "../../lib/labels";
import type { AppInfo, Member, Removed, WorkspaceInfo } from "./types";
import { memberName } from "./types";
import type { BulkResponse } from "../../components/BulkResult";

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
    out.push("将成为所有者：可以管理这个空间里的所有成员（包括你）、邀请任意角色、移交所有权。所有者只能由其他所有者降级。");
    return out;
  }
  if (from === "owner") out.push("将不再是所有者：失去管理其他所有者与管理员、移交所有权的权限。");
  if ((from === "owner" || from === "admin") && rank(to) < rank("admin")) out.push("将失去邀请成员和管理成员的权限。");
  if (to === "viewer" && from !== "viewer") out.push("将不能再编辑；导出权限会随之关闭，需要时可以再单独开启。");
  if (rank(to) > rank(from)) out.push(`将获得「${roleLabel(to)}」的权限：${ROLE_GOVERNS[to]}。`);
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
  if (!r) return "已移除";
  return `已撤销 ${r.entitlements_revoked} 项应用使用资格、${r.tokens_revoked} 个令牌；`
    + `${r.sessions_moved} 个登录会话已移出该空间；取消了 ${r.invitations_cancelled} 个 TA 发出的邀请`;
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
    <ActionDialog title={`修改 ${memberName(m)} 的角色`} confirmLabel="修改角色" danger={dangerous}
      reason={dangerous ? "required" : "optional"} reauthScope="admin" ready={role !== m.role}
      impact={
        <>
          <b>{memberName(m)}</b>（{m.email}）在「{ws.name}」的角色将从「{roleLabel(m.role)}」改为「{roleLabel(role)}」。
          {effects.length > 0 && <ul>{effects.map((t) => <li key={t}>{t}</li>)}</ul>}
          {dangerous && <p className="small muted" style={{ margin: "6px 0 0" }}>这是需要写明理由并重新验证身份的操作。</p>}
        </>
      }
      run={(reason) => patch(`${base(wid)}/members/${encodeURIComponent(m.user_id)}`, { role, reason: reason || undefined })}
      onDone={onDone} onClose={onClose}>
      <div className="field">
        <label htmlFor={id}>新角色</label>
        <select id={id} className="input" value={role} onChange={(e) => setRole(e.target.value)}>
          {options.map((r) => <option key={r} value={r}>{roleLabel(r)}{r === m.role ? "（当前）" : ""}</option>)}
        </select>
        {frozen && <span className="tiny muted">空间已冻结：只能降低角色，不能提升。</span>}
        {callerRole === "admin" && <span className="tiny muted">管理员最多可以授予「管理员」；授予后你将不能再管理这位成员。</span>}
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
    <ActionDialog title={`修改 ${ms.length} 位成员的角色`} confirmLabel={`改为「${roleLabel(role)}」`} danger={dangerous}
      reason={dangerous ? "required" : "optional"} reauthScope="admin" ready={changing.length > 0}
      impact={
        <>
          将把以下 <b>{changing.length}</b> 位成员在「{ws.name}」的角色改为「{roleLabel(role)}」
          {ms.length - changing.length > 0 && <>（另有 {ms.length - changing.length} 位已经是该角色，不会变化）</>}：
          <Names ms={changing} />
          {role === "viewer" && <div>降为只读的成员将不能再编辑，导出权限随之关闭。</div>}
          {dangerous && <div>其中包含管理员或所有者的降级、或新的所有者：需要写明理由并重新验证身份。</div>}
          {frozen && raising.length > 0 && <div className="err-text">空间已冻结，其中 {raising.length} 项提升会被拒绝。</div>}
          <div className="small muted">每一项单独执行、单独报告结果；被拒绝的项不会改变。</div>
        </>
      }
      run={(reason) => post<BulkResponse>(`${base(wid)}/members/roles`, {
        changes: changing.map((m) => ({ user_id: m.user_id, role })), reason: reason || undefined,
      })}
      onDone={onDone} onClose={onClose}>
      <div className="field">
        <label htmlFor={id}>新角色</label>
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
  const appName = app?.name || "应用";
  return (
    <ActionDialog title={single ? "移除成员" : `移除 ${ms.length} 位成员`} confirmLabel={single ? "移除成员" : `移除 ${ms.length} 位成员`}
      danger reason="required" reauthScope="admin"
      reasonPlaceholder="例如：已离职 / 项目结束 / 账户交接"
      impact={
        <>
          {single
            ? <>将把 <b>{memberName(single)}</b>（{single.email}，{roleLabel(single.role)}）从「{ws.name}」移除。</>
            : <>将把以下 {ms.length} 位成员从「{ws.name}」移除：<Names ms={ms} /></>}
          立即生效：
          <ul>
            <li>失去这个空间里全部项目、模板与文件的访问；</li>
            <li>在这个空间使用 {appName} 的资格被撤销，已签发给应用的令牌全部失效；</li>
            <li>正停留在这个空间的登录会被移出该空间（账户本身不会被退出登录）；</li>
            <li>{single ? "TA" : "他们"}发出但尚未被接受的邀请会被取消。</li>
          </ul>
          账户本身不受影响。以后需要时可以重新邀请，重新加入后从头开始，不会恢复原来的角色和资格。
        </>
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
    <ActionDialog title={`撤销 ${app.name} 使用资格`} confirmLabel="撤销使用资格" danger reason="optional"
      reauthScope="admin"
      impact={
        <>
          {single
            ? <>将撤销 <b>{memberName(single)}</b>（{single.email}）在「{ws.name}」使用 {app.name} 的资格。</>
            : <>将撤销以下 {ms.length} 位成员在「{ws.name}」使用 {app.name} 的资格：<Names ms={ms} /></>}
          <ul>
            <li>立即不能再打开 {app.name}；</li>
            <li>已签发给 {app.name} 的令牌全部失效；</li>
            <li>成员身份和角色不变，之后可以再授予。</li>
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
    <ActionDialog title="移交所有权" confirmLabel="移交所有权" danger reason="required" reauthScope="admin"
      impact={
        <>
          <b>{memberName(m)}</b>（{m.email}，当前为{roleLabel(m.role)}）将成为「{ws.name}」的所有者，
          拥有与你相同的全部管理权限，包括管理你自己。
          {self === "owner"
            ? <div>你仍保留所有者身份：接收方也将拥有所有者身份。</div>
            : <>你将变为「{roleLabel(self)}」：<ul>{effects.map((t) => <li key={t}>{t.replace(/^将/, "你将")}</li>)}</ul></>}
          两项变化在同一个事务里完成，空间在任何时刻都有所有者。
        </>
      }
      run={(reason) => post<{ self_role: string }>(`${base(wid)}/ownership/transfer`,
        { to_user_id: m.user_id, demote_self_to: self === "owner" ? null : self, reason })}
      onDone={onDone} onClose={onClose}>
      <fieldset className="col gap4" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="small" style={{ color: "var(--text-2)", marginBottom: 4 }}>移交之后，我的角色</legend>
        {(["owner", "admin", "editor", "viewer"] as const).map((r) => (
          <label key={r} className="check">
            <input type="radio" name={name} value={r} checked={self === r} onChange={() => setSelf(r)} />
            {r === "owner" ? "仍是所有者（共同拥有）" : roleLabel(r)}
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
    <ActionDialog title="退出空间" confirmLabel="退出空间" danger reason="none" ready={!soleOwner}
      impact={
        <>
          你将离开「{wsName}」：
          <ul>
            <li>失去这个空间里全部内容的访问；</li>
            <li>你在这个空间的应用使用资格与令牌会被撤销；</li>
            <li>你发出但尚未被接受的邀请会被取消。</li>
          </ul>
          要回来，需要空间管理员重新邀请你。
          {soleOwner && <div className="notice warn mt8">你是这个空间唯一的所有者，不能直接退出。请先把所有权移交给另一位成员。</div>}
        </>
      }
      run={() => post(`${base(wid)}/leave`, {})}
      onDone={onDone} onClose={onClose} />
  );
}
