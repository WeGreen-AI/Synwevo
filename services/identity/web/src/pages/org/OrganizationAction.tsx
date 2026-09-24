import { useId, useState, type ReactNode } from "react";
import { ApiError, post, useSession } from "@synwevo/auth-client";
import { Modal, useI18n } from "@synwevo/ui";
import { FlowError, PasswordField, useAction } from "./AuthFlow";
export interface OrganizationActionSpec { title: string; impact: string; danger?: boolean; run: (reason: string) => Promise<unknown> }
export default function OrganizationAction({ spec, children, ready = true, onDone, onClose }: {
  spec: OrganizationActionSpec; children?: ReactNode; ready?: boolean; onDone: () => void; onClose: () => void;
}) {
  const { t } = useI18n(); const { me } = useSession(); const action = useAction(); const id = useId();
  const [reason, setReason] = useState(""); const [password, setPassword] = useState(""); const [reauth, setReauth] = useState(false);
  const [ended, setEnded] = useState(false);
  function submit(e: React.FormEvent) { e.preventDefault(); void action.run(async () => {
    if (!ready) return;
    if (Array.from(reason.trim()).length < 8) throw new Error("请填写至少 8 个字符的操作原因。");
    try {
      if (reauth) { await post("/api/auth/reauth", { password, scope: "admin" }); setPassword(""); setReauth(false); }
      await spec.run(reason.trim()); onDone();
    } catch (e) {
      if (e instanceof ApiError && e.code === "reauth_required") { setReauth(true); return; }
      if (e instanceof ApiError && e.status === 401 && e.code !== "bad_credentials") setEnded(true);
      setPassword(""); throw e;
    }
  }); }
  return <Modal title={spec.title} onClose={() => { if (!action.busy) onClose(); }} size="narrow" role="alertdialog" describedBy={`${id}-impact`}>
    <form className="flow-stack" onSubmit={submit} aria-busy={action.busy}>
      <p id={`${id}-impact`} className={spec.danger ? "impact danger" : "impact"}>{spec.impact}</p>
      <FlowError error={action.error} />
      <fieldset disabled={action.busy || ended} className="flow-options">{children}
        <div className="field"><label htmlFor={`${id}-reason`}>{t("操作原因")}</label><textarea id={`${id}-reason`} className="input" value={reason} onChange={e => setReason(e.target.value)} required maxLength={500} rows={3} autoFocus aria-describedby={`${id}-hint`} /><span id={`${id}-hint`} className="tiny muted">{t("8–500 个字符，会写入审计记录。")}</span></div>
        {reauth && <><p className="notice" role="status">{t("这一步需要重新验证身份，请输入当前口令。")}</p><input type="text" name="username" autoComplete="username" value={me?.email || ""} readOnly hidden /><PasswordField label={t("当前口令")} value={password} onChange={setPassword} fresh={false} autoFocus /></>}
      </fieldset>
      {ended ? <a className="btn primary" href={`/login?next=${encodeURIComponent(window.location.pathname)}`}>{t("重新登录")}</a> : <div className="org-actions"><button type="button" className="btn" onClick={onClose} disabled={action.busy}>{t("取消")}</button><button className={`btn ${spec.danger ? "danger" : "primary"}`} disabled={action.busy || !ready}>{t(action.busy ? "处理中…" : reauth ? "验证并确认" : "确认操作")}</button></div>}
    </form>
  </Modal>;
}
