import { useEffect, useState } from "react";
import { patch, post, safeNext, setWorkspaceContext, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { AuthFrame, FlowError, Loading, PasswordField, clearInvitation, checkPassword, readIntent, saveIntent, signedInDestination, useAction, useLinkToken } from "./org/AuthFlow";
import type { InvitationPreview } from "./Invite";

export default function Register() {
  const inviteToken = useLinkToken("invite_token", readIntent().invite_token);
  return <Registration key={inviteToken} inviteToken={inviteToken} />;
}
function Registration({ inviteToken }: { inviteToken: string }) {
  const { t, locale } = useI18n(); const { state, me, error, refresh } = useSession(); const action = useAction();
  const [joined, setJoined] = useState(false);
  const [preview, setPreview] = useState<InvitationPreview | null>(null); const [loadError, setLoadError] = useState<unknown>(null);
  const [email, setEmail] = useState(""); const [name, setName] = useState(""); const [password, setPassword] = useState("");
  const [intent, setIntent] = useState<"personal" | "organization">(() => new URLSearchParams(window.location.search).get("intent") === "organization" || readIntent().intent === "organization" ? "organization" : "personal");
  useEffect(() => {
    if (!inviteToken) return;
    const abort = new AbortController();
    post<InvitationPreview>("/api/auth/invitations/preview", { token: inviteToken }, abort.signal)
      .then(p => { setPreview(p); setEmail(p.email); saveIntent({ invite_token: inviteToken }); }).catch(e => { if (!abort.signal.aborted) setLoadError(e); });
    return () => abort.abort();
  }, [inviteToken]);
  useEffect(() => { if (me && !action.busy) window.location.assign(signedInDestination(me, "/onboarding")); }, [me, action.busy]);
  function submit(e: React.FormEvent) {
    e.preventDefault(); void action.run(async () => {
      checkPassword(password, email, name);
      const next = safeNext(new URLSearchParams(window.location.search).get("next") || readIntent().next || "/onboarding");
      saveIntent({ email: email.trim(), intent, invite_token: inviteToken || undefined, next });
      if (preview) {
        const out = await post<{ workspace_id?: string }>("/api/auth/invitations/accept", { token: inviteToken, password, display_name: name.trim(), locale });
        // The invitation itself proves the exact recipient address; do not create an unrelated personal space.
        setJoined(true); setPassword(""); saveIntent({ invite_token: undefined });
        if (out.workspace_id) setWorkspaceContext(out.workspace_id);
        await patch("/api/auth/profile", { locale });
        window.location.assign("/onboarding"); return;
      }
      const result = await post<{ status: "verification_pending"; delivery: "requested" | "unavailable" }>("/api/auth/register", { email: email.trim(), password, display_name: name.trim(), locale, intent, ...(inviteToken ? { invite_token: inviteToken } : {}) });
      setPassword("");
      window.location.assign(`/verify-email${result.delivery === "unavailable" ? "?delivery=unavailable" : ""}`);
    });
  }
  return <AuthFrame title={t(preview ? "接受邀请，创建账户" : "创建你的账户")} description={t(preview ? "此邀请将验证受邀邮箱，并加入指定工作空间。请确认后提交。" : "先验证邮箱，再为个人工作或团队协作准备空间。")}>
    {joined ? <><FlowError error={action.error} /><p role="status">{t("已加入工作空间。")}</p><a className="btn primary" href="/onboarding">{t("继续")}</a></> : me ? <Loading /> : error ? <><FlowError error={error} /><button type="button" className="btn" onClick={() => void action.run(refresh)} disabled={action.busy}>{t("重试")}</button></> : !state ? <Loading />
      : loadError ? <><FlowError error={loadError} /><a href="/invite" className="btn">{t("返回邀请")}</a><a href="/register" onClick={clearInvitation}>{t("不接受此邀请，返回注册")}</a></>
      : inviteToken && !preview ? <Loading />
      : (inviteToken && preview?.state !== "pending") || (!inviteToken && state.registration !== "open") ? <><p className="notice">{t(inviteToken ? "邀请链接无效，请使用最新的邀请邮件。" : "此服务暂未开放注册。")}</p><a className="btn" href="/login">{t("返回登录")}</a></>
      : preview && !preview.needs_account ? <><p>{t("请使用受邀邮箱登录，然后回来确认加入。")}</p><a className="btn primary" href="/login?next=%2Finvite">{t("登录并继续")}</a></> : <form onSubmit={submit} aria-busy={action.busy}>
        <FlowError error={action.error} />
        {preview && <p className="notice">{t("你正在接受「{workspace}」的邀请。", { workspace: preview.workspace || t("工作空间") })}</p>}
        <div className="field"><label htmlFor="register-name">{t(preview ? "显示名称（可选）" : "显示名称")}</label><input id="register-name" className="input" value={name} onChange={e => setName(e.target.value)} autoComplete="name" maxLength={100} required={!preview} autoFocus /></div>
        <div className="field"><label htmlFor="register-email">{t("邮箱")}</label><input id="register-email" className="input" value={email} onChange={e => setEmail(e.target.value)} type="email" autoComplete="username" inputMode="email" autoCapitalize="off" spellCheck={false} readOnly={!!preview} required aria-describedby={preview ? "invite-email-note" : undefined} />{preview && <span id="invite-email-note" className="tiny muted">{t("邮箱由邀请决定，无法在这里修改。")}</span>}</div>
        <PasswordField value={password} onChange={setPassword} />
        {!preview && <fieldset className="flow-options"><legend>{t("你希望如何开始？")}</legend>
          <label className="flow-option"><input type="radio" name="intent" value="personal" checked={intent === "personal"} onChange={() => setIntent("personal")} /><span>{t("个人使用")}<small>{t("为自己的文档和项目建立空间。")}</small></span></label>
          <label className="flow-option"><input type="radio" name="intent" value="organization" checked={intent === "organization"} onChange={() => setIntent("organization")} /><span>{t("团队协作")}<small>{t("创建组织，再邀请团队成员。")}</small></span></label>
        </fieldset>}
        <button className="btn primary" disabled={action.busy}>{t(action.busy ? "提交中…" : preview ? "创建账户并接受邀请" : "创建账户并验证邮箱")}</button>
        <p className="authfoot">{t("已有账户？")} <a href="/login" onClick={() => saveIntent({ invite_token: inviteToken || undefined })}>{t("登录")}</a></p>
      </form>}
  </AuthFrame>;
}
