import { useEffect, useState } from "react";
import { post, setWorkspaceContext, useSession } from "@synwevo/auth-client";
import { Modal, useI18n } from "@synwevo/ui";
import { AuthFrame, FlowError, Loading, clearInvitation, needsVerification, readIntent, saveIntent, useAction, useLinkToken } from "./org/AuthFlow";
export interface InvitationPreview {
  state: "pending" | "accepted" | "revoked" | "expired" | "declined"; email: string; role: string; role_label: string;
  workspace: string | null; inviter: string | null; expires_at: number; needs_account: boolean; signed_in_as?: string;
}
const DEAD = {
  declined: ["邀请已拒绝", "你没有加入这个工作空间。若改变主意，请让邀请人重新发送邀请。"],
  accepted: ["这个邀请已经用过了", "如果你已经加入，请直接登录查看工作空间。"],
  revoked: ["这个邀请已被撤销", "邀请人取消了这次邀请。请使用最新的邀请邮件。"],
  expired: ["这个邀请已过期", "邀请链接有有效期，请让邀请人重新发送。"],
};
export default function Invite() {
  const token = useLinkToken("token", readIntent().invite_token);
  return <Invitation key={token} token={token} />;
}
function Invitation({ token }: { token: string }) {
  const { t, locale } = useI18n(); const { me } = useSession(); const action = useAction();
  const [preview, setPreview] = useState<InvitationPreview | null>(null); const [error, setError] = useState<unknown>(null); const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!token) { setError(new Error("这个链接缺少邀请令牌，请使用邮件里的完整链接。")); return; }
    saveIntent({ invite_token: token }); const abort = new AbortController(); setError(null);
    post<InvitationPreview>("/api/auth/invitations/preview", { token }, abort.signal)
      .then(setPreview).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [token, retry]);
  const [confirmDecline, setConfirmDecline] = useState(false);
  const wrong = preview?.signed_in_as && preview.signed_in_as.toLowerCase() !== preview.email.toLowerCase();
  const dead = preview && preview.state !== "pending" ? DEAD[preview.state] : null;
  const role = preview?.role === "owner" ? "所有者" : preview?.role === "admin" ? "管理员" : preview?.role === "editor" ? "编辑者" : preview?.role === "viewer" ? "查看者" : "成员";
  async function accept() { await action.run(async () => {
    const out = await post<{ workspace_id?: string }>("/api/auth/invitations/accept", { token });
    if (out.workspace_id) setWorkspaceContext(out.workspace_id);
    clearInvitation(); window.location.assign("/onboarding");
  }); }
  async function decline() { await action.run(async () => {
    await post("/api/auth/invitations/decline", { token });
    clearInvitation(); setConfirmDecline(false); setPreview(p => p ? { ...p, state: "declined" } : p);
  }); }
  return <AuthFrame title={t(error ? "邀请链接无法打开" : dead ? dead[0] : "工作空间邀请")} description={t(dead ? dead[1] : "核对邀请信息后再决定是否加入；打开链接不会自动接受。")}>
    <FlowError error={error || (!confirmDecline && action.error)} />
    {error ? <><button type="button" className="btn" onClick={() => setRetry(v => v + 1)}>{t("重试")}</button><a className="action-link" href="/login" onClick={clearInvitation}>{t("前往登录")}</a></>
      : !preview ? <Loading /> : dead ? <a className="btn primary" href="/login" onClick={clearInvitation}>{t("前往登录")}</a> : <>
        <dl className="flow-facts"><dt>{t("工作空间")}</dt><dd>{preview.workspace || t("工作空间")}</dd><dt>{t("邀请邮箱")}</dt><dd>{preview.email}</dd>
          <dt>{t("你的角色")}</dt><dd>{t(role)}</dd>{preview.inviter && <><dt>{t("邀请人")}</dt><dd>{preview.inviter}</dd></>}
          <dt>{t("有效期至")}</dt><dd>{new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(preview.expires_at * 1000)}</dd></dl>
        {wrong ? <><p className="notice warn">{t("你当前登录的是 {current}，这个邀请是发给 {email} 的。", { current: preview.signed_in_as!, email: preview.email })}</p><button type="button" className="btn" disabled={action.busy} onClick={() => void action.run(async () => { await post("/api/auth/logout", {}); window.location.reload(); })}>{t("退出当前账户")}</button></>
          : preview.needs_account ? <><p className="tiny muted">{t("创建账户后直接加入；受邀邮箱无需再验证。")}</p><a className="btn primary" href={`/register#invite_token=${encodeURIComponent(token)}`}>{t("创建账户并继续")}</a><a className="authfoot" href="/login?next=%2Finvite">{t("已有账户？登录并继续")}</a></>
          : !preview.signed_in_as ? <><p className="tiny muted">{t("请使用受邀邮箱登录，然后回来确认加入。")}</p><a className="btn primary" href="/login?next=%2Finvite">{t("登录并继续")}</a></>
          : needsVerification(me) ? <a className="btn primary" href="/verify-email">{t("先验证邮箱")}</a>
          : <><button type="button" className="btn primary" disabled={action.busy} onClick={() => void accept()}>{t(action.busy ? "加入中…" : "接受邀请并加入")}</button><button type="button" className="btn ghost" disabled={action.busy} onClick={() => setConfirmDecline(true)}>{t("拒绝邀请")}</button></>}
      </>}
    {confirmDecline && <Modal title={t("拒绝邀请")} role="alertdialog" describedBy="decline-impact" onClose={() => { if (!action.busy) setConfirmDecline(false); }}>
      <div className="flow-stack"><p id="decline-impact">{t("拒绝后，此邀请链接将失效，你不会加入「{workspace}」。", { workspace: preview?.workspace || t("工作空间") })}</p>
        <FlowError error={action.error} /><div className="org-actions"><button type="button" className="btn" disabled={action.busy} onClick={() => setConfirmDecline(false)}>{t("保留邀请")}</button><button type="button" className="btn danger" disabled={action.busy} onClick={() => void decline()}>{t(action.busy ? "处理中…" : "确认拒绝邀请")}</button></div>
      </div>
    </Modal>}
  </AuthFrame>;
}
