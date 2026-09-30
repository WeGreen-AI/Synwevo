import { useEffect, useRef, useState } from "react";
import { post } from "@synwevo/auth-client";
import { t as tr, fmtTime } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { errText, ACCOUNT_STATUS } from "./labels";
import "./account-operations.messages";

type Plan = {
  target: { id: string; email: string; status: string };
  result_status: string; preview_token: string; expires_at: number;
  impact: { sessions: number; tokens: number; pending_authentication: number };
};
export type AccountStatusResult = { operation_id: string; status: string; sessions_revoked: number };

/** A plan is bound by the server to this account, action, origin and current version. */
export function AccountStatusDialog({ user, onClose, onDone }: {
  user: { id: string; email: string; status: string };
  onClose: () => void; onDone: (out: AccountStatusResult) => void;
}) {
  const suspend = user.status !== "suspended";
  const status = suspend ? "suspended" : "active";
  const title = tr(suspend ? "停用账户" : "恢复账户");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null);
  useEffect(() => {
    let live = true;
    setPlan(null); setError(""); setConfirmed(false); attempt.current = null;
    post<Plan>(`/api/admin/users/${encodeURIComponent(user.id)}/status-preview`, { status })
      .then((next) => { if (live) setPlan(next); })
      .catch((e) => { if (live) setError(errText(e)); });
    return () => { live = false; };
  }, [user.id, status, revision]);
  return <ActionDialog<AccountStatusResult> title={title} confirmLabel={title} reason="required" danger
    ready={!!plan && confirmed && !busy}
    impact={<><b>{user.email}</b><p>{tr("此操作仅影响这个账户；不会授予管理员任何文档或私信内容访问权。")}</p></>}
    onClose={onClose} onDone={onDone}
    run={async (reason) => {
      if (!plan) throw new Error(tr("请先查看操作影响。"));
      const fingerprint = JSON.stringify([plan.preview_token, reason]);
      if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() };
      setBusy(true);
      try {
        return await post<AccountStatusResult>(`/api/admin/users/${encodeURIComponent(user.id)}/status`, {
          status, reason, preview_token: plan.preview_token, idempotency_key: attempt.current.key,
        });
      } finally { setBusy(false); }
    }}>
    {error && <p className="notice err" role="alert">{error}</p>}
    {!plan && !error && <p role="status">{tr("正在核对账户与操作影响…")}</p>}
    {plan && <>
      <p>{tr("状态：{before} → {after}", {
        before: ACCOUNT_STATUS[plan.target.status] || plan.target.status,
        after: ACCOUNT_STATUS[plan.result_status] || plan.result_status,
      })}</p>
      {suspend ? <ul>
        <li>{tr("撤销 {sessions} 个会话和 {tokens} 个应用令牌。", plan.impact)}</li>
        <li>{tr("作废 {count} 个待完成的登录或恢复凭据。", { count: plan.impact.pending_authentication })}</li>
        <li>{tr("正在运行的任务和连接会在下一次权限检查时停止；已经生成的数据保留。")}</li>
      </ul> : <p>{tr(plan.result_status === "pending"
        ? "邮箱尚未验证，恢复后仍需本人完成邮箱验证；旧会话、令牌和找回链接不会恢复。"
        : "恢复后可以重新登录；旧会话、令牌和找回链接不会恢复。")}</p>}
      <p className="muted sm">{tr("预览有效至 {time}。账户变化后必须重新预览。", { time: fmtTime(plan.expires_at) })}</p>
      <label className="check"><input type="checkbox" checked={confirmed} disabled={busy}
        onChange={(e) => setConfirmed(e.target.checked)} />{tr("我已核对目标账户和上述影响。")}</label>
    </>}
    <button className="btn sm" type="button" disabled={busy} onClick={() => setRevision((n) => n + 1)}>{tr("重新预览影响")}</button>
  </ActionDialog>;
}
