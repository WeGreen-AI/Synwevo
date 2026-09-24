import { useEffect, useState } from "react";
import { get, post, setWorkspaceContext, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { AuthFrame, FlowError, Loading, needsVerification, portalWorkspaceUrl, readIntent, useAction } from "./org/AuthFlow";
import OrganizationCreate from "./org/OrganizationCreate";
import { roleSource } from "./org/types";
interface OnboardingState {
  completed: boolean; intent?: "personal" | "organization";
  workspaces: { id: string; name: string; kind: string; role: string }[];
  pending_invitations: { id: string; workspace_id: string; workspace_name: string; role: string; expires_at: number }[];
}
export default function Onboarding() {
  const { t, locale } = useI18n(); const { me, state, refresh, error: sessionError } = useSession(); const action = useAction();
  const [data, setData] = useState<OnboardingState | null>(null); const [error, setError] = useState<unknown>(null); const [retry, setRetry] = useState(0);
  const [choice, setChoice] = useState<"personal" | "organization" | null>(() => readIntent().intent || null);
  useEffect(() => {
    if (!me || needsVerification(me)) return;
    const abort = new AbortController(); setError(null);
    get<OnboardingState>("/api/auth/onboarding", abort.signal).then(d => { setData(d); setChoice(v => v || d.intent || null); }).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [me?.id, retry, needsVerification(me)]);
  async function enter(id: string) {
    const destination = portalWorkspaceUrl(state?.portal_url, id, locale);
    await post("/api/auth/workspace", { workspace_id: id });
    setWorkspaceContext(id);
    window.location.assign(destination);
  }
  return <AuthFrame wide title={t(data?.completed ? "你的工作空间已准备好" : "从这里开始")} description={t("创建个人空间、组建团队，或接受已有团队的邀请。")}>
    <div className="org-actions"><a href="/account">{t("账户设置")}</a><a href="/organizations">{t("我的组织")}</a></div>
    <FlowError error={sessionError || error || action.error} />
    {needsVerification(me) ? <a className="btn primary" href="/verify-email">{t("先验证邮箱")}</a> : me === undefined ? <Loading /> : !me ? <a className="btn primary" href="/login?next=%2Fonboarding">{t("登录并继续")}</a>
      : error || sessionError ? <button type="button" className="btn" onClick={() => { setRetry(v => v + 1); void refresh(); }}>{t("重试")}</button> : !data ? <Loading /> : <>
        {data.workspaces.length > 0 && <section className="org-section"><h2>{t("可用工作空间")}</h2><div className="org-grid">{data.workspaces.map(w => <article className="org-card" key={w.id}><h3>{w.name}</h3><p>{t(w.kind === "personal" ? "个人空间" : "团队空间")} · {t(roleSource(w.role))}</p><button type="button" className="btn" disabled={action.busy} onClick={() => void action.run(() => enter(w.id))}>{t("进入工作空间")}</button></article>)}</div></section>}
        {data.pending_invitations.length > 0 && <section className="org-section"><h2>{t("等待你接受的邀请")}</h2><p className="tiny muted">{t("请打开对应邀请邮件中的链接，核对信息并确认加入。")}</p><ul className="org-list">{data.pending_invitations.map(i => <li key={i.id}><span>{i.workspace_name}</span><span className="muted">{t(roleSource(i.role))}</span></li>)}</ul>{readIntent().invite_token && <a className="btn" href="/invite">{t("查看当前邀请")}</a>}</section>}
        <section className="org-section"><h2>{t("准备一个新空间")}</h2><div className="org-grid">
          <article className="org-card"><h3>{t("个人使用")}</h3><p>{t("拥有自己的工作空间，整理文档与项目。已有个人空间时会继续使用它。")}</p><button type="button" className="btn" disabled={action.busy} onClick={() => void action.run(async () => { const out = await post<{ workspace: { id: string } }>("/api/auth/onboarding/personal", {}); await enter(out.workspace.id); })}>{t(action.busy ? "处理中…" : "使用个人空间")}</button></article>
          <article className="org-card"><h3>{t("组建团队")}</h3><p>{t("创建组织与首个工作空间，按角色邀请成员一起工作。")}</p><button type="button" className="btn" aria-expanded={choice === "organization"} aria-controls="onboarding-create" onClick={() => setChoice(choice === "organization" ? null : "organization")}>{t(choice === "organization" ? "收起创建表单" : "创建组织")}</button></article>
        </div></section>
        {choice === "organization" && <section id="onboarding-create" className="org-section"><h2>{t("创建组织")}</h2><OrganizationCreate onCreated={org => { window.location.assign(`/organizations/${encodeURIComponent(org.id)}`); }} /></section>}
      </>}
  </AuthFrame>;
}
